"""真实 PostgreSQL 事务内验证案例分析和发布账号归属，模型 HTTP 使用模拟响应。"""
import json
import os
import unittest
from unittest.mock import patch

import httpx
from sqlalchemy.exc import IntegrityError

from collectors.base import CollectedCase
from integrations.llm.deepseek import DeepSeekClient
from models.publish_record import PublishMetrics
from models.rednote import RednoteAnalysis
from schemas.rednote import RednoteAnalysisDraft, RednoteAnalysisRevisionDraft, RednoteFeedbackCreate, RednoteReviseRequest
from services.exceptions import NotFoundError
from schemas.content import ContentCreate
from schemas.platform_account import PlatformAccountCreate
from schemas.publish_record import PublishRequest, PublishResult, PublishMetricsCreate
from schemas.review import ReviewCreate
from schemas.task import TaskCreate, TaskStatusUpdate
from services import content, platform_account, publish_record, rednote, review, task
import test_vector_store_postgres as pg_tests
from test_collection_api import VALID_ANALYSIS


def tearDownModule():
    pg_tests.engine.dispose()


@unittest.skipUnless(os.getenv("PGVECTOR_INTEGRATION_TEST") == "1", "需显式启用 PostgreSQL 集成测试")
class CollectionPostgresTest(unittest.TestCase):
    setUp = pg_tests.PostgresVectorStoreTest.setUp

    def test_analysis_feedback_versions_and_comparison_keep_immutable_history(self):
        case_id = rednote.save_collected_case(self.db, CollectedCase("rednote",
            "https://www.xiaohongshu.com/explore/0123456789abcdef01234567", "Example", "Public source")).id
        first_value = RednoteAnalysisDraft(**VALID_ANALYSIS)
        second_value = RednoteAnalysisRevisionDraft(**{**VALID_ANALYSIS, "hook": "Revised hook"},
                                                    new_insights=["Distinguish observation from inference"])
        with patch("agents.analysis_agent.analyze_case", side_effect=[first_value, second_value, second_value]) as model:
            first = rednote.analyze_case(self.db, case_id)
            first_id = first.id
            feedback_id = rednote.save_feedback(self.db, first_id, RednoteFeedbackCreate(content="Add missing evidence limits")).id
            second = rednote.revise_analysis(self.db, first_id, RednoteReviseRequest(feedback_ids=[feedback_id]))
            second_id = second.id
            third = rednote.analyze_case(self.db, case_id)
            self.assertEqual(model.call_args.kwargs["previous_analysis"]["analysis_id"], second_id)
        self.assertEqual((first.version, second.version, third.version), (1, 2, 3))
        self.assertEqual(third.parent_analysis_id, second_id)
        self.assertEqual(second.feedback_ids, [feedback_id])
        self.assertEqual(rednote.get_analysis(self.db, first_id).result, VALID_ANALYSIS)
        comparison = rednote.compare_analyses(self.db, first_id, second_id)
        self.assertTrue(comparison.same_source)
        self.assertIn("hook", [d.field for d in comparison.differences])
        self.assertEqual(rednote.search_cases(self.db, q="Example")[0].analysis_count, 3)
        self.db.info["user_id"] = 2
        with self.assertRaises(NotFoundError):
            rednote.list_feedback(self.db, first_id)
        self.db.info["user_id"] = 1
        with self.assertRaises(IntegrityError):
            with self.db.begin_nested():
                self.db.add(RednoteAnalysis(cases_id=case_id, version=3, content="Duplicate"))
                self.db.flush()

    def test_case_analysis_and_publish_metrics_are_persisted_with_matching_account(self):
        collected = CollectedCase("rednote", "https://www.xiaohongshu.com/explore/0123456789abcdef01234567",
            "Example", "Public example text", image_urls=("https://images.example/a.jpg",),
            metrics={"likes": 10000, "favorites": None}, metrics_raw={"likes": "1万"})
        case = rednote.save_collected_case(self.db, collected)
        case_id = case.id
        self.assertEqual(rednote.save_collected_case(self.db, collected).id, case_id)
        self.assertEqual(rednote.get_case(self.db, case_id).metrics["likes"], 10000)

        def handler(request):
            self.assertFalse(self.db.in_transaction())
            payload = json.loads(request.content)
            inputs = json.loads(payload["messages"][1]["content"])
            self.assertEqual(inputs["case"]["body"], "Public example text")
            return httpx.Response(200, json={"choices": [{"finish_reason": "stop", "message": {
                "content": json.dumps(VALID_ANALYSIS),
            }}]})

        with patch("config.DEEPSEEK_API_KEY", "test-key"), patch("config.MODEL_DEEPSEEK", "deepseek-flash"), \
             patch("config.URL_DEEPSEEK", "https://api.deepseek.com"), \
             httpx.Client(transport=httpx.MockTransport(handler)) as http:
            model = DeepSeekClient(http_client=http)
            with patch("agents.analysis_agent.deepseek.DeepSeekClient", return_value=model):
                analysis = rednote.analyze_case(self.db, case_id)
            self.assertEqual(analysis.result, VALID_ANALYSIS)
            self.assertEqual(analysis.cases_id, case_id)

        account = platform_account.create_account(self.db, PlatformAccountCreate(platform="rednote", platform_user_id="actual-account"))
        account_id = account.id
        self.db.info["user_id"] = 2
        other_id = platform_account.create_account(self.db, PlatformAccountCreate(platform="rednote", platform_user_id="other-account")).id
        self.db.info["user_id"] = 1
        created = task.create_task(self.db, TaskCreate(product_id=1, platform_name="rednote", task_type="generate", input_text="Example"), user_id=1)
        task_id = created.id
        draft_id = content.create_content(self.db, ContentCreate(task_id=task_id, title="title", body="body")).id
        for status in ("running", "pending_review"):
            task.update_task_status(self.db, task_id, TaskStatusUpdate(status=status))
        review.create_review(self.db, ReviewCreate(task_id=task_id, content_id=draft_id, status="approved"))
        record_id = publish_record.create_publish_record(self.db, PublishRequest(
            task_id=task_id, content_id=draft_id, platform="rednote", account_id=account_id)).id
        publish_record.record_publish_result(self.db, record_id, PublishResult(
            publish_status="published", platform_post_id="post", account_id=account_id))
        feedback = publish_record.save_metrics(self.db, record_id, PublishMetricsCreate(metrics={"likes": 10}))
        self.assertEqual((feedback.account_id, feedback.publish_record_id), (account_id, record_id))
        # 数据库外键拒绝将同一作品的反馈绑定到其他平台账号。
        with self.assertRaises(IntegrityError):
            with self.db.begin_nested():
                self.db.add(PublishMetrics(user_id=1, publish_record_id=record_id, account_id=other_id,
                                           metrics={"likes": 20}, source="manual"))
                self.db.flush()
