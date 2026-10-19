"""在隔离 schema 验证发布约束，以及跨连接共享 Chrome 互斥。"""
import os
import unittest
from uuid import uuid4
from unittest.mock import patch

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from database import engine
from integrations.publishers.locking import publishing_lock
from models.content import Content
from models.platform_account import PlatformAccount
from models.publish_record import PublishRecord
from models.task import Task
from services.exceptions import ConflictError
import test_vector_store_postgres as pg_tests


@unittest.skipUnless(os.getenv("PGVECTOR_INTEGRATION_TEST") == "1", "需显式启用 PostgreSQL 发布测试")
class PublishingPostgresTest(unittest.TestCase):
    setUp = pg_tests.PostgresVectorStoreTest.setUp

    def test_unknown_publication_blocks_second_attempt_at_database_level(self):
        task = Task(user_id=1, product_id=1, platform_name="rednote", task_type="generate", input_text="Post", status="publishing")
        self.db.add(task)
        self.db.flush()
        content = Content(task_id=task.id, title="Title", body="Body", version=1)
        account = PlatformAccount(user_id=1, platform="rednote", platform_user_id="1" * 24)
        self.db.add_all([content, account])
        self.db.flush()
        values = {"task_id": task.id, "content_id": content.id, "account_id": account.id, "platform": "rednote"}
        self.db.add(PublishRecord(**values, publish_status="unknown"))
        self.db.commit()
        self.db.add(PublishRecord(**values, publish_status="publishing"))
        with self.assertRaises(IntegrityError):
            self.db.commit()
        self.db.rollback()

    def test_browser_lock_is_exclusive_across_sessions_and_released(self):
        with Session(engine) as first, Session(engine) as second, patch(
                "integrations.publishers.locking.CHROME_LOCK_ID", int(uuid4().hex[:12], 16)):
            with publishing_lock(first):
                with self.assertRaises(ConflictError):
                    with publishing_lock(second):
                        self.fail("Second executor acquired shared browser")
            with publishing_lock(second):
                pass
