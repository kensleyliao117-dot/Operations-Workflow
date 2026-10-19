"""案例搜索、反馈修订、历史参考及多模型版本比较的接口测试。"""
import json
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import app
from integrations.llm.deepseek import DeepSeekTimeoutError
from models.rednote import RednoteAnalysis, RednoteCase
import test_collection_api as collection_tests


class AnalysisVersionsTest(unittest.TestCase):
    setUp = collection_tests.CollectionAPITest.setUp
    setUpBase = collection_tests.CollectionAPITest.setUpBase
    tearDown = collection_tests.CollectionAPITest.tearDown
    post = collection_tests.CollectionAPITest.post
    register_second_user = collection_tests.CollectionAPITest.register_second_user

    def initial(self):
        collected = self.post("/rednote/collect", {"analyze": True})
        return collected["case"], collected["analysis"]

    def revision_output(self, **changes):
        value = {**collection_tests.VALID_ANALYSIS, "hook": "通过场景问题引入",
                 "new_insights": ["标题的信息差主要来自场景冲突，而非产品参数"], **changes}
        self.model.return_value = json.dumps(value)
        return value

    def feedback(self, analysis):
        return self.post(f"/rednote/analyses/{analysis['id']}/feedback",
                          {"content": "原分析忽略了标题与正文的情绪转折，请补充并区分推测。"})

    def test_search_reports_history_and_duplicate_collection_skips_model(self):
        case, analysis = self.initial()
        rows = self.client.get("/rednote/cases/search", params={"q": "耳机"}).json()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["analysis_count"], 1)
        self.assertEqual(rows[0]["latest_analysis_id"], analysis["id"])
        self.assertEqual(rows[0]["latest_analysis_version"], 1)
        self.assertTrue(rows[0]["already_analyzed"])
        self.assertIn("已有", rows[0]["history_hint"])
        rows = self.client.get("/rednote/cases/search", params={"url": self.url + "?xsec_token=secret"}).json()
        self.assertEqual(rows[0]["id"], case["id"])
        self.assertEqual(self.client.get("/rednote/cases/search", params={"q": "%"}).json(), [])
        self.assertEqual(self.client.get("/rednote/cases/search", params={"url": "https://example.com"}).status_code, 422)
        self.model.reset_mock()
        duplicate = self.post("/rednote/collect", {"analyze": True})
        self.assertEqual(duplicate["analysis_status"], "existing")
        self.assertTrue(duplicate["case_already_saved"])
        self.assertEqual(duplicate["analysis"]["id"], analysis["id"])
        self.assertEqual(duplicate["analysis_count"], 1)
        self.model.assert_not_called()
        self.revision_output()
        renewed = self.post("/rednote/collect", {"analyze": True, "reanalyze": True})
        self.assertEqual(renewed["analysis_status"], "completed")
        self.assertEqual(renewed["analysis"]["version"], 2)
        self.assertEqual(renewed["analysis_count"], 2)

    def test_feedback_is_persisted_and_revision_preserves_original_and_guides_next_analysis(self):
        case, first = self.initial()
        feedback = self.feedback(first)
        self.assertEqual(self.client.get(f"/rednote/analyses/{first['id']}/feedback").json()[0]["id"], feedback["id"])
        value = self.revision_output()
        revised = self.post(f"/rednote/analyses/{first['id']}/revise", {"feedback_ids": [feedback["id"]]})
        self.assertEqual((first["version"], revised["version"]), (1, 2))
        self.assertEqual(revised["parent_analysis_id"], first["id"])
        self.assertEqual(revised["revision_type"], "feedback")
        self.assertEqual(revised["feedback_ids"], [feedback["id"]])
        self.assertEqual(revised["new_insights"], value["new_insights"])
        self.assertNotIn("new_insights", revised["result"])
        inputs = json.loads(self.model.call_args.kwargs["user_prompt"])
        self.assertEqual(inputs["previous_analysis"]["analysis_id"], first["id"])
        self.assertEqual(inputs["user_feedback"][0]["content"], feedback["content"])
        self.assertEqual(self.client.get(f"/rednote/analyses/{first['id']}").json()["result"], first["result"])
        latest = self.post(f"/rednote/cases/{case['id']}/analyze", {})
        inputs = json.loads(self.model.call_args.kwargs["user_prompt"])
        self.assertEqual(inputs["previous_analysis"]["analysis_id"], revised["id"])
        self.assertEqual(latest["version"], 3)
        self.assertEqual(latest["parent_analysis_id"], revised["id"])
        self.assertEqual(latest["revision_type"], "reanalysis")
        self.assertIn("不能为追求新意编造结论", self.model.call_args.kwargs["system_prompt"])

    def test_failed_revision_keeps_feedback_and_retry_does_not_skip_version(self):
        _, first = self.initial()
        feedback = self.feedback(first)
        endpoint = f"/rednote/analyses/{first['id']}/revise"
        self.model.side_effect = DeepSeekTimeoutError("请求超时")
        self.post(endpoint, {"feedback_ids": [feedback["id"]]}, 503)
        self.assertEqual(len(self.client.get("/rednote/analyses").json()), 1)
        self.assertEqual(len(self.client.get(f"/rednote/analyses/{first['id']}/feedback").json()), 1)
        self.model.side_effect = None
        self.revision_output()
        second = self.post(endpoint, {"feedback_ids": [feedback["id"]]})
        self.assertEqual(second["version"], 2)

    def test_model_selection_and_comparison_use_same_source_and_explicit_base(self):
        case, first = self.initial()
        settings = json.dumps([{"id": "fast", "provider": "deepseek", "model": "deepseek-flash"},
                               {"id": "alternate", "provider": "deepseek", "model": "configured-other-model"}])
        with patch("config.ANALYSIS_MODELS_JSON", settings):
            models = self.client.get("/rednote/analysis-models").json()
            self.assertEqual([m["id"] for m in models], ["fast", "alternate"])
            self.assertNotIn("key", json.dumps(models))
            self.revision_output(hook="Model A hook")
            a = self.post(f"/rednote/cases/{case['id']}/analyze", {"model_id": "fast", "base_analysis_id": first["id"]})
            self.revision_output(hook="Model B hook")
            b = self.post(f"/rednote/cases/{case['id']}/analyze", {"model_id": "alternate", "base_analysis_id": first["id"]})
            self.assertEqual(self.model.call_args.kwargs["model"], "configured-other-model")
        self.assertEqual((a["version"], b["version"]), (2, 3))
        self.assertEqual(a["model"], "deepseek-flash")
        self.assertEqual(b["model_profile"], "alternate")
        comparison = self.client.get("/rednote/analyses/compare", params={"left_id": a["id"], "right_id": b["id"]}).json()
        self.assertTrue(comparison["same_source"])
        self.assertTrue(comparison["same_parent"])
        self.assertTrue(comparison["same_feedback"])
        hook = next(d for d in comparison["differences"] if d["field"] == "hook")
        self.assertEqual((hook["left"], hook["right"]), ("Model A hook", "Model B hook"))
        self.assertEqual(comparison["left"]["model"], a["model"])
        self.assertEqual(comparison["right"]["model"], b["model"])

    def test_independent_analysis_omits_history_and_source_change_is_visible(self):
        case, first = self.initial()
        self.feedback(first)
        with Session(self.engine) as db:
            db.get(RednoteCase, case["id"]).body = "Changed source text"
            db.commit()
        self.model.return_value = json.dumps(collection_tests.VALID_ANALYSIS)
        independent = self.post(f"/rednote/cases/{case['id']}/analyze", {"use_history": False})
        inputs = json.loads(self.model.call_args.kwargs["user_prompt"])
        self.assertIsNone(inputs["previous_analysis"])
        self.assertEqual(inputs["user_feedback"], [])
        self.assertIsNone(independent["parent_analysis_id"])
        compare = self.client.get("/rednote/analyses/compare", params={"left_id": first["id"], "right_id": independent["id"]}).json()
        self.assertFalse(compare["same_source"])

    def test_concurrent_analysis_version_is_not_overwritten(self):
        case, first = self.initial()
        value = {**collection_tests.VALID_ANALYSIS, "new_insights": ["No new reliable findings"]}

        def concurrent(**kwargs):
            with Session(self.engine) as db:
                db.add(RednoteAnalysis(cases_id=case["id"], version=2, content="Concurrent manual version"))
                db.commit()
            return json.dumps(value)

        self.model.side_effect = concurrent
        self.post(f"/rednote/cases/{case['id']}/analyze", {}, 409)
        with Session(self.engine) as db:
            self.assertEqual(db.scalar(select(func.count()).select_from(RednoteAnalysis)), 2)
            self.assertEqual(db.scalar(select(RednoteAnalysis).where(RednoteAnalysis.version == 2)).content,
                             "Concurrent manual version")

    def test_reference_feedback_and_comparison_permissions_are_checked_before_ai(self):
        case, first = self.initial()
        feedback = self.feedback(first)
        other_case = self.post("/rednote/cases", {"name": "Other case", "link": self.url + "other", "body": "Other body"})
        other_analysis = self.post("/rednote/analyses", {"cases_id": other_case["id"], "content": "Other analysis"})
        other_feedback = self.feedback(other_analysis)
        self.model.reset_mock()
        self.post(f"/rednote/cases/{case['id']}/analyze", {"base_analysis_id": other_analysis["id"]}, 422)
        self.post(f"/rednote/cases/{case['id']}/analyze", {"model_id": "unknown"}, 422)
        self.post(f"/rednote/cases/{case['id']}/analyze", {"use_history": False, "base_analysis_id": first["id"]}, 422)
        self.post(f"/rednote/analyses/{first['id']}/revise", {"feedback_ids": [other_feedback["id"]]}, 422)
        for ids in ([], [True], [feedback["id"], feedback["id"]]):
            self.post(f"/rednote/analyses/{first['id']}/revise", {"feedback_ids": ids}, 422)
        self.assertEqual(self.client.get("/rednote/analyses/compare", params={"left_id": first["id"], "right_id": other_analysis["id"]}).status_code, 422)
        self.register_second_user()
        self.assertEqual(self.client.get("/rednote/cases/search", params={"q": "耳机"}).json(), [])
        self.post(f"/rednote/analyses/{first['id']}/feedback", {"content": "Foreign feedback"}, 404)
        self.assertEqual(self.client.get(f"/rednote/analyses/{first['id']}/feedback").status_code, 404)
        self.post(f"/rednote/analyses/{first['id']}/revise", {"feedback_ids": [feedback["id"]]}, 404)
        self.assertEqual(self.client.get("/rednote/analyses/compare", params={"left_id": first["id"], "right_id": first["id"]}).status_code, 404)
        self.model.assert_not_called()

    def test_revision_endpoints_are_local_only_and_case_creation_normalizes_urls(self):
        case, first = self.initial()
        feedback = self.feedback(first)
        self.post("/rednote/cases", {"name": "Duplicate", "link": self.url + "?xsec_token=secret"}, 409)
        remote = TestClient(app, base_url="http://127.0.0.1", client=("203.0.113.1", 40000))
        self.addCleanup(remote.close)
        response = remote.post(f"/rednote/analyses/{first['id']}/revise", json={"feedback_ids": [feedback["id"]]},
                               headers={"Authorization": f"Bearer {self.token}"})
        self.assertEqual(response.status_code, 403)


if __name__ == "__main__":
    unittest.main()
