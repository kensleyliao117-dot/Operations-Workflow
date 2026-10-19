"""模型白名单、聊天配置隔离与不同供应商 HTTP 适配测试。"""
import json
import unittest
from unittest.mock import patch

import httpx

from agents import analysis_agent
from integrations.llm import analysis_models, deepseek
from services.exceptions import InvalidInputError, ServiceUnavailableError
from test_collection_api import VALID_ANALYSIS


class AnalysisModelsTest(unittest.TestCase):
    def setUp(self):
        for target, value in [("config.ANALYSIS_MODELS_JSON", ""), ("config.QWEN_CHAT_MODEL", ""),
                              ("config.MODEL_DEEPSEEK", "deepseek-flash"),
                              ("config.DEEPSEEK_API_KEY", "deepseek-test-key"),
                              ("config.DASHSCOPE_API_KEY", "qwen-test-key"),
                              ("config.QWEN_CHAT_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1")]:
            patcher = patch(target, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_chat_profiles_do_not_reuse_embedding_model_and_custom_whitelist_selects_model(self):
        with patch("config.MODEL_DASHSCOPE", "text-embedding-v3"):
            self.assertEqual([p.id for p in analysis_models.model_profiles()], ["deepseek"])
        with patch("config.QWEN_CHAT_MODEL", "configured-qwen-chat"):
            self.assertEqual(analysis_models.select_model("qwen").model, "configured-qwen-chat")
        settings = json.dumps([{"id": "alternative", "provider": "deepseek", "model": "other-configured-model"}])
        with patch("config.ANALYSIS_MODELS_JSON", settings):
            profile = analysis_models.select_model(None)
            self.assertEqual(profile.id, "alternative")
            with patch.object(deepseek, "generate_text", return_value="ok") as call:
                self.assertEqual(analysis_models.generate_text(profile, system_prompt="system", user_prompt="user"), "ok")
                self.assertEqual(call.call_args.kwargs["model"], "other-configured-model")

    def test_invalid_registry_and_unknown_profile_fail_without_http(self):
        values = ["private-config", "{}", "[]", '[{"id":"x"}]', json.dumps([
            {"id": "user:1", "provider": "qwen", "model": "chat"}]), json.dumps([
            {"id": "x", "provider": "unknown", "model": "model"}]), json.dumps([
            {"id": "x", "provider": "qwen", "model": "text-embedding-v3"}]), json.dumps([
            {"id": "x", "provider": "qwen", "model": "chat", "base_url": "private-secret"}])]
        with patch.object(analysis_models.httpx, "Client") as client:
            for value in values:
                with patch("config.ANALYSIS_MODELS_JSON", value), self.assertRaises(ServiceUnavailableError) as caught:
                    analysis_models.model_profiles()
                self.assertNotIn("private", str(caught.exception))
            with self.assertRaises(InvalidInputError):
                analysis_models.select_model("unknown")
            client.assert_not_called()

    def test_qwen_calls_chat_endpoint_with_its_own_model_and_key(self):
        requests = []

        def handler(request):
            requests.append(request)
            return httpx.Response(200, json={"choices": [{"finish_reason": "stop", "message": {"content": "  result  "}}]})

        http = httpx.Client(transport=httpx.MockTransport(handler))
        profile = analysis_models.AnalysisModel("qwen", "qwen", "configured-qwen-chat")
        with patch.object(analysis_models.httpx, "Client", return_value=http):
            result = analysis_models.generate_text(profile, system_prompt="system", user_prompt="user")
        self.assertEqual(result, "result")
        self.assertTrue(http.is_closed)
        request = requests[0]
        self.assertEqual(str(request.url), "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions")
        self.assertEqual(request.headers["Authorization"], "Bearer qwen-test-key")
        payload = json.loads(request.content)
        self.assertEqual(payload["model"], "configured-qwen-chat")
        self.assertEqual(payload["messages"], [{"role": "system", "content": "system"}, {"role": "user", "content": "user"}])
        self.assertFalse(payload["enable_thinking"])

    def test_qwen_errors_are_sanitized_and_not_retried(self):
        profile = analysis_models.AnalysisModel("qwen", "qwen", "configured-qwen-chat")
        for mode in ("timeout", "network", "status", "empty", "invalid", "incomplete"):
            calls = []

            def handler(request):
                calls.append(request)
                if mode == "timeout":
                    raise httpx.ReadTimeout("qwen-test-key private-response", request=request)
                if mode == "network":
                    raise httpx.ConnectError("qwen-test-key private-response", request=request)
                if mode == "status":
                    return httpx.Response(429, text="qwen-test-key private-response")
                if mode == "empty":
                    return httpx.Response(200, content=b"")
                if mode == "invalid":
                    return httpx.Response(200, json={"error": "qwen-test-key private-response"})
                return httpx.Response(200, json={"choices": [{"finish_reason": "length", "message": {"content": "partial"}}]})

            http = httpx.Client(transport=httpx.MockTransport(handler))
            with patch.object(analysis_models.httpx, "Client", return_value=http), self.assertRaises(ServiceUnavailableError) as caught:
                analysis_models.generate_text(profile, system_prompt="system", user_prompt="user")
            self.assertEqual(len(calls), 1)
            self.assertNotIn("qwen-test-key", str(caught.exception))
            self.assertNotIn("private-response", str(caught.exception))
            self.assertTrue(http.is_closed)

    def test_qwen_missing_credentials_and_invalid_url_do_not_send_requests(self):
        profile = analysis_models.AnalysisModel("qwen", "qwen", "configured-qwen-chat")
        with patch.object(analysis_models.httpx, "Client") as client:
            with patch("config.DASHSCOPE_API_KEY", ""), self.assertRaises(ServiceUnavailableError):
                analysis_models.generate_text(profile, system_prompt="system", user_prompt="user")
            with patch("config.QWEN_CHAT_BASE_URL", "http://bad.example"), self.assertRaises(ServiceUnavailableError):
                analysis_models.generate_text(profile, system_prompt="system", user_prompt="user")
            client.assert_not_called()

    def test_history_requires_new_insights_and_does_not_accept_incomplete_analysis(self):
        with patch.object(analysis_models, "generate_text", return_value=json.dumps(VALID_ANALYSIS)):
            with self.assertRaises(ServiceUnavailableError):
                analysis_agent.analyze_case({"body": "source"}, previous_analysis={"version": 1})
        with patch.object(analysis_models, "generate_text", return_value=json.dumps({
                **VALID_ANALYSIS, "new_insights": ["没有新增可靠发现"]})):
            result = analysis_agent.analyze_case({"body": "source"}, previous_analysis={"version": 1})
            self.assertEqual(result.new_insights, ["没有新增可靠发现"])

    def test_deepseek_client_preserves_explicit_model_override(self):
        requests = []

        def handler(request):
            requests.append(request)
            return httpx.Response(200, json={"choices": [{"message": {"content": "result"}}]})

        with httpx.Client(transport=httpx.MockTransport(handler)) as http:
            client = deepseek.DeepSeekClient(http_client=http, model="explicit-model")
            client.generate_text("system", "user")
        self.assertEqual(json.loads(requests[0].content)["model"], "explicit-model")


if __name__ == "__main__":
    unittest.main()
