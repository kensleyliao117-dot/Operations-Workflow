"""任务彻底删除的权限、执行互斥及关联清理。"""
from datetime import datetime, timedelta, timezone
import unittest

from sqlalchemy.orm import Session

import test_business_api as business
import test_memory as memory_tests
from models.task import Task
from models.publish_record import PublishRecord
from models.workflow import TaskWorkflow


class TaskDeletionTest(unittest.TestCase):
    setUp = business.BusinessAPITest.setUp
    tearDown = business.BusinessAPITest.tearDown
    post = business.BusinessAPITest.post
    status = business.BusinessAPITest.status
    register_second_user = business.BusinessAPITest.register_second_user

    def create(self):
        return self.post('/tasks', {'product_id': self.product_id, 'platform_name': 'rednote',
                                   'task_type': 'generate', 'input_text': 'Delete test'})['id']

    def test_delete_is_owned_and_removes_task_and_content(self):
        task_id = self.create()
        content = self.post('/contents', {'task_id': task_id, 'title': 'Saved', 'body': 'History'})
        owner = dict(self.client.headers)
        self.register_second_user()
        self.assertEqual(self.client.delete(f'/tasks/{task_id}').status_code, 404)
        self.client.headers.clear()
        self.assertEqual(self.client.delete(f'/tasks/{task_id}').status_code, 401)
        self.client.headers.update(owner)
        self.assertEqual(self.client.delete(f'/tasks/{task_id}').status_code, 204)
        self.assertEqual(self.client.get('/tasks').json(), [])
        self.assertEqual(self.client.get(f'/tasks/{task_id}').status_code, 404)
        self.assertEqual(self.client.patch(f'/tasks/{task_id}/status', json={'status': 'running'}).status_code, 404)
        self.assertEqual(self.client.get(f'/tasks/{task_id}/workflow').status_code, 404)
        self.assertEqual(self.client.delete(f'/tasks/{task_id}').status_code, 404)
        self.assertEqual(self.client.get(f"/contents/{content['id']}").status_code, 404)
        self.assertEqual(self.client.patch(f"/contents/{content['id']}", json={'body': 'Changed'}).status_code, 404)
        with Session(self.engine) as db:
            self.assertIsNone(db.get(Task, task_id))

    def test_executing_tasks_and_uncertain_publications_are_blocked(self):
        task_id = self.create()
        content = self.post('/contents', {'task_id': task_id, 'title': 'Saved', 'body': 'History'})
        for state in ('running', 'publishing'):
            with Session(self.engine) as db:
                db.get(Task, task_id).status = state
                db.commit()
            self.assertEqual(self.client.delete(f'/tasks/{task_id}').status_code, 409)
        with Session(self.engine) as db:
            db.get(Task, task_id).status = 'cancelled'
            db.add(TaskWorkflow(task_id=task_id, user_id=self.user_id, run_id='delete-test', options={}, status='running'))
            db.commit()
        self.assertEqual(self.client.delete(f'/tasks/{task_id}').status_code, 409)
        with Session(self.engine) as db:
            db.get(TaskWorkflow, task_id).status = 'waiting_review'
            db.add(PublishRecord(task_id=task_id, content_id=content['id'], platform='rednote',
                                 account_id=self.account_id, publish_status='unknown'))
            db.commit()
        self.assertEqual(self.client.delete(f'/tasks/{task_id}').status_code, 409)

    def test_image_attempt_blocks_deletion_even_after_status_cancellation(self):
        task_id = self.create()
        with Session(self.engine) as db:
            task = db.get(Task, task_id)
            task.status = 'cancelled'
            task.checkpoint = {'image_generation': {'status': 'running',
                'expires_at': (datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat()}}
            db.commit()
        self.assertEqual(self.client.delete(f'/tasks/{task_id}').status_code, 409)

    def test_old_soft_deleted_task_can_be_permanently_removed(self):
        task_id = self.create()
        with Session(self.engine) as db:
            db.get(Task, task_id).is_deleted = True
            db.commit()
        self.assertEqual(self.client.get('/tasks').json(), [])
        self.assertEqual(self.client.get('/tasks?include_deleted=true').json()[0]['id'], task_id)
        self.assertEqual(self.client.delete(f'/tasks/{task_id}').status_code, 204)

    def test_task_documents_vectors_then_product_can_be_deleted(self):
        from models.document_chunk import DocumentChunk
        from sqlalchemy import select
        task_id = self.create()
        document = self.client.post(f'/products/{self.product_id}/documents',
            files={'file': ('facts.txt', b'facts')}).json()
        with Session(self.engine) as db:
            db.add(DocumentChunk(user_id=self.user_id, product_id=self.product_id,
                document_id=document['id'], chunk_id='a'*64, chunk_index=0, text='facts', start_char=0,
                end_char=5, document_hash='b'*64, embedding_model='test', embedding_dimensions=2,
                embedding=[1.,0.], chunk_metadata={}))
            db.commit()
        self.assertEqual(self.client.delete(f'/products/{self.product_id}').status_code, 409)
        self.assertEqual(self.client.delete(f'/tasks/{task_id}').status_code, 204)
        self.assertEqual(self.client.delete(f'/products/{self.product_id}').status_code, 409)
        self.assertEqual(self.client.delete(f"/products/{self.product_id}/documents/{document['id']}").status_code, 204)
        with Session(self.engine) as db:
            self.assertIsNone(db.scalar(select(DocumentChunk)))
        self.assertEqual(self.client.delete(f'/products/{self.product_id}').status_code, 204)

    def test_private_images_are_cleaned_and_file_failure_can_be_retried(self):
        from pathlib import Path
        from unittest.mock import patch
        from models.content import Content
        task_id = self.create()
        content = self.post('/contents', {'task_id': task_id, 'title': 'Saved'})
        root = Path(self.storage.name) / 'images'
        filename = 'a'*32 + '.png'
        path = root / f'user_{self.user_id}' / f'product_{self.product_id}' / filename
        path.parent.mkdir(parents=True)
        path.write_bytes(b'local-image')
        with Session(self.engine) as db:
            db.get(Content, content['id']).image_assets = [{'storage_key':
                f'generated_images/user_{self.user_id}/product_{self.product_id}/{filename}'}]
            db.commit()
        with patch('config.GENERATED_IMAGE_DIR', root):
            with patch('pathlib.Path.unlink', side_effect=PermissionError('locked')):
                self.assertEqual(self.client.delete(f'/tasks/{task_id}').status_code, 503)
            self.assertTrue(path.exists())
            self.assertEqual(self.client.delete(f'/tasks/{task_id}').status_code, 204)
        self.assertFalse(path.exists())

    def test_shared_images_and_foreign_paths_are_not_removed(self):
        from pathlib import Path
        from unittest.mock import patch
        from models.content import Content
        first_id, second_id = self.create(), self.create()
        first = self.post('/contents', {'task_id': first_id, 'title': 'First'})
        second = self.post('/contents', {'task_id': second_id, 'title': 'Second'})
        root = Path(self.storage.name) / 'images'
        filename = 'b'*32 + '.png'
        path = root / f'user_{self.user_id}' / f'product_{self.product_id}' / filename
        foreign = root / 'user_999' / 'product_999' / filename
        for item in (path, foreign):
            item.parent.mkdir(parents=True, exist_ok=True)
            item.write_bytes(b'keep-unless-last-owner')
        key = f'generated_images/user_{self.user_id}/product_{self.product_id}/{filename}'
        with Session(self.engine) as db:
            db.get(Content, first['id']).image_assets = [{'storage_key': key},
                {'storage_key': f'generated_images/user_999/product_999/{filename}'},
                {'storage_key': f'generated_images/user_{self.user_id}/product_{self.product_id}/../../keep.png'}]
            db.get(Content, second['id']).image_assets = [{'storage_key': key}]
            db.commit()
        with patch('config.GENERATED_IMAGE_DIR', root):
            self.assertEqual(self.client.delete(f'/tasks/{first_id}').status_code, 204)
            self.assertTrue(path.exists())
            self.assertTrue(foreign.exists())
            self.assertEqual(self.client.delete(f'/tasks/{second_id}').status_code, 204)
            self.assertFalse(path.exists())
            self.assertTrue(foreign.exists())


class DeletedTaskFeedbackTest(unittest.TestCase):
    setUp = memory_tests.MemoryAPITest.setUp
    tearDown = memory_tests.MemoryAPITest.tearDown
    post = business.BusinessAPITest.post
    status = business.BusinessAPITest.status
    review = business.BusinessAPITest.review
    publish = memory_tests.MemoryAPITest.publish
    metrics = memory_tests.MemoryAPITest.metrics

    def test_completed_task_deletion_removes_publication_feedback_and_product_references(self):
        from memory.models import PublishedPost, PostMetrics
        self.metrics(likes=0, comments=0)
        task_id = self.record['task_id']
        self.assertEqual(self.client.delete(f'/tasks/{task_id}').status_code, 204)
        self.assertEqual(self.client.get(f'/tasks/{task_id}').status_code, 404)
        self.assertEqual(self.client.get(f"/publish-records/{self.record['id']}").status_code, 404)
        with Session(self.memory_engine) as db:
            from sqlalchemy import select
            self.assertIsNone(db.scalar(select(PublishedPost)))
            self.assertIsNone(db.scalar(select(PostMetrics)))
        self.assertEqual(self.client.delete(f'/products/{self.product_id}').status_code, 204)

    def test_memory_cleanup_failure_keeps_retryable_task_and_blocks_new_activity(self):
        from unittest.mock import patch
        from sqlalchemy.exc import SQLAlchemyError
        task_id = self.record['task_id']
        with patch('memory.services.deletion.delete_posts', side_effect=SQLAlchemyError('unavailable')):
            self.assertEqual(self.client.delete(f'/tasks/{task_id}').status_code, 503)
        self.assertEqual(self.client.get(f'/publish-records/{self.record["id"]}').status_code, 404)
        rows = self.client.get('/tasks?include_deleted=true').json()
        self.assertEqual([row['id'] for row in rows if row['is_deleted']], [task_id])
        self.assertEqual(self.client.delete(f'/products/{self.product_id}').status_code, 409)
        self.assertEqual(self.client.delete(f'/tasks/{task_id}').status_code, 204)
