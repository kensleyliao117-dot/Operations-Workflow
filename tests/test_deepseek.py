import json
import unittest
from unittest.mock import patch

import httpx

from config import ConfigurationError
from integrations.llm.deepseek import DeepSeekClient, DeepSeekError, DeepSeekTimeoutError, generate_text
from services.exceptions import InvalidInputError


class DeepSeekTest(unittest.TestCase):
    def setUp(self):
        for target, value in [("config.DEEPSEEK_API_KEY", "test-secret-key"),
                              ("config.URL_DEEPSEEK", "https://api.deepseek.com"),
                              ("config.MODEL_DEEPSEEK", "deepseek-flash")]:
            patcher = patch(target, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.requests = []
        self.custom_handler = None

        def handler(request):
            self.requests.append(request)
            if self.custom_handler:
                return self.custom_handler(request)
            return httpx.Response(200, json={"choices": [{"finish_reason": "stop", "message": {
                "role": "assistant", "content": "  生成的正文\n第二段  ", "reasoning_content": "不应返回的思考内容",
            }}]})

        self.http = httpx.Client(transport=httpx.MockTransport(handler))
        self.addCleanup(self.http.close)
        self.client = DeepSeekClient(http_client=self.http)

    def test_request_roles_model_auth_and_plain_text(self):
        result = self.client.generate_text("你是文案助手", "为耳机写标题")
        self.assertEqual(result, "生成的正文\n第二段")
        request = self.requests[0]
        self.assertEqual(str(request.url), "https://api.deepseek.com/chat/completions")
        self.assertEqual(request.headers["authorization"], "Bearer test-secret-key")
        self.assertEqual(json.loads(request.content), {"model": "deepseek-flash", "messages": [
            {"role": "system", "content": "你是文案助手"}, {"role": "user", "content": "为耳机写标题"},
        ], "stream": False})
        self.assertEqual(request.extensions["timeout"]["read"], 60.0)

    def test_missing_key_invalid_url_model_and_timeout_fail_without_request(self):
        for target, value in [("config.DEEPSEEK_API_KEY", " "), ("config.MODEL_DEEPSEEK", ""),
                              ("config.URL_DEEPSEEK", "http://api.deepseek.com"),
                              ("config.URL_DEEPSEEK", "https://user:private-key@api.deepseek.com"),
                              ("config.URL_DEEPSEEK", "https://api.deepseek.com/chat/completions")]:
            with patch(target, value), self.assertRaises(ConfigurationError) as caught:
                DeepSeekClient(http_client=self.http)
            self.assertNotIn("private-key", str(caught.exception))
        for timeout in (0, -1, True, float("nan"), float("inf")):
            with self.assertRaises(ConfigurationError):
                DeepSeekClient(http_client=self.http, timeout=timeout)
        self.assertEqual(self.requests, [])

    def test_prompt_validation_precedes_network(self):
        for system, user in [("", "ok"), ("ok", "   "), (None, "ok"), ("ok", 3)]:
            with self.assertRaises(InvalidInputError):
                self.client.generate_text(system, user)
        self.assertEqual(self.requests, [])

    def test_timeout_and_network_error_are_sanitized_and_not_retried(self):
        for kind, expected in [(httpx.ReadTimeout, DeepSeekTimeoutError),
                               (httpx.ConnectTimeout, DeepSeekTimeoutError),
                               (httpx.ConnectError, DeepSeekError)]:
            self.requests.clear()
            def fail(request):
                raise kind("test-secret-key private prompt", request=request)
            self.custom_handler = fail
            with self.assertRaises(expected) as caught:
                self.client.generate_text("private prompt", "test")
            self.assertNotIn("test-secret-key", str(caught.exception))
            self.assertNotIn("private prompt", str(caught.exception))
            self.assertEqual(len(self.requests), 1)

    def test_api_errors_and_redirect_are_sanitized_and_not_followed(self):
        for status in (302, 400, 401, 402, 403, 404, 422, 429, 500, 502, 503):
            self.requests.clear()
            self.custom_handler = lambda request, code=status: httpx.Response(code,
                headers={"Location": "https://other.example"}, text="private response test-secret-key")
            with self.assertRaises(DeepSeekError) as caught:
                self.client.generate_text("system", "user")
            self.assertIn(str(status), str(caught.exception))
            self.assertNotIn("private response", str(caught.exception))
            self.assertNotIn("test-secret-key", str(caught.exception))
            self.assertEqual(len(self.requests), 1)

    def test_empty_malformed_and_non_text_responses(self):
        payloads = [None, [], {}, {"error": {"message": "private response"}}, {"choices": []},
                    {"choices": [None]}, {"choices": [{}]}, {"choices": [{"message": None}]},
                    {"choices": [{"finish_reason": [], "message": {"content": "text"}}]}]
        payloads.extend({"choices": [{"message": {"content": value}}]} for value in (None, "", " \n ", 4, []))
        for payload in payloads:
            self.custom_handler = lambda request, body=payload: httpx.Response(200, json=body)
            with self.assertRaises(DeepSeekError):
                self.client.generate_text("system", "user")
        for body in (b"", b" \n ", b"not json"):
            self.custom_handler = lambda request, data=body: httpx.Response(200, content=data)
            with self.assertRaises(DeepSeekError):
                self.client.generate_text("system", "user")

    def test_incomplete_or_interrupted_generation_is_not_returned_as_success(self):
        for reason in ("length", "content_filter", "tool_calls", "insufficient_system_resource", "aborted"):
            self.custom_handler = lambda request, why=reason: httpx.Response(200, json={"choices": [
                {"finish_reason": why, "message": {"content": "partial"}}]})
            with self.assertRaises(DeepSeekError):
                self.client.generate_text("system", "user")

    def test_borrowed_http_client_remains_open(self):
        with self.client as client:
            client.generate_text("system", "user")
        self.assertFalse(self.http.is_closed)

    def test_convenience_function_closes_its_http_client_even_on_failure(self):
        self.custom_handler = lambda request: httpx.Response(500)
        with patch("integrations.llm.deepseek.httpx.Client", return_value=self.http):
            with self.assertRaises(DeepSeekError):
                generate_text("system", "user")
        self.assertTrue(self.http.is_closed)


if __name__ == "__main__":
    unittest.main()
