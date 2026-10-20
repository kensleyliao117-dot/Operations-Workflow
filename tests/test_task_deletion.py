"""任务软删除的权限、执行互斥及历史保留。"""
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

    def test_delete_is_owned_and_hides_task_but_retains_content(self):
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
        self.assertEqual(self.client.get(f"/contents/{content['id']}").status_code, 200)
        self.assertEqual(self.client.patch(f"/contents/{content['id']}", json={'body': 'Changed'}).status_code, 404)
        with Session(self.engine) as db:
            self.assertTrue(db.get(Task, task_id).is_deleted)

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


class DeletedTaskFeedbackTest(unittest.TestCase):
    setUp = memory_tests.MemoryAPITest.setUp
    tearDown = memory_tests.MemoryAPITest.tearDown
    post = business.BusinessAPITest.post
    status = business.BusinessAPITest.status
    review = business.BusinessAPITest.review
    publish = memory_tests.MemoryAPITest.publish
    metrics = memory_tests.MemoryAPITest.metrics

    def test_completed_task_deletion_preserves_publication_and_feedback(self):
        task_id = self.record['task_id']
        self.assertEqual(self.client.delete(f'/tasks/{task_id}').status_code, 204)
        self.assertEqual(self.client.get(f'/tasks/{task_id}').status_code, 404)
        self.assertEqual(self.client.get(f"/publish-records/{self.record['id']}").status_code, 200)
        metric = self.metrics(likes=0, comments=0)
        self.assertEqual(metric['metrics']['likes'], 0)
