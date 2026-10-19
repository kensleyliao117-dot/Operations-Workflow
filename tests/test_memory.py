"""双独立测试数据库验证运营经验闭环，不连接业务数据或调用真实模型。"""
from contextlib import contextmanager
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import json
from unittest.mock import patch
import unittest

from sqlalchemy import create_engine, event, func, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app import app
from database import Base, get_db
from memory_database import MemoryBase, get_memory_db
from memory.models import Feedback, OptimizationMemory, PostMetrics, PublishedPost
from memory.schemas import FeedbackDraft
from models.memory_sync import MemorySyncEvent
from models.platform_account import PlatformAccount
from models.publish_record import PublishRecord
from collectors.rednote_metrics import CollectedMetrics
from agents import feedback_agent
from services.exceptions import ContentGenerationError
import test_business_api as api_tests


NOTE_ID = "2" * 24
PLATFORM_USER = "1" * 24


def valid_feedback(ids):
    return {"summary": "通勤主题仅有少量观测，需要进一步实验", "lessons": [{
        "category": "hook", "observation": "通勤场景获得互动，尚不能确定原因",
        "next_experiment": "保持产品事实一致，比较不同通勤开头", "evidence_metric_ids": ids,
        "confidence": "low", "conclusion_type": "hypothesis"}], "limitations": ["单篇作品，无法判断因果或受众偏好"]}


