"""独立模型凭据、实际调用路由及自定义出站地址隔离。"""
import json
import socket
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx
from cryptography.fernet import Fernet
from sqlalchemy import select
from sqlalchemy.orm import Session

from integrations.llm import analysis_models as adapters, compatible
from models.analysis_model import UserAnalysisModel
from schemas.analysis_model import AnalysisModelWrite
from services import analysis_models, content_generation, model_secrets
from services.exceptions import InvalidInputError, NotFoundError, ServiceUnavailableError
import test_business_api as business
import test_content_generation as generation_tests


CUSTOM = {"name": "Independent text model", "provider": "Another supplier", "model": "vendor/chat-model",
          "base_url": "https://api.vendor.example/v1", "api_key": "test-only-personal-key", "is_default": True}


class ModelConnectionsTest(unittest.TestCase):
    tearDown = business.BusinessAPITest.tearDown
    post = business.BusinessAPITest.post
    register_second_user = business.BusinessAPITest.register_second_user

    def setUp(self):
        business.BusinessAPITest.setUp(self)
        key = patch("config.MODEL_CREDENTIALS_KEY", Fernet.generate_key().decode())
        key.start()
        self.addCleanup(key.stop)

    def test_save_encrypts_key_never_returns_it_and_does_not_edit_environment_file(self):
        env_file = Path(__file__).resolve().parents[1] / ".env"
        before = env_file.read_bytes() if env_file.exists() else None
        profile = self.post("/analysis-models", CUSTOM)
        self.assertTrue(profile["ready"])
        self.assertEqual(profile["credential_source"], "user")
        self.assertNotIn("api_key", profile)
        self.assertNotIn(CUSTOM["api_key"], self.client.get("/analysis-models").text)
        with Session(self.engine) as db:
            row = db.get(UserAnalysisModel, profile["config_id"])
            self.assertNotIn(CUSTOM["api_key"], row.encrypted_api_key)
            self.assertEqual(model_secrets.decrypt_key(row.encrypted_api_key, self.user_id), CUSTOM["api_key"])
            with self.assertRaises(ServiceUnavailableError):
                model_secrets.decrypt_key(row.encrypted_api_key, self.user_id + 1)
            db.info["user_id"] = self.user_id
            selected = analysis_models.select_model(db, None)
            self.assertEqual(selected.id, profile["id"])
            self.assertNotIn(CUSTOM["api_key"], repr(selected))
            self.assertNotIn(CUSTOM["api_key"], json.dumps(selected.snapshot()))
        self.assertEqual(env_file.read_bytes() if env_file.exists() else None, before)

    def test_edit_retains_key_url_change_requires_replacement_and_default_switch_is_unique(self):
        first = self.post("/analysis-models", CUSTOM)
        data = {**CUSTOM, "api_key": None, "model": "vendor/new-model"}
        self.assertEqual(self.client.put(f"/analysis-models/{first['config_id']}", json=data).status_code, 200)
        changed = {**data, "base_url": "https://different.vendor.example/v1"}
        self.assertEqual(self.client.put(f"/analysis-models/{first['config_id']}", json=changed).status_code, 422)
        with Session(self.engine) as db:
            row = db.get(UserAnalysisModel, first["config_id"])
            self.assertEqual(row.base_url, CUSTOM["base_url"])
            self.assertEqual(model_secrets.decrypt_key(row.encrypted_api_key, self.user_id), CUSTOM["api_key"])
        self.assertEqual(self.client.put(f"/analysis-models/{first['config_id']}", json={**changed, "api_key": "replacement-key"}).status_code, 200)
        second = self.post("/analysis-models", {**CUSTOM, "name": "Second supplier"})
        defaults = [row for row in self.client.get("/analysis-models").json() if row["is_default"]]
        self.assertEqual([row["id"] for row in defaults], [second["id"]])
        with Session(self.engine) as db:
            row = db.get(UserAnalysisModel, first["config_id"])
            self.assertEqual(model_secrets.decrypt_key(row.encrypted_api_key, self.user_id), "replacement-key")

    def test_owner_isolation_applies_to_keys_defaults_and_test_calls(self):
        first = self.post("/analysis-models", CUSTOM)
        self.register_second_user()
        with patch.object(adapters, "generate_text") as model:
            self.post(f"/analysis-models/{first['config_id']}/test", {}, 404)
            model.assert_not_called()
        self.assertFalse(any(row["id"] == first["id"] for row in self.client.get("/analysis-models").json()))
        with Session(self.engine) as db:
            db.info["user_id"] = self.client.get("/auth/me").json()["id"]
            self.assertNotEqual(analysis_models.select_model(db, None).id, first["id"])
            with self.assertRaises(NotFoundError):
                analysis_models.select_model(db, first["id"])

    def test_connection_test_uses_saved_endpoint_key_and_model_without_returning_response(self):
        profile = self.post("/analysis-models", {**CUSTOM, "provider": "supplier-" + "x" * 91})
        with patch.object(compatible, "generate_text", return_value="OK private-provider-output") as call:
            result = self.post(f"/analysis-models/{profile['config_id']}/test", {}, 200)
        self.assertTrue(result["ok"])
        self.assertNotIn("private-provider-output", json.dumps(result))
        self.assertEqual(call.call_args.kwargs["endpoint"], CUSTOM["base_url"])
        self.assertEqual(call.call_args.kwargs["api_key"], CUSTOM["api_key"])
        self.assertEqual(call.call_args.kwargs["model"], CUSTOM["model"])

    def test_analysis_preserves_long_custom_provider_name_and_uses_its_endpoint(self):
        from test_collection_api import VALID_ANALYSIS
        provider = "supplier-" + "x" * 91
        profile = self.post("/analysis-models", {**CUSTOM, "provider": provider})
        case = self.post("/rednote/cases", {"name": "Case", "body": "Source text",
            "link": "https://www.xiaohongshu.com/explore/" + "a" * 24})
        with patch.object(compatible, "generate_text", return_value=json.dumps(VALID_ANALYSIS)) as model:
            saved = self.post(f"/rednote/cases/{case['id']}/analyze", {"model_id": profile["id"]})
        self.assertEqual(saved["provider"], provider)
        self.assertEqual(model.call_args.kwargs["endpoint"], CUSTOM["base_url"])
        self.assertNotIn(CUSTOM["api_key"], json.dumps(saved))

    def test_invalid_input_does_not_echo_secrets_or_make_requests(self):
        with patch.object(compatible, "generate_text") as model:
            for url in ("http://127.0.0.1:9222", "https://127.0.0.1", "https://[::1]", "https://localhost",
                        "https://api.vendor.example/v1/chat/completions", "https://user:private@api.example.com",
                        "https://api.example.com?api_key=private-key"):
                result = self.client.post("/analysis-models", json={**CUSTOM, "base_url": url})
                self.assertEqual(result.status_code, 422)
                self.assertNotIn(CUSTOM["api_key"], result.text)
                self.assertNotIn("private-key", result.text)
            result = self.client.post("/analysis-models", json={**CUSTOM, "api_key": "secret\ninvalid"})
            self.assertEqual(result.status_code, 422)
            self.assertNotIn("secret", result.text)
            model.assert_not_called()

    def test_local_encryption_key_persists_and_missing_or_changed_key_fails_closed(self):
        with patch("config.MODEL_CREDENTIALS_KEY", ""), patch("config.STORAGE_DIR", Path(self.storage.name)):
            token = model_secrets.encrypt_key("test-key", 1)
            self.assertEqual(model_secrets.decrypt_key(token, 1), "test-key")
            key_file = Path(self.storage.name) / "secrets/model-credentials.key"
            original = key_file.read_bytes()
            model_secrets.encrypt_key("another-key", 2)
            self.assertEqual(key_file.read_bytes(), original)
            key_file.unlink()
            with self.assertRaises(ServiceUnavailableError):
                model_secrets.decrypt_key(token, 1)
            self.assertFalse(key_file.exists())


