"""在两个真实 PostgreSQL 数据库的隔离 schema 中验证同步，不改业务数据。"""
import os
from contextlib import contextmanager
import unittest
from uuid import uuid4

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from memory_database import MemoryBase, memory_engine
from memory.models import PublishedPost
from memory.services.publications import sync_publication
from models.content import Content
from models.platform_account import PlatformAccount
from models.review import Review
from models.task import Task
from models.memory_sync import MemorySyncEvent
from schemas.publish_record import PublishRequest, PublishResult
from services import publish_record, task as task_service
import test_vector_store_postgres as pg_tests


@unittest.skipUnless(os.getenv("PGVECTOR_INTEGRATION_TEST") == "1", "需显式启用 PostgreSQL 双库经验测试")
class MemoryPostgresTest(unittest.TestCase):
    def setUp(self):
        pg_tests.PostgresVectorStoreTest.setUp(self)
        self.memory_connection = memory_engine.connect()
        self.addCleanup(self.memory_connection.close)
        transaction = self.memory_connection.begin()
        self.addCleanup(transaction.rollback)
        schema = "memory_test_" + uuid4().hex
        self.memory_connection.execute(text(f"CREATE SCHEMA {schema}"))
        self.memory_connection.execute(text(f"SET LOCAL search_path TO {schema}, public"))
        MemoryBase.metadata.create_all(self.memory_connection, checkfirst=False)
        self.memory = Session(bind=self.memory_connection, join_transaction_mode="create_savepoint")
        self.addCleanup(self.memory.close)

    def test_database_is_independent_and_sync_is_replayable(self):
        self.assertNotEqual(self.db.scalar(text("SELECT current_database()")),
                            self.memory.scalar(text("SELECT current_database()")))
        task = Task(user_id=1, product_id=1, platform_name="rednote", task_type="generate", input_text="Post", status="approved")
        self.db.add(task)
        self.db.flush()
        content = Content(task_id=task.id, title="Title", body="Body", version=1)
        account = PlatformAccount(user_id=1, platform="rednote", platform_user_id="1" * 24)
        self.db.add_all([content, account])
        self.db.flush()
        self.db.add(Review(task_id=task.id, content_id=content.id, reviewer_id=1, status="approved"))
        self.db.commit()
        created = publish_record.create_publish_record(self.db, PublishRequest(task_id=task.id, content_id=content.id,
            platform="rednote", account_id=account.id))
        published = publish_record.record_publish_result(self.db, created.id, PublishResult(publish_status="published",
            platform_post_id="2" * 24, account_id=account.id))
        synced = sync_publication(self.db, self.memory, published.id)
        replay = sync_publication(self.db, self.memory, published.id)
        self.assertEqual(synced.id, replay.id)
        self.assertEqual(self.memory.scalar(select(func.count(PublishedPost.id))), 1)
        event = self.db.scalar(select(MemorySyncEvent).where(MemorySyncEvent.publish_record_id == published.id))
        self.assertEqual(event.status, "synced")
        @contextmanager
        def memory_factory():
            yield self.memory
        self.db.info['memory_session_factory'] = memory_factory
        deleted_task_id = task.id
        task_service.delete_task(self.db, deleted_task_id)
        self.assertEqual(self.memory.scalar(select(func.count(PublishedPost.id))), 0)
        self.assertIsNone(self.db.scalar(select(Task).where(Task.id == deleted_task_id)))
