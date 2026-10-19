import json
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

import httpx
from fastapi import UploadFile
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import Session

from config import ConfigurationError
from database import Base
from models.document_chunk import DocumentChunk
from models.product import Product, ProductDocument
from models.user import Users
from rag.embeddings import QwenEmbeddings
from rag.retriever import index_document, retrieve, upload_and_index_document
from rag.vector_store import SearchResult
from services.exceptions import AuthenticationError, InvalidInputError, NotFoundError, ServiceUnavailableError


class RetrieverTest(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory(prefix="rag-retriever-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        upload_dir = patch("services.product.UPLOAD_DIR", self.root)
        upload_dir.start()
        self.addCleanup(upload_dir.stop)
        tokenizer = patch("config.EMBEDDING_TOKENIZER_PATH", "")
        tokenizer.start()
        self.addCleanup(tokenizer.stop)
        self.engine = create_engine("sqlite://")
        self.addCleanup(self.engine.dispose)

        @event.listens_for(self.engine, "connect")
        def enable_foreign_keys(connection, _):
            connection.execute("PRAGMA foreign_keys=ON")

        Base.metadata.create_all(self.engine)
        self.db = Session(self.engine)
        self.addCleanup(self.db.close)
        self.db.add_all([Users(id=1, username="first"), Users(id=2, username="second")])
        self.db.flush()
        self.db.add_all([Product(id=1, user_id=1, name="a"), Product(id=2, user_id=1, name="b"),
                         Product(id=3, user_id=2, name="private")])
        self.db.commit()
        self.db.info["user_id"] = 1
        self.requests = []
        self.remote_status = 200
        self.on_request = None

        def handler(request):
            self.assertFalse(self.db.in_transaction(), "Embedding 期间不能持有 Session 事务")
            self.requests.append(request)
            if self.on_request:
                self.on_request()
            body = json.loads(request.content)
            return httpx.Response(self.remote_status, json={"model": body["model"], "data": [
                {"index": i, "embedding": [1.0] + [0.0] * (body["dimensions"] - 1)}
                for i, _ in enumerate(body["input"])]})

        self.http = httpx.Client(transport=httpx.MockTransport(handler))
        self.addCleanup(self.http.close)
        self.embedder = QwenEmbeddings(api_key="test-key", model="text-embedding-v3", dimensions=64,
            max_retries=0, base_url="https://embedding.example/v1", http_client=self.http)

    def upload(self, text="Product facts for commuting.", **options):
        upload = UploadFile(file=BytesIO(text.encode("utf-8")), filename="manual.txt")
        return upload_and_index_document(self.db, 1, upload, embedder=self.embedder, **options)

    def test_upload_index_reindex_and_status(self):
        result = self.upload()
        self.assertEqual(result.status, "indexed")
        self.assertEqual(result.chunk_count, 1)
        document = self.db.get(ProductDocument, result.document_id)
        self.assertEqual(document.status, "indexed")
        self.assertTrue((self.root / "1" / "1").is_dir())
        second = index_document(self.db, 1, result.document_id, embedder=self.embedder)
        self.assertEqual(result.document_hash, second.document_hash)
        self.assertEqual(len(list(self.db.scalars(select(DocumentChunk)))), 1)
        self.assertEqual(len(self.requests), 2)
        self.assertFalse(self.http.is_closed, "调用方提供的客户端应由调用方关闭")

    def test_failed_first_index_keeps_upload_and_can_retry(self):
        self.remote_status = 401
        with self.assertRaises(ServiceUnavailableError) as caught:
            self.upload()
        document = self.db.scalar(select(ProductDocument))
        self.assertEqual(document.status, "index_failed")
        self.assertIn(str(document.id), str(caught.exception))
        self.assertIn("/index", str(caught.exception))
        self.assertEqual(len(list(self.root.rglob("*.txt"))), 1)
        self.assertEqual(list(self.db.scalars(select(DocumentChunk))), [])
        self.remote_status = 200
        result = index_document(self.db, 1, document.id, embedder=self.embedder)
        self.assertEqual(result.status, "indexed")
        self.assertEqual(len(list(self.db.scalars(select(ProductDocument)))), 1)

    def test_failed_reindex_keeps_existing_vectors_and_ready_status(self):
        result = self.upload()
        original = self.db.scalar(select(DocumentChunk)).chunk_id
        self.remote_status = 500
        with self.assertRaises(ServiceUnavailableError):
            index_document(self.db, 1, result.document_id, embedder=self.embedder)
        self.assertEqual(self.db.scalar(select(DocumentChunk)).chunk_id, original)
        self.assertEqual(self.db.get(ProductDocument, result.document_id).status, "indexed")

    def test_parse_and_configuration_failure_are_recoverable(self):
        with self.assertRaises(InvalidInputError):
            self.upload(" \n\t ")
        document = self.db.scalar(select(ProductDocument))
        self.assertEqual(document.status, "index_failed")
        self.assertEqual(self.requests, [])
        with patch("rag.retriever.QwenEmbeddings", side_effect=ConfigurationError("请配置向量 Key")):
            with self.assertRaises(ServiceUnavailableError):
                upload_and_index_document(self.db, 1, UploadFile(file=BytesIO(b"valid"), filename="valid.txt"))
        self.assertTrue(all(d.status == "index_failed" for d in self.db.scalars(select(ProductDocument))))

    def test_ownership_checks_happen_before_remote_requests(self):
        result = self.upload()
        self.requests.clear()
        for operation in [lambda: index_document(self.db, 2, result.document_id, embedder=self.embedder),
                          lambda: retrieve(self.db, 3, "secret", embedder=self.embedder),
                          lambda: retrieve(self.db, 2, "wrong doc", document_id=result.document_id, embedder=self.embedder)]:
            with self.assertRaises(NotFoundError):
                operation()
        self.db.info["user_id"] = 2
        with self.assertRaises(NotFoundError):
            index_document(self.db, 1, result.document_id, embedder=self.embedder)
        self.db.info.clear()
        with self.assertRaises(AuthenticationError):
            retrieve(self.db, 1, "facts", embedder=self.embedder)
        self.assertEqual(self.requests, [])

    def test_invalid_options_do_not_upload_or_call_model(self):
        for options in [{"chunk_size": 0}, {"chunk_size": 2001}, {"chunk_overlap": 800}, {"chunk_overlap": -1}]:
            with self.assertRaises(InvalidInputError):
                self.upload(**options)
        for query, options in [(" ", {}), ("x" * 2001, {}), ("x", {"top_k": 0}),
                               ("x", {"max_context_chars": 255}), ("x", {"min_similarity": float("nan")})]:
            with self.assertRaises(InvalidInputError):
                retrieve(self.db, 1, query, embedder=self.embedder, **options)
        self.assertEqual(self.requests, [])
        self.assertEqual(list(self.root.rglob("*.txt")), [])

    def test_empty_knowledge_base_avoids_model_calls(self):
        with patch("rag.retriever.QwenEmbeddings", side_effect=AssertionError("must not construct client")):
            result = retrieve(self.db, 1, "facts")
        self.assertEqual(result.context, "")
        self.assertEqual(result.sources, [])

    def test_query_context_budget_citations_and_model_consistency(self):
        indexed = self.upload("long product text " * 80)
        self.requests.clear()
        metadata = {"user_id": 1, "product_id": 1, "start_char": 0, "end_char": 1000}
        hit = SearchResult("a" * 64, "t" * 1000, 1, 1, indexed.document_id, "manual.txt", 0, 0.99, metadata)
        with patch("rag.retriever.search_vectors", return_value=[hit]) as search:
            result = retrieve(self.db, 1, "  commuting  ", top_k=2, max_context_chars=256, embedder=self.embedder)
        self.assertEqual(result.query, "commuting")
        self.assertEqual(len(result.context), 256)
        self.assertTrue(result.sources[0].truncated)
        self.assertEqual(result.sources[0].citation, "[1]")
        self.assertIn(result.sources[0].text, result.context)
        self.assertEqual(result.sources[0].filename, "manual.txt")
        self.assertEqual(search.call_args.kwargs["model"], self.embedder.model)
        self.assertEqual(search.call_args.kwargs["dimensions"], 64)
        self.assertEqual(json.loads(self.requests[0].content)["input"], ["commuting"])

    def test_missing_model_index_and_no_matches_return_empty(self):
        self.upload()
        with patch("config.EMBEDDING_MODEL", "text-embedding-v4"), patch("rag.retriever.QwenEmbeddings") as factory:
            result = retrieve(self.db, 1, "facts")
            factory.assert_not_called()
        self.assertEqual(result.sources, [])
        with patch("rag.retriever.search_vectors", return_value=[]):
            result = retrieve(self.db, 1, "facts", embedder=self.embedder)
        self.assertEqual(result.context, "")

    def test_mismatched_query_client_fails_before_remote_request(self):
        self.upload()
        self.requests.clear()
        for model, dimensions in [("text-embedding-v4", 64), ("text-embedding-v3", 128)]:
            other = QwenEmbeddings(api_key="test-key", model=model, dimensions=dimensions,
                base_url="https://embedding.example/v1", http_client=self.http)
            with patch("config.EMBEDDING_MODEL", "text-embedding-v3"), \
                    patch("config.EMBEDDING_DIMENSIONS", 64), \
                    patch("rag.retriever.QwenEmbeddings", return_value=other):
                with self.assertRaises(ServiceUnavailableError) as caught:
                    retrieve(self.db, 1, "facts")
            self.assertIn("不一致", str(caught.exception))
        self.assertEqual(self.requests, [])

    def test_changed_profile_is_shared_by_default_indexing_and_query_clients(self):
        with patch("config.EMBEDDING_MODEL", "text-embedding-v4"), patch("config.EMBEDDING_DIMENSIONS", 128):
            client = QwenEmbeddings(api_key="test-key", base_url="https://embedding.example/v1", http_client=self.http)
            with patch("rag.retriever.QwenEmbeddings", return_value=client):
                indexed = upload_and_index_document(self.db, 1,
                    UploadFile(file=BytesIO(b"Product facts"), filename="profile.txt"))
                hit = SearchResult("b" * 64, "Product facts", 1, 1, indexed.document_id,
                                   "profile.txt", 0, 1.0, {})
                with patch("rag.retriever.search_vectors", return_value=[hit]) as search:
                    result = retrieve(self.db, 1, "facts")
        row = self.db.scalar(select(DocumentChunk))
        self.assertEqual((row.embedding_model, row.embedding_dimensions), ("text-embedding-v4", 128))
        self.assertEqual((result.model, result.dimensions), (indexed.model, indexed.dimensions))
        self.assertEqual(search.call_args.kwargs["model"], indexed.model)
        self.assertEqual(search.call_args.kwargs["dimensions"], indexed.dimensions)
        self.assertTrue(all(json.loads(r.content)["model"] == "text-embedding-v4" for r in self.requests))
        self.assertTrue(all(json.loads(r.content)["dimensions"] == 128 for r in self.requests))

    def test_document_deleted_during_embedding_cannot_be_recreated(self):
        def delete_document():
            document = self.db.scalar(select(ProductDocument))
            self.db.delete(document)
            self.db.commit()

        self.on_request = delete_document
        with self.assertRaises(NotFoundError):
            self.upload()
        self.assertEqual(list(self.db.scalars(select(DocumentChunk))), [])


if __name__ == "__main__":
    unittest.main()
