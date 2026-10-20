"""真实 LangGraph 配合隔离业务数据库验证人工审核和恢复流程。"""
from contextlib import contextmanager
import json
from threading import Lock
import unittest
from unittest.mock import patch

from langgraph.checkpoint.memory import InMemorySaver
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from integrations.llm.deepseek import DeepSeekTimeoutError
from models.content import Content
from models.task import Task
from models.workflow import TaskWorkflow
from models.rednote import RednoteCase, RednoteAnalysis
from models.rules import PlatformRule
from schemas.rules import RuleAIDraft
from services import content_generation
from services.exceptions import ConflictError
from workflows import nodes
from workflows.persistence import _serializer
import test_business_api as api_tests
from test_collection_api import VALID_ANALYSIS


class WorkflowTest(unittest.TestCase):
    tearDown = api_tests.BusinessAPITest.tearDown
    post = api_tests.BusinessAPITest.post
    review = api_tests.BusinessAPITest.review
    status = api_tests.BusinessAPITest.status
    register_second_user = api_tests.BusinessAPITest.register_second_user
    generation_mocks = api_tests.BusinessAPITest.generation_mocks

    def setUp(self):
        api_tests.BusinessAPITest.setUp(self)
        with Session(self.engine) as db:
            db.add(PlatformRule(platform_name="rednote", content="No unsupported product claims"))
            db.commit()
        rules_patch = patch("agents.rule_agent.check_content", return_value=RuleAIDraft(risks=[]))
        self.rule_agent = rules_patch.start()
        self.addCleanup(rules_patch.stop)
        self.saver = InMemorySaver(serde=_serializer())
        self.lock = Lock()

        @contextmanager
        def checkpointer(task_id, *, exclusive=False):
            if exclusive and not self.lock.acquire(blocking=False):
                raise ConflictError("Workflow is already running")
            try:
                yield self.saver
            finally:
                if exclusive:
                    self.lock.release()

        persistence_patch = patch("services.workflow.persistence.open_checkpointer", side_effect=checkpointer)
        persistence_patch.start()
        self.addCleanup(persistence_patch.stop)
        rag_patch = patch("services.content_generation.retriever.retrieve", return_value=self.generation_mocks())
        self.retrieve = rag_patch.start()
        self.addCleanup(rag_patch.stop)
        self.draft = {"title": "通勤音乐", "body": "通勤路上听听音乐。", "tags": ["通勤", "耳机"]}
        model_patch = patch("integrations.llm.deepseek.generate_text", return_value=json.dumps(self.draft))
        self.model = model_patch.start()
        self.addCleanup(model_patch.stop)
        self.task = self.post("/tasks", {"product_id": self.product_id, "task_type": "generate",
                                        "input_text": "写一篇通勤文案", "platform_name": "rednote"})
        self.base = f"/tasks/{self.task['id']}/workflow"

    def start(self, **options):
        return self.post(self.base + "/start", options)

    def test_start_uses_existing_service_saves_once_and_pauses_for_review(self):
        saved = self.start()
        self.assertEqual(saved["status"], "waiting_review")
        self.assertEqual(saved["task_status"], "pending_review")
        self.assertEqual(saved["next_nodes"], ["human_review"])
        self.assertEqual(saved["rule_check"]["status"], "no_risk_detected")
        self.assertEqual(saved["interrupt"]["content_id"], saved["content_id"])
        self.assertEqual(self.client.get(self.base).json()["run_id"], saved["run_id"])
        self.assertEqual(len(self.client.get("/contents").json()), 1)
        self.assertEqual(self.client.get("/publish-records").json(), [])
        self.retrieve.assert_called_once()
        self.model.assert_called_once()
        self.post(self.base + "/start", {}, 409)
        self.post(f"/tasks/{self.task['id']}/generate", {}, 409)
        retry = self.post(self.base + "/retry", None, 200)
        self.assertEqual(retry["status"], "waiting_review")
        self.model.assert_called_once()

    def test_approval_must_exist_in_database_and_ends_without_publishing(self):
        saved = self.start()
        self.post(self.base + "/resume", {"action": "review"}, 409)
        self.post(self.base + "/resume", {"approved": True}, 422)
        self.review(self.task["id"], saved["content_id"])
        result = self.post(self.base + "/resume", {}, 200)
        self.assertEqual(result["status"], "approved")
        self.assertEqual(result["task_status"], "approved")
        self.assertEqual(result["next_nodes"], [])
        self.assertIsNone(result["interrupt"])
        self.assertEqual(self.client.get("/publish-records").json(), [])
        self.post(self.base + "/resume", {}, 409)
        self.model.assert_called_once()

    def test_selected_analysis_and_prompt_are_passed_to_existing_generation_service(self):
        prompt = self.post("/prompts", {"platform": "rednote", "name": "Natural", "prompt_content": "使用自然的短句"})
        with Session(self.engine) as db:
            case = RednoteCase(user_id=self.user_id, name="Case", body="Foreign product claims",
                               link="https://www.xiaohongshu.com/explore/test")
            db.add(case)
            db.flush()
            analysis = RednoteAnalysis(cases_id=case.id, content="Foreign product claims", result=VALID_ANALYSIS,
                                       model="deepseek-flash")
            db.add(analysis)
            db.flush()
            analysis_id = analysis.id
            db.commit()
        result = self.start(prompt_id=prompt["id"], analysis_ids=[analysis_id])
        self.assertEqual(result["status"], "waiting_review")
        inputs = json.loads(self.model.call_args.kwargs["user_prompt"])
        self.assertEqual(inputs["case_references"][0]["analysis_id"], analysis_id)
        self.assertEqual(inputs["custom_prompt"]["style_requirements"], "使用自然的短句")
        self.assertNotIn("Foreign product claims", self.model.call_args.kwargs["user_prompt"])
        self.retrieve.assert_called_once()
        self.model.assert_called_once()

    def test_rule_risks_are_advisory_and_manual_changes_still_create_new_review_version(self):
        self.post("/rules/custom", {"rule_type": "keyword", "keywords": ["通勤"], "targets": ["body"],
                                    "content": "改为其他使用场景"})
        first = self.start()
        self.assertEqual((first["status"], first["task_status"]), ("waiting_review", "pending_review"))
        self.assertEqual(first["rule_check"]["status"], "risk_detected")
        self.assertIn("review", first["interrupt"]["actions"])
        self.assertTrue(any("不能保证小红书审核通过" in n for n in first["notices"]))
        edited = self.client.patch(f"/contents/{first['content_id']}", json={"body": "工作之余听听音乐。"}).json()
        second = self.post(self.base + "/resume", {"action": "submit_changes"}, 200)
        self.assertEqual((second["status"], second["task_status"]), ("waiting_review", "pending_review"))
        self.assertEqual(second["content_id"], edited["id"])
        self.assertEqual(second["rule_check"]["status"], "no_risk_detected")
        self.assertEqual(self.client.get(f"/rules/checks/{first['rule_check']['id']}").json()["status"], "risk_detected")
        self.review(self.task["id"], edited["id"])
        self.assertEqual(self.post(self.base + "/resume", {}, 200)["status"], "approved")
        self.model.assert_called_once()

    def test_rule_ai_failure_pauses_for_recheck_without_losing_content(self):
        self.rule_agent.side_effect = DeepSeekTimeoutError("请求超时")
        first = self.start()
        self.assertEqual(first["status"], "waiting_review")
        self.assertEqual(first["rule_check"]["status"], "error")
        self.assertEqual(len(self.client.get("/contents").json()), 1)
        self.rule_agent.side_effect = None
        checked = self.post(self.base + "/resume", {"action": "recheck"}, 200)
        self.assertEqual(checked["status"], "waiting_review")
        self.assertEqual(checked["content_id"], first["content_id"])
        self.assertEqual(self.rule_agent.call_count, 2)
        self.model.assert_called_once()

    def test_changed_rules_invalidate_review_and_recheck_can_expose_new_risks(self):
        first = self.start()
        rule = self.post("/rules/custom", {"rule_type": "keyword", "keywords": ["通勤"], "targets": ["body"]})
        risk = self.post(self.base + "/resume", {"action": "recheck"}, 200)
        self.assertEqual(risk["status"], "waiting_review")
        self.assertEqual(risk["task_status"], "pending_review")
        response = self.client.put(f"/rules/custom/{rule['id']}", json={"content": "Disabled", "is_active": False})
        self.assertEqual(response.status_code, 200)
        clear = self.post(self.base + "/resume", {"action": "recheck"}, 200)
        self.assertEqual(clear["status"], "waiting_review")
        self.assertNotEqual(clear["rule_check"]["rules_fingerprint"], risk["rule_check"]["rules_fingerprint"])
        self.model.assert_called_once()

    def test_rejection_pauses_for_changes_and_regeneration_makes_new_version(self):
        first = self.start()
        self.review(self.task["id"], first["content_id"], status="rejected", comment="调整标题")
        rejected = self.post(self.base + "/resume", {}, 200)
        self.assertEqual(rejected["status"], "waiting_changes")
        self.assertEqual(rejected["task_status"], "rejected")
        self.post(self.base + "/resume", {}, 409)
        second = self.post(self.base + "/resume", {"action": "regenerate", "generation": {"top_k": 3}}, 200)
        self.assertEqual(second["status"], "waiting_review")
        self.assertEqual((first["content_version"], second["content_version"]), (1, 2))
        self.assertNotEqual(first["content_id"], second["content_id"])
        self.assertEqual(self.retrieve.call_args.kwargs["top_k"], 3)
        self.assertEqual(self.model.call_count, 2)
        self.review(self.task["id"], first["content_id"], expected=409)
        self.review(self.task["id"], second["content_id"])
        self.assertEqual(self.post(self.base + "/resume", {}, 200)["status"], "approved")

    def test_manual_changes_submit_latest_version_and_require_another_review(self):
        first = self.start()
        self.post(self.base + "/resume", {"action": "submit_changes"}, 409)
        edited = self.client.patch(f"/contents/{first['content_id']}", json={"body": "人工修改的正文"})
        self.assertEqual(edited.status_code, 201)
        self.assertEqual(self.client.get(f"/tasks/{self.task['id']}").json()["status"], "running")
        submitted = self.post(self.base + "/resume", {"action": "submit_changes"}, 200)
        self.assertEqual(submitted["status"], "waiting_review")
        self.assertEqual(submitted["task_status"], "pending_review")
        self.assertEqual(submitted["content_id"], edited.json()["id"])
        self.assertEqual(submitted["rule_check"]["content_id"], edited.json()["id"])
        self.review(self.task["id"], first["content_id"], expected=409)
        self.review(self.task["id"], submitted["content_id"])
        self.assertEqual(self.post(self.base + "/resume", {}, 200)["status"], "approved")
        self.model.assert_called_once()

    def test_model_failure_records_failed_task_and_graph_retry_resumes_generation(self):
        self.model.side_effect = DeepSeekTimeoutError("请求超时")
        self.post(self.base + "/start", {}, 503)
        failed = self.client.get(self.base).json()
        self.assertEqual((failed["status"], failed["task_status"]), ("failed", "failed"))
        self.assertEqual(failed["next_nodes"], ["generate_content"])
        self.assertEqual(self.client.get("/contents").json(), [])
        self.model.side_effect = None
        result = self.post(self.base + "/retry", None, 200)
        self.assertEqual(result["status"], "waiting_review")
        self.assertEqual(result["content_version"], 1)

    def test_replay_after_content_commit_does_not_generate_or_save_twice(self):
        original = content_generation.generate_and_save_content
        failed = False

        def after_commit(*args, **kwargs):
            nonlocal failed
            result = original(*args, **kwargs)
            if not failed:
                failed = True
                raise RuntimeError("private-provider-response")
            return result

        with patch.object(content_generation, "generate_and_save_content", side_effect=after_commit):
            self.post(self.base + "/start", {}, 503)
            result = self.post(self.base + "/retry", None, 200)
        self.assertEqual(result["status"], "waiting_review")
        self.assertEqual(len(self.client.get("/contents").json()), 1)
        self.model.assert_called_once()
        with Session(self.engine) as db:
            run = db.get(TaskWorkflow, self.task["id"])
            self.assertNotIn("private", run.error_message or "")
            content = db.scalar(select(Content))
            self.assertEqual(content.generation_key, f"{run.run_id}:0")
            self.assertEqual(content.generation_metadata["product_id"], self.product_id)

    def test_retry_recovers_running_attempt_left_by_a_terminated_process(self):
        self.model.side_effect = DeepSeekTimeoutError("请求超时")
        self.post(self.base + "/start", {}, 503)
        with Session(self.engine) as db:
            task = db.get(Task, self.task["id"])
            attempt = task.checkpoint["content_generation"]
            task.checkpoint = {**task.checkpoint, "content_generation": {**attempt, "status": "running"}}
            task.status = "running"
            db.commit()
        self.model.side_effect = None
        result = self.post(self.base + "/retry", None, 200)
        self.assertEqual(result["status"], "waiting_review")
        self.assertEqual(result["content_version"], 1)

    def test_failure_after_generation_retries_only_remaining_nodes(self):
        def fail_rules(state):
            raise RuntimeError("private-rule-error")

        with patch.object(nodes, "platform_rules", fail_rules):
            self.post(self.base + "/start", {}, 503)
        self.assertEqual(self.client.get(self.base).json()["next_nodes"], ["platform_rules"])
        result = self.post(self.base + "/retry", None, 200)
        self.assertEqual(result["status"], "waiting_review")
        self.assertEqual(result["content_version"], 1)
        self.model.assert_called_once()

    def test_rule_stage_retry_uses_latest_saved_version_without_regeneration(self):
        with patch.object(nodes, 'platform_rules', side_effect=RuntimeError('rule stage interrupted')):
            self.post(self.base + '/start', {}, 503)
        first = self.client.get('/contents').json()[0]
        edited = self.client.patch(f"/contents/{first['id']}", json={'body': '已保存的新版本'}).json()
        result = self.post(self.base + '/retry', None, 200)
        self.assertEqual((result['task_status'], result['status']), ('pending_review', 'waiting_review'))
        self.assertEqual(result['content_id'], edited['id'])
        self.assertEqual(result['interrupt']['content_version'], edited['version'])
        self.assertIsNone(result['rule_check'])
        self.model.assert_called_once()
        self.rule_agent.assert_not_called()
        self.review(self.task['id'], first['id'], expected=409)
        self.review(self.task['id'], edited['id'])
        self.assertEqual(self.post(self.base + '/resume', {}, 200)['status'], 'approved')

    def test_task_deletion_removes_checkpoint_and_content(self):
        result = self.start()
        with Session(self.engine) as db:
            run = db.get(TaskWorkflow, self.task['id'])
            from services.workflow import _config
            checkpoint_config = _config(run)
        self.assertIsNotNone(self.saver.get_tuple(checkpoint_config))
        self.assertEqual(self.client.delete(f"/tasks/{self.task['id']}").status_code, 204)
        self.assertIsNone(self.saver.get_tuple(checkpoint_config))
        self.assertEqual(self.client.get(f"/contents/{result['content_id']}").status_code, 404)

    def test_version_saved_during_rule_check_enters_review_without_old_risks(self):
        edited = None
        def change_during_check(*args, **kwargs):
            nonlocal edited
            first = self.client.get('/contents').json()[0]
            response = self.client.patch(f"/contents/{first['id']}", json={'body': '检查期间保存的修改'})
            self.assertEqual(response.status_code, 201)
            edited = response.json()
            return RuleAIDraft(risks=[])
        self.rule_agent.side_effect = change_during_check
        result = self.start()
        self.assertEqual(result['content_id'], edited['id'])
        self.assertEqual(result['task_status'], 'pending_review')
        self.assertEqual(result['status'], 'waiting_review')
        self.assertIsNone(result['rule_check'])
        self.assertTrue(any('规则检查未完成' in text for text in result['notices']))

    def test_owner_and_preflight_checks_precede_any_generation(self):
        self.assertEqual(self.client.get(self.base).status_code, 404)
        self.post(self.base + "/start", {"analysis_ids": [999]}, 404)
        with Session(self.engine) as db:
            self.assertEqual(db.scalar(select(func.count()).select_from(TaskWorkflow)), 0)
            self.assertEqual(db.get(Task, self.task["id"]).status, "pending")
        self.retrieve.assert_not_called()
        self.start()
        self.register_second_user()
        for suffix in ("",):
            self.assertEqual(self.client.get(self.base + suffix).status_code, 404)
        self.post(self.base + "/start", {}, 404)
        self.post(self.base + "/resume", {}, 404)
        self.post(self.base + "/retry", None, 404)

    def test_cancelled_task_cannot_resume_and_duplicate_run_is_blocked(self):
        def concurrent(**kwargs):
            self.post(self.base + "/retry", None, 409)
            return json.dumps(self.draft)

        self.model.side_effect = concurrent
        started = self.start()
        self.status(self.task["id"], "cancelled")
        response = self.client.get(self.base)
        self.assertEqual(response.status_code, 200)
        cancelled = response.json()
        self.assertEqual((cancelled["task_status"], cancelled["status"], cancelled["stage"]),
                         ("cancelled", "cancelled", "cancelled"))
        self.assertEqual(cancelled["next_nodes"], [])
        self.assertIsNone(cancelled["interrupt"])
        self.assertEqual(cancelled["content_id"], started["content_id"])
        self.assertEqual(cancelled["content_version"], started["content_version"])
        with Session(self.engine) as db:
            self.assertEqual(db.get(TaskWorkflow, self.task["id"]).status, "waiting_review")
            self.assertEqual(db.get(Content, started["content_id"]).body, self.draft["body"])
        self.post(self.base + "/resume", {"action": "regenerate"}, 409)
        self.assertEqual(self.model.call_count, 1)

    def test_cancelled_task_status_overrides_previous_workflow_failure_in_response(self):
        self.model.side_effect = DeepSeekTimeoutError("请求超时")
        self.post(self.base + "/start", {}, 503)
        self.status(self.task["id"], "cancelled")
        response = self.client.get(self.base)
        self.assertEqual(response.status_code, 200)
        cancelled = response.json()
        self.assertEqual((cancelled["status"], cancelled["stage"]), ("cancelled", "cancelled"))
        self.assertEqual(cancelled["next_nodes"], [])
        self.assertIsNone(cancelled["interrupt"])
        self.assertTrue(cancelled["error_message"])


if __name__ == "__main__":
    unittest.main()
