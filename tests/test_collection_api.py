"""本地采集、结构化分析、用户隔离及实际发布账号关联测试。"""
import json
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import app
from collectors.base import ChromeConnectionError, CollectedCase
from integrations.llm.deepseek import DeepSeekTimeoutError
from models.rednote import RednoteAnalysis, RednoteCase
from models.publish_record import PublishMetrics, PublishRecord
import test_business_api as api_tests


VALID_ANALYSIS = {
    "summary": "通勤耳机案例的内容表达分析", "content_structure": ["标题建立悬念", "正文描述场景"],
    "hook": "通过情绪化标题制造信息差", "emotional_triggers": ["好奇"], "pain_points": ["通勤场景需求"],
    "cta": "未观察到明确 CTA", "image_style": "未进行图片识别，无法判断视觉风格",
    "video_structure": "未提供视频信息，无法判断", "topics": ["耳机"],
    "reusable_patterns": ["场景引入后说明选择理由"], "metrics_observations": ["仅描述公开互动显示值"],
    "limitations": ["缺少曝光基数，不能确认爆款或因果关系"],
}


class CollectionAPITest(unittest.TestCase):
    setUpBase = api_tests.BusinessAPITest.setUp
    tearDown = api_tests.BusinessAPITest.tearDown
    post = api_tests.BusinessAPITest.post
    status = api_tests.BusinessAPITest.status
    task_and_content = api_tests.BusinessAPITest.task_and_content
    review = api_tests.BusinessAPITest.review
    register_second_user = api_tests.BusinessAPITest.register_second_user

    def setUp(self):
        self.setUpBase()
        self.url = "https://www.xiaohongshu.com/explore/0123456789abcdef01234567"
        self.collected = CollectedCase("rednote", self.url, "耳机案例", "案例原始正文",
            image_urls=("https://images.example/post.jpg",), metrics={"likes": 12000, "favorites": 0, "comments": None},
            metrics_raw={"likes": "1.2万", "favorites": "0", "comments": None}, author_platform_id="a" * 24)
        chrome_patch = patch("services.rednote.ChromeBrowser")
        self.chrome = chrome_patch.start()
        self.addCleanup(chrome_patch.stop)
        collector_patch = patch("services.rednote.RednoteCollector")
        self.collector = collector_patch.start().return_value
        self.addCleanup(collector_patch.stop)
        self.collector.collect.return_value = self.collected
        self.collector.collect_current.return_value = self.collected
        model_patch = patch("agents.analysis_agent.deepseek.generate_text", return_value=json.dumps(VALID_ANALYSIS))
        self.model = model_patch.start()
        self.addCleanup(model_patch.stop)

    def test_collect_save_and_analyze_returns_persisted_media_metrics_and_structure(self):
        result = self.post("/rednote/collect", {"url": self.url + "?xsec_token=private", "analyze": True})
        case, analysis = result["case"], result["analysis"]
        self.assertEqual((case["name"], case["body"], case["link"]), ("耳机案例", "案例原始正文", self.url))
        self.assertEqual(case["image_urls"], ["https://images.example/post.jpg"])
        self.assertEqual(case["metrics"], {"likes": 12000, "favorites": 0, "comments": None})
        self.assertEqual(case["metrics_raw"]["likes"], "1.2万")
        self.assertIsNotNone(case["collected_at"])
        self.assertEqual(result["analysis_status"], "completed")
        self.assertEqual(analysis["result"], VALID_ANALYSIS)
        self.assertEqual(analysis["cases_id"], case["id"])
        self.assertEqual(len(analysis["source_fingerprint"]), 64)
        self.assertEqual(json.loads(analysis["content"]), VALID_ANALYSIS)
        with Session(self.engine) as db:
            self.assertEqual(db.get(RednoteCase, case["id"]).user_id, self.user_id)
            self.assertEqual(db.get(RednoteAnalysis, analysis["id"]).result, VALID_ANALYSIS)

    def test_repeat_collection_refreshes_observations_and_does_not_duplicate_case(self):
        first = self.post("/rednote/collect", {})
        self.collector.collect_current.return_value = CollectedCase("rednote", self.url, "新标题", "新正文",
            metrics={"likes": 13000}, metrics_raw={"likes": "1.3万"})
        second = self.post("/rednote/collect", {})
        self.assertEqual(first["case"]["id"], second["case"]["id"])
        self.assertEqual(second["case"]["body"], "案例原始正文")
        self.assertEqual(second["case"]["metrics"]["likes"], 13000)
        self.assertEqual(len(self.client.get("/rednote/cases").json()), 1)
        self.model.assert_not_called()

    def test_saved_analysis_product_rag_and_custom_prompt_generate_content_for_review(self):
        collected = self.post("/rednote/collect", {"analyze": True})
        analysis_id = collected["analysis"]["id"]
        prompt = self.post("/prompts", {"platform": "rednote", "name": "Natural",
                                       "prompt_content": "使用自然的短句"})
        task = self.post("/tasks", {"product_id": self.product_id, "platform_name": "rednote",
                                    "task_type": "generate", "input_text": "写一篇通勤文案"})
        endpoint = f"/tasks/{task['id']}/generate"
        draft = {"title": "通勤音乐时间", "body": "通勤路上听听音乐。", "tags": ["通勤", "耳机"]}
        # 分析与生成复用同一个 DeepSeek 客户端入口，按调用顺序切换模拟输出。
        self.model.reset_mock()
        self.model.return_value = json.dumps(draft)
        with patch("services.content_generation.retriever.retrieve",
                   return_value=api_tests.BusinessAPITest.generation_mocks(self)):
            first = self.post(endpoint, {"prompt_id": prompt["id"], "analysis_ids": [analysis_id]})
            arguments = self.model.call_args.kwargs
            payload = json.loads(arguments["user_prompt"])
            self.assertEqual(payload["custom_prompt"]["style_requirements"], "使用自然的短句")
            self.assertEqual(payload["product_knowledge"]["context"], "[1] 适合通勤。")
            self.assertEqual(payload["case_references"][0]["analysis_id"], analysis_id)
            self.assertNotIn("案例原始正文", arguments["user_prompt"])
            second = self.post(endpoint, {})
        self.assertEqual(first["analysis_references"][0]["analysis_id"], analysis_id)
        self.assertEqual(second["analysis_references"], [])
        self.assertEqual((first["version"], second["version"]), (1, 2))
        self.assertEqual(second["task_status"], "pending_review")
        self.assertEqual(self.client.get(f"/contents/{first['content_id']}").json()["body"], draft["body"])
        self.post("/publish-records", {"task_id": task["id"], "content_id": second["content_id"],
                                      "platform": "rednote", "account_id": self.account_id}, 409)
        self.assertEqual(self.client.get("/publish-records").json(), [])

    def test_generation_analysis_ids_enforce_jwt_owner_and_schema_before_rag(self):
        collected = self.post("/rednote/collect", {"analyze": True})
        analysis_id = collected["analysis"]["id"]
        self.register_second_user()
        product = self.post("/products", {"name": "Second product"})
        task = self.post("/tasks", {"product_id": product["id"], "platform_name": "rednote",
                                    "task_type": "generate", "input_text": "Create a post"})
        endpoint = f"/tasks/{task['id']}/generate"
        self.model.reset_mock()
        with patch("services.content_generation.retriever.retrieve") as retrieve:
            for ids in ([0], [True], ["1"], [analysis_id, analysis_id], list(range(1, 7)), None):
                self.post(endpoint, {"analysis_ids": ids}, 422)
            self.post(endpoint, {"analysis_ids": [analysis_id]}, 404)
            authorization = self.client.headers.pop("Authorization")
            self.post(endpoint, {"analysis_ids": [analysis_id]}, 401)
            self.client.headers["Authorization"] = authorization
            retrieve.assert_not_called()
        self.model.assert_not_called()
        self.assertEqual(self.client.get(f"/tasks/{task['id']}").json()["status"], "pending")

    def test_jwt_and_input_validation_precede_browser_access(self):
        authorization = self.client.headers.pop("Authorization")
        self.post("/rednote/collect", {}, 401)
        self.client.headers["Authorization"] = authorization
        for value in ({"user_id": 999}, {"account_id": self.account_id}, {"url": "https://example.com"}):
            self.post("/rednote/collect", value, 422)
        self.chrome.assert_not_called()
        self.model.assert_not_called()

    def test_remote_forwarded_host_and_origin_requests_are_blocked_even_with_valid_jwt(self):
        for peer in ("203.0.113.8", "192.168.1.20"):
            remote = TestClient(app, base_url="http://127.0.0.1", client=(peer, 40000))
            try:
                result = remote.post("/rednote/collect", json={}, headers={"Authorization": f"Bearer {self.token}"})
                self.assertEqual(result.status_code, 403, result.text)
            finally:
                remote.close()
        for headers in ({"X-Forwarded-For": "127.0.0.1"}, {"Forwarded": "for=127.0.0.1"},
                        {"X-Real-IP": "127.0.0.1"}, {"Host": "external.example"}, {"Origin": "https://external.example"}):
            result = self.client.post("/rednote/collect", json={}, headers=headers)
            self.assertEqual(result.status_code, 403, result.text)
        self.chrome.assert_not_called()
        self.model.assert_not_called()

    def test_different_project_users_share_browser_but_have_separate_cases_and_analyses(self):
        first = self.post("/rednote/collect", {"analyze": True})
        self.register_second_user()
        self.assertEqual(self.client.get("/rednote/cases").json(), [])
        self.assertEqual(self.client.get("/rednote/analyses").json(), [])
        self.post(f"/rednote/cases/{first['case']['id']}/analyze", {}, 404)
        self.assertEqual(self.client.get(f"/rednote/analyses/{first['analysis']['id']}").status_code, 404)
        second = self.post("/rednote/collect", {})
        self.assertNotEqual(first["case"]["id"], second["case"]["id"])
        # 共用浏览器不要求项目用户登记发布账号。
        self.assertEqual(self.client.get("/platform-accounts").json(), [])

    def test_ai_failure_keeps_saved_case_and_can_be_retried_separately(self):
        self.model.side_effect = DeepSeekTimeoutError("DeepSeek 请求超时")
        result = self.post("/rednote/collect", {"analyze": True})
        self.assertEqual(result["analysis_status"], "failed")
        self.assertIsNone(result["analysis"])
        self.assertIn("超时", result["analysis_error"])
        self.assertEqual(self.client.get(f"/rednote/cases/{result['case']['id']}").status_code, 200)
        self.assertEqual(self.client.get("/rednote/analyses").json(), [])
        self.model.side_effect = None
        analysis = self.post(f"/rednote/cases/{result['case']['id']}/analyze", {})
        self.assertEqual(analysis["result"], VALID_ANALYSIS)

    def test_invalid_ai_structure_and_empty_model_result_are_not_saved(self):
        case = self.post("/rednote/collect", {})["case"]
        for value in ("", "private-response", "[]", '{"summary":"a","summary":"b"}',
                      json.dumps({**VALID_ANALYSIS, "extra": "private-response"})):
            self.model.return_value = value
            response = self.post(f"/rednote/cases/{case['id']}/analyze", {}, 503)
            self.assertNotIn("private-response", str(response))
        self.assertEqual(self.client.get("/rednote/analyses").json(), [])

    def test_closed_chrome_or_invalid_note_never_creates_case(self):
        self.chrome.return_value.__enter__.side_effect = ChromeConnectionError("Chrome 尚未启动")
        self.post("/rednote/collect", {}, 503)
        self.assertEqual(self.client.get("/rednote/cases").json(), [])
        self.model.assert_not_called()

    def test_case_change_during_ai_call_does_not_save_stale_analysis(self):
        case = self.post("/rednote/collect", {})["case"]

        def changed(**kwargs):
            with Session(self.engine) as db:
                db.get(RednoteCase, case["id"]).body = "已修改正文"
                db.commit()
            return json.dumps(VALID_ANALYSIS)

        self.model.side_effect = changed
        self.post(f"/rednote/cases/{case['id']}/analyze", {}, 409)
        self.assertEqual(self.client.get("/rednote/analyses").json(), [])

    def approved(self):
        task_id, content_id = self.task_and_content()
        self.review(task_id, content_id)
        return {"task_id": task_id, "content_id": content_id, "platform": "rednote", "account_id": self.account_id}

    def test_publish_requires_owned_matching_account_and_feedback_inherits_actual_account(self):
        request = self.approved()
        self.post("/publish-records", {k: v for k, v in request.items() if k != "account_id"}, 422)
        other = self.post("/platform-accounts", {"platform": "rednote", "platform_user_id": "second-account"})
        wrong_platform = self.post("/platform-accounts", {"platform": "other", "platform_user_id": "account"})
        self.post("/publish-records", {**request, "account_id": wrong_platform["id"]}, 409)
        record = self.post("/publish-records", request)
        self.post(f"/publish-records/{record['id']}/metrics", {"metrics": {"likes": 1}}, 409)
        published = {"publish_status": "published", "platform_post_id": "post", "account_id": other["id"]}
        self.post(f"/publish-records/{record['id']}/result", published, 409)
        self.post(f"/publish-records/{record['id']}/result", {**published, "account_id": self.account_id}, 200)
        feedback = self.post(f"/publish-records/{record['id']}/metrics", {"metrics": {"likes": 10, "impressions": 100}})
        self.assertEqual(feedback["account_id"], self.account_id)
        self.assertEqual(feedback["publish_record_id"], record["id"])
        self.assertEqual(feedback["source"], "manual")
        for payload in ({"metrics": {"likes": -1}}, {"metrics": {"likes": True}}, {"metrics": {}},
                        {"metrics": {"likes": 1}, "account_id": other["id"]}, {"metrics": {"likes": 1}, "user_id": 999}):
            self.post(f"/publish-records/{record['id']}/metrics", payload, 422)
        self.register_second_user()
        self.assertEqual(self.client.get(f"/platform-accounts/{self.account_id}").status_code, 404)
        self.assertEqual(self.client.get(f"/publish-records/{record['id']}/metrics").status_code, 404)
        self.post(f"/publish-records/{record['id']}/metrics", {"metrics": {"likes": 100}}, 404)
        self.product_id = self.post("/products", {"name": "Second user product"})["id"]
        own_request = self.approved()
        self.post("/publish-records", own_request, 404)

    def test_legacy_publish_without_platform_account_cannot_accept_feedback(self):
        values = self.approved()
        with Session(self.engine) as db:
            record = PublishRecord(task_id=values["task_id"], content_id=values["content_id"],
                                    platform="rednote", publish_status="published", account_id=None)
            db.add(record)
            db.commit()
            record_id = record.id
        self.post(f"/publish-records/{record_id}/metrics", {"metrics": {"likes": 1}}, 409)
        with Session(self.engine) as db:
            self.assertEqual(db.scalar(select(func.count()).select_from(PublishMetrics)), 0)


if __name__ == "__main__":
    unittest.main()
