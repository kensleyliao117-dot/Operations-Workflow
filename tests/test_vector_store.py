"""离线验证写入、事务、归属与 PostgreSQL 查询条件，不冒充真实向量检索。"""
from dataclasses import replace
import unittest
from unittest.mock import Mock, patch

from sqlalchemy import create_engine, event, select
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Session

from database import Base
from models.document_chunk import DocumentChunk
from models.product import Product, ProductDocument
from models.user import Users
from rag.embeddings import EmbeddedChunk
from rag.text_splitter import split_text
from rag.vector_store import delete_document_vectors, save_document_vectors, search_vectors
from services.exceptions import AuthenticationError, InvalidInputError, NotFoundError, ServiceUnavailableError


class VectorStoreTest(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite://")

        @event.listens_for(self.engine, "connect")
        def foreign_keys(connection, _):
            connection.execute("PRAGMA foreign_keys=ON")

        Base.metadata.create_all(self.engine)
        self.db = Session(self.engine)
        self.addCleanup(self.engine.dispose)
        self.addCleanup(self.db.close)
        self.db.add_all([Users(id=1, username="one"), Users(id=2, username="two")])
        self.db.flush()
        self.db.add_all([Product(id=1, user_id=1, name="a"), Product(id=2, user_id=1, name="b"),
                         Product(id=3, user_id=2, name="c")])
        self.db.flush()
        for doc_id, product_id in [(1, 1), (2, 2), (3, 3)]:
            self.db.add(ProductDocument(id=doc_id, product_id=product_id, filename="manual.txt",
                storage_key=f"uploads/{doc_id}/{product_id}/manual.txt", size_bytes=100,
                content_type="text/plain", status="uploaded"))
        self.db.commit()
        self.db.info["user_id"] = 1

    def chunks(self, text="alpha beta gamma delta", *, user_id=1, product_id=1, document_id=1,
               model="text-embedding-v3", dimensions=64):
        return [EmbeddedChunk(c, [1.0] + [0.0] * (dimensions - 1), model, dimensions)
                for c in split_text(text, user_id=user_id, product_id=product_id,
                                    document_id=document_id, chunk_size=12, chunk_overlap=2)]

    def rows(self):
        return list(self.db.scalars(select(DocumentChunk).order_by(DocumentChunk.chunk_index)))

    def test_save_idempotence_and_replacement(self):
        chunks = self.chunks()
        self.assertEqual(save_document_vectors(self.db, 1, 1, chunks), len(chunks))
        save_document_vectors(self.db, 1, 1, chunks)
        self.assertEqual(len(self.rows()), len(chunks))
        save_document_vectors(self.db, 1, 1, self.chunks("short"))
        rows = self.rows()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].text, "short")
        self.assertEqual(rows[0].chunk_metadata["user_id"], 1)
        self.assertEqual(rows[0].chunk_metadata["document_id"], 1)
        self.assertEqual(len(rows[0].embedding), 64)

    def test_unauthenticated_and_cross_owner_operations(self):
        chunks = self.chunks()
        self.db.info.clear()
        for operation in [lambda: save_document_vectors(self.db, 1, 1, chunks),
                          lambda: search_vectors(self.db, 1, [1.0] * 64, dimensions=64),
                          lambda: delete_document_vectors(self.db, 1, 1)]:
            with self.assertRaises(AuthenticationError):
                operation()
        self.db.info["user_id"] = 2
        for operation in [lambda: save_document_vectors(self.db, 1, 1, chunks),
                          lambda: search_vectors(self.db, 1, [1.0] * 64, dimensions=64),
                          lambda: delete_document_vectors(self.db, 1, 1)]:
            with self.assertRaises(NotFoundError):
                operation()

    def test_document_and_chunk_scope_validation(self):
        with self.assertRaises(NotFoundError):
            save_document_vectors(self.db, 1, 2, self.chunks(document_id=2))
        for field, value in [("user_id", 2), ("product_id", 2), ("document_id", 2)]:
            item = self.chunks()[0]
            forged = replace(item, chunk=replace(item.chunk, **{field: value}))
            with self.assertRaises(InvalidInputError):
                save_document_vectors(self.db, 1, 1, [forged])
        self.assertEqual(self.rows(), [])

    def test_invalid_snapshot_does_not_replace_previous_vectors(self):
        original = self.chunks()
        save_document_vectors(self.db, 1, 1, original)
        malformed = [[], original[::-1], original + [original[0]],
                     [replace(original[0], vector=[0.0] * 64)],
                     [replace(original[0], vector=[float("nan")] * 64)],
                     [replace(original[0], vector=[float("inf")] * 64)],
                     [replace(original[0], vector=[1e39] * 64)],
                     [replace(original[0], vector=[1e-100] * 64)],
                     [replace(original[0], vector=[1.0] * 63)],
                     [original[0], replace(original[1], model="text-embedding-v4")]]
        for chunks in malformed:
            with self.subTest(chunks=len(chunks)), self.assertRaises(InvalidInputError):
                save_document_vectors(self.db, 1, 1, chunks)
            self.assertEqual([r.chunk_id for r in self.rows()], [c.chunk_id for c in original])

    def test_failed_commit_restores_previous_snapshot(self):
        original = self.chunks()
        save_document_vectors(self.db, 1, 1, original)
        with patch.object(self.db, "commit", side_effect=RuntimeError("commit failed")):
            with self.assertRaises(RuntimeError):
                save_document_vectors(self.db, 1, 1, self.chunks("new"))
        self.assertEqual([r.chunk_id for r in self.rows()], [c.chunk_id for c in original])

    def test_models_and_products_remain_separate_and_delete_is_scoped(self):
        first = self.chunks("first")
        save_document_vectors(self.db, 1, 1, first)
        save_document_vectors(self.db, 1, 1, self.chunks("other model", model="text-embedding-v4"))
        save_document_vectors(self.db, 1, 1, self.chunks("other dims", dimensions=128))
        save_document_vectors(self.db, 2, 2, self.chunks("product b", product_id=2, document_id=2))
        self.assertEqual(delete_document_vectors(self.db, 1, 1, model="text-embedding-v3", dimensions=64), 1)
        self.assertEqual(len(self.rows()), 3)
        self.assertEqual(delete_document_vectors(self.db, 1, 1, model="text-embedding-v3", dimensions=64), 0)
        self.db.delete(self.db.get(ProductDocument, 1))
        self.db.commit()
        self.assertEqual([r.product_id for r in self.rows()], [2])

    def test_deleting_document_cannot_be_indexed(self):
        self.db.get(ProductDocument, 1).status = "deleting"
        self.db.commit()
        with self.assertRaises(NotFoundError):
            save_document_vectors(self.db, 1, 1, self.chunks())

    def test_search_compiles_user_product_model_and_dimension_filters(self):
        # 通过真实查询构造器检查 PostgreSQL SQL，距离计算仍需真实 pgvector 集成测试。
        db = Mock()
        db.info = {"user_id": 7}
        db.get_bind.return_value.dialect.name = "postgresql"
        db.execute.return_value = []
        with patch("rag.vector_store.get_product"):
            self.assertEqual(search_vectors(db, 31, [1.0] * 64, model="text-embedding-v3",
                                            dimensions=64, top_k=3, min_similarity=0.7), [])
        statement = db.execute.call_args.args[0]
        compiled = statement.compile(dialect=postgresql.dialect())
        sql = str(compiled)
        for condition in ["document_chunks.user_id =", "document_chunks.product_id =",
                          "products.user_id =", "products.id =", "product_documents.product_id =",
                          "product_documents.status !=", "document_chunks.embedding_model =",
                          "document_chunks.embedding_dimensions =", "<=>", "CASE WHEN", "ORDER BY", "LIMIT"]:
            self.assertIn(condition, sql)
        params = compiled.params
        self.assertEqual(params["user_id_1"], 7)
        self.assertEqual(params["product_id_1"], 31)
        self.assertEqual(params["embedding_dimensions_1"], 64)
        self.assertEqual(params["embedding_model_1"], "text-embedding-v3")
        self.assertIn(3, params.values())

    def test_search_validates_vector_and_limits_and_requires_postgres(self):
        for options in [{"top_k": 0}, {"top_k": 101}, {"min_similarity": float("nan")},
                        {"min_similarity": 1.1}, {"model": "unknown"}, {"dimensions": 65}]:
            with self.subTest(options=options), self.assertRaises(InvalidInputError):
                search_vectors(self.db, 1, [1.0] * 64, **{"dimensions": 64, **options})
        with self.assertRaises(ServiceUnavailableError):
            search_vectors(self.db, 1, [1.0] * 64, dimensions=64)


if __name__ == "__main__":
    unittest.main()
