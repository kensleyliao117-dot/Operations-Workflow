"""真实 PostgreSQL checkpointer 的跨连接恢复及任务执行互斥测试。"""
import json
import os
import unittest
from unittest.mock import patch
from uuid import uuid4

from psycopg import sql

from rag.retriever import RetrievedSource, RetrievalResult
from schemas.content_generation import TaskContentGenerationRequest
from schemas.review import ReviewCreate
from schemas.task import TaskCreate
from schemas.workflow import WorkflowResumeRequest
from schemas.rules import RuleAIDraft
from models.rules import PlatformRule
from services import review, task, workflow
from services.exceptions import ConflictError
from workflows import persistence
import test_vector_store_postgres as pg_tests


@unittest.skipUnless(os.getenv("PGVECTOR_INTEGRATION_TEST") == "1", "需显式启用 PostgreSQL 工作流测试")
class WorkflowPostgresTest(unittest.TestCase):
    setUp = pg_tests.PostgresVectorStoreTest.setUp

    def test_checkpoint_survives_new_connection_and_releases_execution_lock(self):
        schema = "workflow_test_" + uuid4().hex
        schema_patch = patch("workflows.persistence.CHECKPOINT_SCHEMA", schema)
        schema_patch.start()
        self.addCleanup(schema_patch.stop)

        def cleanup():
            with persistence._connection() as connection:
                connection.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))

        self.addCleanup(cleanup)
        persistence.setup_checkpoints()
        created = task.create_task(self.db, TaskCreate(product_id=1, platform_name="rednote",
            task_type="generate", input_text="Write a commuting post"), user_id=1)
        task_id = created.id
        self.db.add(PlatformRule(platform_name="rednote", content="No unsupported claims"))
        self.db.commit()
        source = RetrievedSource("[1]", "b" * 64, 1, "facts.txt", 0, 0.9, "For commuting.", False,
            {"user_id": 1, "product_id": 1, "document_id": 1})
        knowledge = RetrievalResult(1, "commuting", "text-embedding-v3", 64, "[1] For commuting.", [source])
        draft = {"title": "Commute music", "body": "For commuting.", "tags": ["commuting"]}
        with patch("agents.rule_agent.check_content", return_value=RuleAIDraft(risks=[])), \
                patch("services.content_generation.retriever.retrieve", return_value=knowledge), \
                patch("integrations.llm.deepseek.generate_text", return_value=json.dumps(draft)) as model:
            started = workflow.start_workflow(self.db, task_id, TaskContentGenerationRequest())
            self.assertEqual(started.status, "waiting_review")
            # 新连接重新构建图，审核中断从 PostgreSQL 恢复，不依赖内存单例。
            restored = workflow.get_workflow(self.db, task_id)
            self.assertEqual(restored.interrupt["content_id"], started.content_id)
            self.assertEqual(restored.run_id, started.run_id)
            with persistence.open_checkpointer(task_id, exclusive=True):
                with self.assertRaises(ConflictError):
                    with persistence.open_checkpointer(task_id, exclusive=True):
                        self.fail("Second executor must not acquire the same task lock")
            review.create_review(self.db, ReviewCreate(task_id=task_id, content_id=started.content_id, status="approved"))
            finished = workflow.resume_workflow(self.db, task_id, WorkflowResumeRequest())
            self.assertEqual(finished.status, "approved")
            self.assertEqual(finished.next_nodes, [])
            model.assert_called_once()


def tearDownModule():
    pg_tests.engine.dispose()


if __name__ == "__main__":
    unittest.main()
