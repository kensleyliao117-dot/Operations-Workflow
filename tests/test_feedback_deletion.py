"""反馈删除、证据关联、用户隔离及主动重新启用。"""
import unittest
from unittest.mock import patch

from sqlalchemy import select
from sqlalchemy.orm import Session

from memory.models import Feedback, OptimizationMemory, PostMetrics, PublishedPost
from services.exceptions import ConflictError
import test_business_api as business
import test_memory as memory_tests


class FeedbackDeletionTest(unittest.TestCase):
    setUp = memory_tests.MemoryAPITest.setUp
    tearDown = memory_tests.MemoryAPITest.tearDown
    post = business.BusinessAPITest.post
    status = business.BusinessAPITest.status
    review = business.BusinessAPITest.review
    publish = memory_tests.MemoryAPITest.publish
    metrics = memory_tests.MemoryAPITest.metrics
    experience = memory_tests.MemoryAPITest.experience
    register_second_user = business.BusinessAPITest.register_second_user

    def test_delete_all_feedback_preserves_publication_and_does_not_auto_restore(self):
        self.experience()
        self.assertEqual(self.client.delete(self.base).status_code, 204)
        self.assertEqual(self.client.get('/memory/publications').json(), [])
        self.assertEqual(self.client.get(f"/publish-records/{self.record['id']}").status_code, 200)
        self.assertEqual(self.client.get(self.base + '/sync-status').json()['status'], 'deleted')
        self.assertEqual(self.client.get(self.base + '/metrics').status_code, 404)
        self.assertEqual(self.client.post(self.base + '/analyze', json={'metrics_ids': [1]}).status_code, 404)
        self.assertEqual(self.client.delete(self.base).status_code, 204)
        with Session(self.memory_engine) as db:
            for model in (PublishedPost, PostMetrics, Feedback, OptimizationMemory):
                self.assertIsNone(db.scalar(select(model)))
        self.post(self.base + '/sync', {}, 200)
        self.assertEqual(self.client.get(self.base + '/metrics').json(), [])
        self.assertEqual(len(self.client.get('/memory/publications').json()), 1)

    def test_metric_delete_removes_dependent_experience_but_preserves_other_observations(self):
        experience = self.experience()
        first = self.client.get(self.base + '/metrics').json()[0]
        second = self.metrics(key='another-observation', likes=10)
        response = self.client.delete(self.base + f"/metrics/{first['id']}")
        self.assertEqual(response.status_code, 204)
        self.assertEqual([row['id'] for row in self.client.get(self.base + '/metrics').json()], [second['id']])
        self.assertEqual(self.client.get(f"/memory/experiences/{experience['id']}").status_code, 404)
        self.assertEqual(self.client.get(f"/memory/feedback/{experience['feedback_id']}").status_code, 404)

    def test_experience_delete_preserves_metrics(self):
        experience = self.experience()
        self.assertEqual(self.client.delete(f"/memory/experiences/{experience['id']}").status_code, 204)
        self.assertEqual(len(self.client.get(self.base + '/metrics').json()), 1)
        self.assertEqual(self.client.get(f"/memory/feedback/{experience['feedback_id']}").status_code, 404)

    def test_other_user_cannot_delete_feedback_metrics_or_experiences(self):
        experience = self.experience()
        metric = self.client.get(self.base + '/metrics').json()[0]
        self.register_second_user()
        for path in (self.base, self.base + f"/metrics/{metric['id']}", f"/memory/experiences/{experience['id']}"):
            self.assertEqual(self.client.delete(path).status_code, 404)

    def test_metric_deleted_during_ai_analysis_cannot_be_saved_as_new_experience(self):
        metric = self.metrics()
        def delete_evidence(payload, **kwargs):
            self.assertEqual(self.client.delete(self.base + f"/metrics/{metric['id']}").status_code, 204)
            from memory.schemas import FeedbackDraft
            return FeedbackDraft.model_validate(memory_tests.valid_feedback([metric['id']]))
        self.analyzer.side_effect = delete_evidence
        self.post(self.base + '/analyze', {'metrics_ids': [metric['id']]}, 409)
        self.assertEqual(self.client.get('/memory/experiences', params={'product_id': self.product_id}).json(), [])

    def test_feedback_cleanup_failure_can_retry_and_old_experience_is_not_retrieved(self):
        from sqlalchemy.exc import SQLAlchemyError
        self.experience()
        with patch('memory.services.deletion.delete_posts', side_effect=SQLAlchemyError('unavailable')):
            self.assertEqual(self.client.delete(self.base).status_code, 503)
        self.assertEqual(self.client.get(self.base + '/sync-status').json()['status'], 'deleting')
        self.assertEqual(self.client.get(f'/products/{self.product_id}/memory/retrieve', params={'query': '通勤'}).json(), [])
        self.post(self.base + '/sync', {}, 409)
        self.assertEqual(self.client.delete(self.base).status_code, 204)