class MemoryAPITest(unittest.TestCase):
    post = api_tests.BusinessAPITest.post
    status = api_tests.BusinessAPITest.status
    review = api_tests.BusinessAPITest.review
    register_second_user = api_tests.BusinessAPITest.register_second_user
    generation_mocks = api_tests.BusinessAPITest.generation_mocks

    def setUp(self):
        api_tests.BusinessAPITest.setUp(self)
        self.memory_engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        @event.listens_for(self.memory_engine, "connect")
        def foreign_keys(connection, _):
            connection.execute("PRAGMA foreign_keys=ON")
        MemoryBase.metadata.create_all(self.memory_engine)
        self.memory_factory = sessionmaker(self.memory_engine)
        def business_db():
            with Session(self.engine) as db:
                db.info["memory_session_factory"] = self.memory_factory
                yield db
        def memory_db():
            with self.memory_factory() as db:
                yield db
        app.dependency_overrides[get_db] = business_db
        app.dependency_overrides[get_memory_db] = memory_db
        with Session(self.engine) as db:
            db.get(PlatformAccount, self.account_id).platform_user_id = PLATFORM_USER
            db.commit()
        patcher = patch("agents.feedback_agent.analyze_feedback", side_effect=lambda payload, **kwargs: FeedbackDraft.model_validate(
            valid_feedback([row["id"] for row in payload["metrics_snapshots"]])))
        self.analyzer = patcher.start()
        self.addCleanup(patcher.stop)
        self.record = self.publish()
        self.base = f"/publish-records/{self.record['id']}/memory"

    def tearDown(self):
        api_tests.BusinessAPITest.tearDown(self)
        self.memory_engine.dispose()

    def publish(self, visibility="public"):
        task = self.post("/tasks", {"product_id": self.product_id, "platform_name": "rednote",
                                    "task_type": "generate", "input_text": "通勤文案"})
        content = self.post("/contents", {"task_id": task["id"], "title": "通勤音乐", "body": "通勤路上听听音乐", "tags": ["通勤"]})
        self.status(task["id"], "running")
        self.status(task["id"], "pending_review")
        self.review(task["id"], content["id"])
        record = self.post("/publish-records", {"task_id": task["id"], "content_id": content["id"],
                                               "platform": "rednote", "account_id": self.account_id, "visibility": visibility})
        return self.post(f"/publish-records/{record['id']}/result", {"publish_status": "published",
            "account_id": self.account_id, "platform_post_id": NOTE_ID,
            "platform_post_url": f"https://www.xiaohongshu.com/explore/{NOTE_ID}"}, 200)

    def metrics(self, key="manual-metric-001", **values):
        return self.post(self.base + "/metrics", {"account_id": self.account_id, "idempotency_key": key,
            "observed_at": datetime.now(timezone.utc).isoformat(), "metrics": values or {"likes": 4, "comments": 0}})

    def experience(self):
        metric = self.metrics()
        return self.post(self.base + "/analyze", {"metrics_ids": [metric["id"]]})

    def test_success_auto_syncs_to_separate_database_and_keeps_content_version(self):
        self.assertNotIn("published_posts", Base.metadata.tables)
        self.assertNotIn("tasks", MemoryBase.metadata.tables)
        with self.memory_factory() as db:
            post = db.scalar(select(PublishedPost))
            self.assertEqual(post.publish_record_id, self.record["id"])
            self.assertEqual(post.content_id, self.record["content_id"])
            self.assertEqual(post.content_version, 1)
            self.assertEqual(post.platform_user_id, PLATFORM_USER)
        status = self.client.get(self.base + "/sync-status").json()
        self.assertEqual(status["status"], "synced")
        self.post(self.base + "/sync", None, 200)
        with self.memory_factory() as db:
            self.assertEqual(db.scalar(select(func.count(PublishedPost.id))), 1)

    def test_frontend_publication_lookup_is_scoped_to_current_user(self):
        response = self.client.get(self.base + "/publication")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["publish_record_id"], self.record["id"])
        self.assertEqual(response.json()["user_id"], self.user_id)
        self.register_second_user()
        self.assertEqual(self.client.get(self.base + "/publication").status_code, 404)

    def test_metrics_keep_missing_values_missing_and_zero_is_valid(self):
        row = self.metrics(likes=0)
        self.assertEqual(row["metrics"], {"likes": 0})
        self.assertEqual(row["source"], "manual")
        self.assertEqual(row["precision"], {"likes": "manual_reported"})
        self.assertEqual(len(self.client.get(self.base + "/metrics").json()), 1)

    def test_metrics_request_is_idempotent_but_cannot_change_values(self):
        payload = {"account_id": self.account_id, "idempotency_key": "same-metric-key", "metrics": {"likes": 5},
                   "observed_at": datetime.now(timezone.utc).isoformat()}
        first = self.post(self.base + "/metrics", payload)
        self.assertEqual(self.post(self.base + "/metrics", payload)["id"], first["id"])
        self.post(self.base + "/metrics", {**payload, "metrics": {"likes": 6}}, 409)

    def test_feedback_publication_list_only_exposes_owned_published_content(self):
        rows = self.client.get('/memory/publications?platform=rednote').json()
        self.assertEqual([r['id'] for r in rows], [self.record['id']])
        self.assertEqual(rows[0]['title'], '通勤音乐')
        self.assertEqual(rows[0]['product_id'], self.product_id)
        self.assertEqual(self.client.get('/memory/publications?platform=other').json(), [])
        self.assertEqual(self.client.get('/memory/publications?offset=-1').status_code, 422)
        self.register_second_user()
        self.assertEqual(self.client.get('/memory/publications').json(), [])

    def test_repeated_observations_keep_age_and_timing_in_ai_evidence(self):
        published = datetime.now(timezone.utc) - timedelta(days=2)
        with Session(self.engine) as db:
            db.get(PublishRecord, self.record['id']).published_at = published.replace(tzinfo=None)
            db.commit()
        with self.memory_factory() as db:
            db.scalar(select(PublishedPost)).published_at = published.replace(tzinfo=None)
            db.commit()
        def save(age, key):
            data = {'account_id': self.account_id, 'idempotency_key': key,
                    'observed_at': (published + timedelta(seconds=age)).isoformat(), 'metrics': {'views': 10, 'likes': 0}}
            return self.post(self.base + '/metrics', data)
        first, second = save(7200, 'age-two-hours'), save(86400, 'age-one-day')
        self.assertEqual(first['elapsed_since_publish_seconds'], 7200)
        self.assertEqual(second['elapsed_since_publish_seconds'], 86400)
        self.assertNotEqual(first['id'], second['id'])
        self.assertIsNotNone(first['published_at'])
        rows = self.client.get(self.base + '/metrics').json()
        self.assertEqual([r['id'] for r in rows], [second['id'], first['id']])
        self.post(self.base + '/analyze', {'metrics_ids': [second['id'], first['id']]})
        evidence = self.analyzer.call_args.args[0]
        self.assertEqual([r['elapsed_since_publish_seconds'] for r in evidence['metrics_snapshots']], [7200, 86400])
        self.assertIsNotNone(evidence['published_at'])
        with self.memory_factory() as db:
            saved = db.scalar(select(Feedback)).evidence_snapshot['metrics_snapshots']
            self.assertEqual(saved[0]['elapsed_since_publish_seconds'], 7200)

    def test_unknown_publish_time_is_not_invented_and_can_fill_missing_mirror_time(self):
        with Session(self.engine) as db:
            db.get(PublishRecord, self.record['id']).published_at = None
            db.commit()
        with self.memory_factory() as db:
            db.scalar(select(PublishedPost)).published_at = None
            db.commit()
        metric = self.metrics()
        self.assertIsNone(metric['elapsed_since_publish_seconds'])
        self.assertIsNone(metric['published_at'])
        published = datetime.now(timezone.utc) - timedelta(days=1)
        with Session(self.engine) as db:
            db.get(PublishRecord, self.record['id']).published_at = published.replace(tzinfo=None)
            db.commit()
        rows = self.client.get(self.base + '/metrics').json()
        self.assertIsNotNone(rows[0]['elapsed_since_publish_seconds'])

    def test_public_post_can_explicitly_collect_creator_data_more_than_once(self):
        first_time = datetime.now(timezone.utc)
        result = CollectedMetrics(NOTE_ID, PLATFORM_USER, {'views': 20, 'likes': 0}, {},
                                  {'views': 'creator_reported'}, first_time, 'rednote_creator')
        with patch('collectors.rednote_metrics.collect_creator_metrics', return_value=result) as creator, \
                patch('collectors.rednote_metrics.collect_metrics') as public:
            first = self.post(self.base + '/collect', {'idempotency_key': 'creator-public-first', 'source': 'rednote_creator'})
            same = self.post(self.base + '/collect', {'idempotency_key': 'creator-public-first', 'source': 'rednote_creator'})
            second = self.post(self.base + '/collect', {'idempotency_key': 'creator-public-second', 'source': 'rednote_creator'})
        self.assertEqual(first['id'], same['id'])
        self.assertNotEqual(first['id'], second['id'])
        self.assertEqual(creator.call_count, 2)
        self.assertNotIn('expected_visibility', creator.call_args.kwargs)
        public.assert_not_called()
        self.post(self.base + '/collect', {'idempotency_key': 'creator-public-first', 'source': 'rednote_public'}, 409)

    def test_invalid_metrics_account_and_observation_time_are_rejected(self):
        base = {"account_id": self.account_id, "idempotency_key": "bad-metric-key",
                "observed_at": datetime.now(timezone.utc).isoformat(), "metrics": {"likes": 1}}
        for metrics in ({}, {"likes": -1}, {"likes": True}, {"fake_score": 99}):
            self.post(self.base + "/metrics", {**base, "metrics": metrics}, 422)
        self.post(self.base + "/metrics", {**base, "account_id": 999}, 409)
        self.post(self.base + "/metrics", {**base, "observed_at": "2020-01-01T00:00:00"}, 422)
        self.post(self.base + "/metrics", {**base, "observed_at": "2020-01-01T00:00:00Z"}, 422)

    def test_collect_verifies_post_account_and_replays_without_opening_chrome(self):
        result = CollectedMetrics(NOTE_ID, PLATFORM_USER, {"likes": 10000}, {"likes": "1万"},
                                  {"likes": "displayed_rounded"}, datetime.now(timezone.utc))
        with patch("collectors.rednote_metrics.collect_metrics", return_value=result) as collect:
            first = self.post(self.base + "/collect", {"idempotency_key": "public-metric-key"})
            second = self.post(self.base + "/collect", {"idempotency_key": "public-metric-key"})
        self.assertEqual(first["id"], second["id"])
        self.assertEqual(first["precision"]["likes"], "displayed_rounded")
        self.assertNotIn("impressions", first["metrics"])
        collect.assert_called_once()

    def test_private_metrics_use_creator_source_and_replay_without_collecting_again(self):
        private = self.publish(visibility="private")
        endpoint = f"/publish-records/{private['id']}/memory/collect"
        result = CollectedMetrics(NOTE_ID, PLATFORM_USER, {"likes": 0, "comments": 0},
            {"likes": "0", "comments": "0"}, {"likes": "creator_reported", "comments": "creator_reported"},
            datetime.now(timezone.utc), "rednote_creator")
        with patch("collectors.rednote_metrics.collect_creator_metrics", return_value=result) as creator, \
                patch("collectors.rednote_metrics.collect_metrics") as public:
            first = self.post(endpoint, {"idempotency_key": "creator-private-001"})
            same = self.post(endpoint, {"idempotency_key": "creator-private-001"})
        self.assertEqual(first["id"], same["id"])
        self.assertEqual(first["source"], "rednote_creator")
        self.assertEqual(first["metrics"], {"likes": 0, "comments": 0})
        public.assert_not_called()
        creator.assert_called_once()

    def test_private_collection_rejects_incorrect_source_without_saving(self):
        private = self.publish(visibility="private")
        endpoint = f"/publish-records/{private['id']}/memory"
        result = CollectedMetrics(NOTE_ID, PLATFORM_USER, {"likes": 0}, {}, {}, datetime.now(timezone.utc))
        with patch("collectors.rednote_metrics.collect_creator_metrics", return_value=result):
            self.post(endpoint + "/collect", {"idempotency_key": "creator-wrong-source"}, 409)
        self.assertEqual(self.client.get(endpoint + "/metrics").json(), [])

    def test_changed_visibility_does_not_prevent_public_collection_of_same_post(self):
        private = self.publish(visibility="private")
        endpoint = f"/publish-records/{private['id']}/memory"
        result = CollectedMetrics(NOTE_ID, PLATFORM_USER, {'likes': 0, 'favorites': 0, 'comments': 0},
            {'likes': '0', 'favorites': '0', 'comments': '0'}, {}, datetime.now(timezone.utc))
        with patch('collectors.rednote_metrics.collect_metrics', return_value=result):
            saved = self.post(endpoint + '/collect', {'idempotency_key': 'changed-visibility-001',
                'source': 'rednote_public', 'source_url': f'https://www.xiaohongshu.com/explore/{NOTE_ID}?xsec_source=example'})
        self.assertEqual(saved['metrics'], result.metrics)
        self.assertEqual(self.client.get(f"/publish-records/{private['id']}").json()['visibility'], 'private')

    def test_wrong_post_or_account_and_forwarded_collect_are_not_saved(self):
        result = CollectedMetrics("3" * 24, PLATFORM_USER, {"likes": 1}, {}, {}, datetime.now(timezone.utc))
        with patch("collectors.rednote_metrics.collect_metrics", return_value=result):
            self.post(self.base + "/collect", {"idempotency_key": "wrong-metric-key"}, 409)
        response = self.client.post(self.base + "/collect", json={"idempotency_key": "forged-metric-key"},
                                    headers={"X-Forwarded-For": "127.0.0.1"})
        self.assertEqual(response.status_code, 403)
        self.assertEqual(self.client.get(self.base + "/metrics").json(), [])

    def test_feedback_is_evidenced_cached_and_creates_new_versions_for_new_observations(self):
        first = self.metrics()
        payload = {"metrics_ids": [first["id"]]}
        one = self.post(self.base + "/analyze", payload)
        same = self.post(self.base + "/analyze", payload)
        self.assertEqual(one["id"], same["id"])
        self.analyzer.assert_called_once()
        second = self.metrics("manual-metric-002", likes=8)
        two = self.post(self.base + "/analyze", {"metrics_ids": [first["id"], second["id"]], "user_note": "尝试新的通勤开头"})
        self.assertEqual(two["version"], 2)
        self.assertIsNotNone(self.analyzer.call_args.args[0]["previous_experience"])
        self.assertEqual(len(self.client.get(f"/memory/experiences?product_id={self.product_id}").json()), 2)

    def test_analysis_failure_does_not_save_feedback_or_experience(self):
        metric = self.metrics()
        with patch("agents.feedback_agent.analyze_feedback", side_effect=ContentGenerationError("bad response")):
            self.post(self.base + "/analyze", {"metrics_ids": [metric["id"]]}, 503)
        with self.memory_factory() as db:
            self.assertEqual(db.scalar(select(func.count(Feedback.id))), 0)
            self.assertEqual(db.scalar(select(func.count(OptimizationMemory.id))), 0)

    def test_default_model_provenance_is_saved_and_model_change_creates_new_experience(self):
        from cryptography.fernet import Fernet
        data = {"name": "Custom", "provider": "Another provider", "model": "chat",
                "base_url": "https://api.vendor.example/v1", "api_key": "test-key", "is_default": True}
        metric = self.metrics()
        with patch("config.MODEL_CREDENTIALS_KEY", Fernet.generate_key().decode()):
            model = self.post("/analysis-models", data)
            first = self.post(self.base + "/analyze", {"metrics_ids": [metric["id"]]})
            self.assertEqual(self.analyzer.call_args.kwargs["profile"].id, model["id"])
            self.assertEqual(self.client.put(f"/analysis-models/{model['config_id']}", json={**data,
                "model": "new-chat"}).status_code, 200)
            second = self.post(self.base + "/analyze", {"metrics_ids": [metric["id"]]})
            self.assertEqual(second["version"], first["version"] + 1)
            with self.memory_factory() as db:
                row = db.get(Feedback, second["feedback_id"])
                self.assertEqual(row.model, "new-chat")
                self.assertEqual(row.evidence_snapshot["model_profile"]["id"], model["id"])
                self.assertNotIn("api_key", json.dumps(row.evidence_snapshot))

    def test_changed_analysis_prompt_creates_new_version_instead_of_reusing_stale_result(self):
        metric = self.metrics()
        data = {"metrics_ids": [metric["id"]]}
        first = self.post(self.base + "/analyze", data)
        with patch("agents.feedback_agent.PROMPT_VERSION", "updated-prompt-for-test"):
            second = self.post(self.base + "/analyze", data)
            same = self.post(self.base + "/analyze", data)
        self.assertNotEqual(first["id"], second["id"])
        self.assertEqual(second["version"], 2)
        self.assertEqual(same["id"], second["id"])
        self.assertEqual(self.analyzer.call_count, 2)

    def test_retrieval_is_product_scoped_and_disabled_latest_version_does_not_revive_old_version(self):
        one = self.experience()
        second = self.metrics("metric-second-version", likes=8)
        two = self.post(self.base + "/analyze", {"metrics_ids": [second["id"]]})
        endpoint = f"/products/{self.product_id}/memory/retrieve?query=通勤"
        rows = self.client.get(endpoint).json()
        self.assertEqual([row["id"] for row in rows], [two["id"]])
        response = self.client.patch(f"/memory/experiences/{two['id']}", json={"is_active": False})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.client.get(endpoint).json(), [])
        other = self.post("/products", {"name": "Different product"})
        self.assertEqual(self.client.get(f"/products/{other['id']}/memory/retrieve?query=通勤").json(), [])

    def test_user_isolation_covers_metrics_analysis_and_memory_reads(self):
        experience = self.experience()
        self.client.headers.pop("Authorization")
        self.assertEqual(self.client.get(self.base + "/metrics").status_code, 401)
        self.register_second_user()
        self.assertEqual(self.client.get(self.base + "/metrics").status_code, 404)
        self.post(self.base + "/analyze", {"metrics_ids": [1]}, 404)
        self.assertEqual(self.client.get(f"/memory/experiences/{experience['id']}").status_code, 404)
        self.assertEqual(self.client.get(f"/products/{self.product_id}/memory/retrieve?query=通勤").status_code, 404)

    def test_generation_retrieves_experience_separately_and_persists_reference(self):
        experience = self.experience()
        task = self.post("/tasks", {"product_id": self.product_id, "platform_name": "rednote",
                                    "task_type": "generate", "input_text": "写一篇通勤文案"})
        draft = {"title": "通勤", "body": "适合通勤", "tags": ["通勤"]}
        with patch("services.content_generation.retriever.retrieve", return_value=self.generation_mocks()), \
             patch("integrations.llm.deepseek.generate_text", return_value=json.dumps(draft)) as model:
            generated = self.post(f"/tasks/{task['id']}/generate", {})
        self.assertEqual(generated["memory_status"], "used")
        self.assertEqual(generated["memory_references"][0]["id"], experience["id"])
        prompt = json.loads(model.call_args.kwargs["user_prompt"])
        self.assertIn("operation_memories", prompt)
        self.assertIn("product_knowledge", prompt)
        saved = self.client.get(f"/contents/{generated['content_id']}").json()
        self.assertEqual(saved["generation_metadata"]["memory_references"][0]["version"], 1)

    def test_memory_outage_does_not_fail_publication_and_sync_can_recover(self):
        @contextmanager
        def unavailable(_):
            raise SQLAlchemyError("private database detail")
            yield
        with patch("memory.services.publications.memory_transaction", unavailable):
            record = self.publish()
        base = f"/publish-records/{record['id']}/memory"
        self.assertEqual(record["publish_status"], "published")
        status = self.client.get(base + "/sync-status").json()
        self.assertEqual(status["status"], "failed")
        self.assertNotIn("private", status["error_message"])
        self.post(base + "/sync", None, 200)
        self.assertEqual(self.client.get(base + "/sync-status").json()["status"], "synced")


class FeedbackAgentTest(unittest.TestCase):
    def test_rejects_invented_metric_ids_and_non_hypothesis_output(self):
        payload = {"metrics_snapshots": [{"id": 1, "metrics": {"likes": 0}}]}
        for value in (valid_feedback([999]), {**valid_feedback([1]), "summary": " "},
                      {**valid_feedback([1]), "lessons": [{**valid_feedback([1])["lessons"][0], "conclusion_type": "proven"}]}):
            with patch("agents.feedback_agent.deepseek.generate_text", return_value=json.dumps(value)), self.assertRaises(ContentGenerationError):
                feedback_agent.analyze_feedback(payload)


if __name__ == "__main__":
    unittest.main()