class CompatibleTransportTest(unittest.TestCase):
    def resolved(self, address):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (address, 443))]

    def test_pin_dns_address_preserves_host_sni_and_rejects_private_or_mixed_resolution(self):
        transport = compatible.PublicHTTPSTransport()
        transport._transport.close()
        requests = []
        transport._transport = httpx.MockTransport(lambda request: (requests.append(request) or httpx.Response(200)))
        with httpx.Client(transport=transport, trust_env=False) as client:
            with patch.object(compatible.socket, "getaddrinfo", return_value=self.resolved("8.8.8.8")):
                client.post("https://api.vendor.example/v1/chat/completions")
            self.assertEqual(requests[0].url.host, "8.8.8.8")
            self.assertEqual(requests[0].headers["Host"], "api.vendor.example")
            self.assertEqual(requests[0].extensions["sni_hostname"], "api.vendor.example")
            for address in ("127.0.0.1", "10.0.0.1", "169.254.169.254", "192.168.1.1", "::1", "100.64.0.1"):
                with patch.object(compatible.socket, "getaddrinfo", return_value=self.resolved(address)):
                    with self.assertRaises(InvalidInputError):
                        client.post("https://api.vendor.example/v1/chat/completions")
            with patch.object(compatible.socket, "getaddrinfo", return_value=self.resolved("8.8.8.8") + self.resolved("10.0.0.1")):
                with self.assertRaises(InvalidInputError):
                    client.post("https://api.vendor.example/v1/chat/completions")
        self.assertEqual(len(requests), 1)

    def test_chat_protocol_response_errors_redirects_and_timeouts_are_sanitized_without_retries(self):
        requests = []
        def request(response):
            def handler(value):
                requests.append(value)
                if isinstance(response, Exception):
                    raise response
                return response
            return httpx.MockTransport(handler)
        params = dict(endpoint=CUSTOM["base_url"], api_key=CUSTOM["api_key"], model=CUSTOM["model"],
                      system_prompt="system", user_prompt="user")
        response = httpx.Response(200, json={"choices": [{"message": {"content": " OK "}, "finish_reason": "stop"}]})
        with patch.object(compatible, "PublicHTTPSTransport", return_value=request(response)):
            self.assertEqual(compatible.generate_text(**params), "OK")
        self.assertEqual(requests[0].headers["Authorization"], "Bearer " + CUSTOM["api_key"])
        self.assertEqual(json.loads(requests[0].content)["model"], CUSTOM["model"])
        for response in (httpx.Response(302, headers={"location": "https://other.example"}, text="secret"),
                         httpx.Response(401, text=CUSTOM["api_key"]), httpx.Response(200, content=b""),
                         httpx.Response(200, json={"choices": []}), httpx.ReadTimeout(CUSTOM["api_key"])):
            with patch.object(compatible, "PublicHTTPSTransport", return_value=request(response)):
                before = len(requests)
                with self.assertRaises(ServiceUnavailableError) as error:
                    compatible.generate_text(**params)
                self.assertNotIn(CUSTOM["api_key"], str(error.exception))
                self.assertEqual(len(requests), before + 1)


