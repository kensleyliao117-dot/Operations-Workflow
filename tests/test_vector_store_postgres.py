"""可选真实 pgvector 测试：PGVECTOR_INTEGRATION_TEST=1 时连接配置的 PostgreSQL。

所有测试数据和表均在事务内的随机 schema 中创建，最终回滚，不改业务表。
默认跳过，不会自动安装扩展或发送产品资料给模型。
"""
import os
import unittest
import uuid
import json
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import httpx
from fastapi import UploadFile

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from database import Base, engine
from models.document_chunk import DocumentChunk
from models.product import Product, ProductDocument
from models.user import Users
from rag.embeddings import EmbeddedChunk
from rag.embeddings import QwenEmbeddings
from rag.retriever import retrieve, upload_and_index_document
from rag.text_splitter import split_text
from rag.vector_store import save_document_vectors, search_vectors
from services.exceptions import NotFoundError


@unittest.skipUnless(os.getenv("PGVECTOR_INTEGRATION_TEST") == "1", "需显式启用 PostgreSQL pgvector 集成测试")
class PostgresVectorStoreTest(unittest.TestCase):
    def setUp(self):
        self.connection = engine.connect()
        self.addCleanup(self.connection.close)
        transaction = self.connection.begin()
        self.addCleanup(transaction.rollback)
        if not self.connection.scalar(text("SELECT 1 FROM pg_extension WHERE extname = 'vector'")):
            self.skipTest("当前数据库尚未启用 pgvector；安装扩展并执行迁移后重试")
        # 名称完全由程序生成；DDL、序列和数据都由外层事务回滚。
        schema = "vector_test_" + uuid.uuid4().hex
        self.connection.execute(text(f"CREATE SCHEMA {schema}"))
        self.connection.execute(text(f"SET LOCAL search_path TO {schema}, public"))
        Base.metadata.create_all(self.connection, checkfirst=False)
        self.db = Session(bind=self.connection, join_transaction_mode="create_savepoint")
        self.addCleanup(self.db.close)
        self.db.add_all([Users(id=1, username="first"), Users(id=2, username="second")])
        self.db.flush()
        self.db.add_all([Product(id=1, user_id=1, name="a"), Product(id=2, user_id=1, name="b"),
                         Product(id=3, user_id=2, name="c")])
        self.db.flush()
        for value in range(1, 4):
            self.db.add(ProductDocument(id=value, product_id=value, filename=f"{value}.txt",
                storage_key=f"uploads/{value}/{value}/file.txt", size_bytes=20,
                content_type="text/plain", status="uploaded"))
        self.db.commit()
        self.db.info["user_id"] = 1

    def store(self, product_id, doc_id, label, vector):
        chunks = split_text(label, user_id=self.db.info["user_id"], product_id=product_id, document_id=doc_id)
        save_document_vectors(self.db, product_id, doc_id,
                              [EmbeddedChunk(c, vector, "text-embedding-v3", len(vector)) for c in chunks])

    def test_cosine_ranking_threshold_model_dimensions_and_isolation(self):
        query = [1.0] + [0.0] * 63
        # 同一文档的两个片段，方向相反。
        chunks = split_text("aaaabbbb", user_id=1, product_id=1, document_id=1,
                            chunk_size=4, chunk_overlap=0)
        save_document_vectors(self.db, 1, 1, [
            EmbeddedChunk(c, query if index == 0 else [-v for v in query], "text-embedding-v3", 64)
            for index, c in enumerate(chunks)
        ])
        self.store(1, 1, "different dimensions", [1.0] * 128)
        self.store(2, 2, "other product", query)
        self.db.info["user_id"] = 2
        self.store(3, 3, "private other user", query)
        with self.assertRaises(NotFoundError):
            search_vectors(self.db, 1, query, dimensions=64, model="text-embedding-v3")
        self.db.info["user_id"] = 1
        results = search_vectors(self.db, 1, query, dimensions=64, model="text-embedding-v3")
        self.assertEqual([r.text for r in results], ["aaaa", "bbbb"])
        self.assertAlmostEqual(results[0].similarity, 1.0)
        self.assertAlmostEqual(results[1].similarity, -1.0)
        self.assertTrue(all(r.user_id == 1 and r.product_id == 1 for r in results))
        results = search_vectors(self.db, 1, query, dimensions=64, model="text-embedding-v3", min_similarity=0.5)
        self.assertEqual([r.text for r in results], ["aaaa"])
        self.assertEqual(search_vectors(self.db, 1, query, dimensions=64, model="text-embedding-v4"), [])
        self.db.get(ProductDocument, 1).status = "deleting"
        self.db.commit()
        self.assertEqual(search_vectors(self.db, 1, query, dimensions=64, model="text-embedding-v3"), [])
        self.db.delete(self.db.get(ProductDocument, 1))
        self.db.commit()
        self.assertIsNone(self.db.scalar(select(DocumentChunk.id).where(DocumentChunk.document_id == 1)))

    def test_user_model_spaces_do_not_mix_even_with_same_model_and_dimensions(self):
        for space, label, dimensions in [('a' * 64, 'space A', 3), ('b' * 64, 'space B', 3), ('c' * 64, 'space C', 4)]:
            chunks = split_text(label, user_id=1, product_id=1, document_id=1)
            save_document_vectors(self.db, 1, 1, [EmbeddedChunk(chunk, [1.] + [0.] * (dimensions - 1),
                'custom-embedding', dimensions, embedding_space=space) for chunk in chunks])
        for space, label in [('a' * 64, 'space A'), ('b' * 64, 'space B')]:
            result = search_vectors(self.db, 1, [1., 0., 0.], model='custom-embedding', dimensions=3, embedding_space=space)
            self.assertEqual([row.text for row in result], [label])
        self.assertEqual(search_vectors(self.db, 1, [1., 0., 0.], model='custom-embedding', dimensions=3,
                                        embedding_space='d' * 64), [])
        self.assertEqual(len(list(self.db.scalars(select(DocumentChunk)))), 3)

    def test_rag_upload_embedding_storage_query_and_citations(self):
        self.connection.execute(text("SELECT setval(pg_get_serial_sequence('product_documents','id'), 3)"))
        temporary = TemporaryDirectory(prefix="rag-postgres-")
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        requests = []

        def handler(request):
            requests.append(request)
            body = json.loads(request.content)
            return httpx.Response(200, json={"model": body["model"], "data": [
                {"index": i, "embedding": [1.0 if "commuting" in value else -1.0] + [0.0] * 63}
                for i, value in enumerate(body["input"])]})

        with patch("services.product.UPLOAD_DIR", root), patch("config.EMBEDDING_TOKENIZER_PATH", ""), \
                httpx.Client(transport=httpx.MockTransport(handler)) as http:
            embedder = QwenEmbeddings(api_key="test-key", model="text-embedding-v3", dimensions=64,
                                      base_url="https://embedding.example/v1", http_client=http)
            def upload(product_id, filename, body):
                return upload_and_index_document(self.db, product_id,
                    UploadFile(filename=filename, file=BytesIO(body.encode())), embedder=embedder)

            first = upload(1, "commuting.txt", "Headphones for commuting.")
            second = upload(1, "office.txt", "Headphones for office.")
            upload(2, "other-product.txt", "Different product for commuting.")
            self.db.info["user_id"] = 2
            upload(3, "private.txt", "Private other user commuting.")
            self.db.info["user_id"] = 1
            result = retrieve(self.db, 1, "commuting", min_similarity=0.5, embedder=embedder)
            self.assertEqual(len(result.sources), 1)
            self.assertEqual(result.sources[0].document_id, first.document_id)
            self.assertEqual(result.sources[0].filename, "commuting.txt")
            self.assertAlmostEqual(result.sources[0].similarity, 1.0)
            self.assertIn("[1]", result.context)
            self.assertIn("Headphones for commuting.", result.context)
            self.assertEqual(result.sources[0].metadata["product_id"], 1)
            self.assertEqual(result.sources[0].metadata["user_id"], 1)
            selected = retrieve(self.db, 1, "commuting", document_id=second.document_id,
                                min_similarity=0.5, embedder=embedder)
            self.assertEqual(selected.sources, [])
            self.assertTrue(all(r.url.host == "embedding.example" for r in requests))
            self.assertEqual(len(list(root.rglob("*.txt"))), 4)

    def test_generation_combines_real_retrieval_preview_and_versioned_storage(self):
        from sqlalchemy import func
        from integrations.llm.deepseek import DeepSeekClient
        from models.content import Content
        from models.rednote import RednoteAnalysis, RednoteCase
        from models.task import Task
        from schemas.prompt import PromptCreate
        from schemas.content_generation import TaskContentGenerationRequest
        from schemas.task import TaskCreate
        from services.content_generation import generate_content, generate_and_save_content
        from services.prompt import create_prompt
        from services.task import create_task
        from integrations.llm.deepseek import DeepSeekTimeoutError

        self.connection.execute(text("SELECT setval(pg_get_serial_sequence('product_documents','id'), 3)"))
        temporary = TemporaryDirectory(prefix="generation-postgres-")
        self.addCleanup(temporary.cleanup)
        requests = []

        def handler(request):
            requests.append(request)
            payload = json.loads(request.content)
            if request.url.host == "embedding.example":
                return httpx.Response(200, json={"model": payload["model"], "data": [
                    {"index": index, "embedding": [1.0] + [0.0] * 63}
                    for index, _ in enumerate(payload["input"])]})
            self.assertEqual(request.url.host, "api.deepseek.com")
            self.assertFalse(self.db.in_transaction())
            self.assertEqual(payload["model"], "deepseek-flash")
            self.assertEqual(payload["messages"][0]["role"], "system")
            inputs = json.loads(payload["messages"][1]["content"])
            self.assertEqual(inputs["custom_prompt"]["style_requirements"], "Use a natural tone.")
            self.assertIn("Headphones for commuting.", inputs["product_knowledge"]["context"])
            self.assertEqual(inputs["case_references"][0]["analysis_id"], analysis_id)
            self.assertEqual(inputs["case_references"][0]["creative_patterns"]["hook"], "Ask a question")
            self.assertNotIn("Foreign claim", payload["messages"][1]["content"])
            return httpx.Response(200, json={"choices": [{"finish_reason": "stop", "message": {
                "content": json.dumps({"title": "Music on the commute", "body": "Headphones for commuting.",
                                       "tags": ["commuting", "headphones"]}),
            }}]})

        with patch("services.product.UPLOAD_DIR", Path(temporary.name)), \
                patch("config.EMBEDDING_TOKENIZER_PATH", ""), \
                patch("config.EMBEDDING_MODEL", "text-embedding-v3"), \
                patch("config.EMBEDDING_DIMENSIONS", 64), \
                patch("config.DEEPSEEK_API_KEY", "test-key"), \
                patch("config.MODEL_DEEPSEEK", "deepseek-flash"), \
                patch("config.URL_DEEPSEEK", "https://api.deepseek.com"), \
                httpx.Client(transport=httpx.MockTransport(handler)) as http:
            embedder = QwenEmbeddings(api_key="test-key", base_url="https://embedding.example/v1", http_client=http)
            model = DeepSeekClient(http_client=http)
            document = upload_and_index_document(self.db, 1,
                UploadFile(filename="facts.txt", file=BytesIO(b"Headphones for commuting.")), embedder=embedder)
            prompt = create_prompt(self.db, PromptCreate(platform="rednote", name="style",
                                                         prompt_content="Use a natural tone."))
            prompt_id = prompt.id
            case = RednoteCase(user_id=1, name="Foreign claim", body="Foreign claim",
                               link="https://www.xiaohongshu.com/explore/test")
            self.db.add(case)
            self.db.flush()
            analysis = RednoteAnalysis(cases_id=case.id, content="Foreign claim", model="deepseek-flash",
                source_fingerprint="b" * 64, result={
                    "summary": "Foreign claim", "content_structure": ["Scene then explanation"],
                    "hook": "Ask a question", "emotional_triggers": ["Curiosity"], "pain_points": [],
                    "cta": "Invite discussion", "image_style": "Unknown", "video_structure": "Unknown",
                    "topics": [], "reusable_patterns": ["Scene first"], "metrics_observations": [],
                    "limitations": ["Insufficient evidence"],
                })
            self.db.add(analysis)
            self.db.flush()
            analysis_id = analysis.id
            self.db.commit()
            with patch("rag.retriever.QwenEmbeddings", return_value=embedder), \
                    patch("integrations.llm.deepseek.DeepSeekClient", return_value=model):
                result = generate_content(self.db, product_id=1, platform="rednote",
                                          user_requirement="Write a commuting post.", prompt_id=prompt_id,
                                          analysis_ids=[analysis_id])
            self.assertEqual(result.title, "Music on the commute")
            self.assertEqual(result.sources[0].document_id, document.document_id)
            self.assertEqual(result.prompt_version, 1)
            self.assertTrue(result.review_required)
            for table in (Task, Content):
                self.assertEqual(self.db.scalar(select(func.count()).select_from(table)), 0)
            self.assertEqual(sum(r.url.host == "embedding.example" for r in requests), 2)
            self.assertEqual(sum(r.url.host == "api.deepseek.com" for r in requests), 1)
            task = create_task(self.db, TaskCreate(product_id=1, platform_name="rednote",
                task_type="generate", input_text="Write a commuting post."), user_id=1)
            task_id = task.id
            options = TaskContentGenerationRequest(prompt_id=prompt_id, analysis_ids=[analysis_id])
            with patch("rag.retriever.QwenEmbeddings", return_value=embedder), \
                    patch("integrations.llm.deepseek.DeepSeekClient", return_value=model):
                first = generate_and_save_content(self.db, task_id, options)
                second = generate_and_save_content(self.db, task_id, options)
                with patch.object(model, "generate_text", side_effect=DeepSeekTimeoutError("DeepSeek 请求超时")):
                    with self.assertRaises(DeepSeekTimeoutError):
                        generate_and_save_content(self.db, task_id, options)
            contents = list(self.db.scalars(select(Content).where(Content.task_id == task_id)
                                            .order_by(Content.version)))
            self.assertEqual([c.version for c in contents], [1, 2])
            self.assertEqual([c.id for c in contents], [first.content_id, second.content_id])
            self.assertEqual(contents[0].tags, ["commuting", "headphones"])
            self.assertEqual(contents[1].body, "Headphones for commuting.")
            self.assertEqual(second.task_status, "pending_review")
            self.assertEqual(first.analysis_references[0].analysis_id, analysis_id)
            self.assertEqual(second.analysis_references[0].source_fingerprint, "b" * 64)
            failed = self.db.get(Task, task_id)
            self.assertEqual((failed.status, failed.failed_from_status), ("failed", "running"))
            self.assertIn("超时", failed.error_message)
            self.assertEqual(failed.checkpoint["content_generation"]["request"]["analysis_ids"], [analysis_id])


def tearDownModule():
    engine.dispose()


if __name__ == "__main__":
    unittest.main()