class CustomModelGenerationTest(unittest.TestCase):
    setUp = generation_tests.ContentGenerationTest.setUp
    call = generation_tests.ContentGenerationTest.call
    new_task = generation_tests.ContentGenerationTest.new_task
    save = generation_tests.ContentGenerationTest.save

    def test_generation_uses_selected_custom_model_and_saves_only_public_model_snapshot(self):
        with patch("config.MODEL_CREDENTIALS_KEY", Fernet.generate_key().decode()):
            profile = analysis_models.save_model(self.db, AnalysisModelWrite(**CUSTOM))
            with patch.object(compatible, "generate_text", return_value=json.dumps(self.valid)) as model:
                result = self.call()
            self.assertEqual(result.generation_model, CUSTOM["model"])
            self.assertEqual(result.generation_model_profile["id"], profile["id"])
            self.assertEqual(model.call_args.kwargs["api_key"], CUSTOM["api_key"])
            self.assertNotIn(CUSTOM["api_key"], result.model_dump_json())
            self.generate.assert_not_called()

    def test_foreign_model_is_rejected_before_rag_and_disabled_models_do_not_fallback(self):
        with patch("config.MODEL_CREDENTIALS_KEY", Fernet.generate_key().decode()):
            profile = analysis_models.save_model(self.db, AnalysisModelWrite(**CUSTOM))
            self.db.info["user_id"] = 2
            with self.assertRaises(NotFoundError):
                content_generation.generate_content(self.db, product_id=2, user_requirement="draft", model_id=profile["id"])
            self.retrieve.assert_not_called()
            self.generate.assert_not_called()
            self.db.info["user_id"] = 1
            analysis_models.save_model(self.db, AnalysisModelWrite(**{**CUSTOM, "is_default": False,
                "is_active": False, "api_key": None}), profile["config_id"])
            with self.assertRaises(InvalidInputError):
                self.call(model_id=profile["id"])
            self.retrieve.assert_not_called()

    def test_custom_generation_saves_public_provenance_and_failure_preserves_existing_version(self):
        from models.content import Content
        from models.task import Task
        with patch("config.MODEL_CREDENTIALS_KEY", Fernet.generate_key().decode()):
            profile = analysis_models.save_model(self.db, AnalysisModelWrite(**CUSTOM))
            task_id = self.new_task()
            with patch.object(compatible, "generate_text", return_value=json.dumps(self.valid)):
                result = self.save(task_id, model_id=profile["id"])
            saved = self.db.get(Content, result.content_id)
            self.assertEqual(saved.generation_metadata["generation_model_profile"]["id"], profile["id"])
            self.assertNotIn(CUSTOM["api_key"], json.dumps(self.db.get(Task, task_id).checkpoint))
            self.assertNotIn("api_key", json.dumps(saved.generation_metadata))
            with patch.object(compatible, "generate_text", side_effect=ServiceUnavailableError("Model unavailable")):
                with self.assertRaises(ServiceUnavailableError):
                    self.save(task_id, model_id=profile["id"])
            self.assertEqual(self.db.get(Task, task_id).status, "failed")
            self.assertEqual(len(list(self.db.scalars(select(Content).where(Content.task_id == task_id)))), 1)
            self.generate.assert_not_called()
