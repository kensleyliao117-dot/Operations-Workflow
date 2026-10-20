"""使用模拟模型和隔离数据库验证生图、文件保存及审核链。"""
from io import BytesIO
import json
from pathlib import Path
from unittest.mock import patch
import unittest

import httpx
from PIL import Image
from sqlalchemy.orm import Session

import config
from agents import image_prompt_agent
from integrations.llm.image import ImageResult, QwenImageClient, QwenImageError, validate_image_url, validate_png
from models.content import Content
from models.task import Task
from models.rules import PlatformRule
from schemas.image_generation import ImagePromptDraft
from schemas.rules import RuleAIDraft
from services.exceptions import ContentGenerationError
import test_business_api as api_tests
import test_workflow as workflow_tests


URL = "https://dashscope-result-sh.oss-cn-shanghai.aliyuncs.com/example.png?Expires=123&Signature=secret"


def png(width=8, height=8):
    output = BytesIO()
    Image.new("RGB", (width, height), "white").save(output, format="PNG")
    return output.getvalue()


class ImageClientTest(unittest.TestCase):
    def setUp(self):
        for name, value in (("QWEN_IMAGE_API_KEY", "image-key"), ("QWEN_IMAGE_MODEL", "qwen-image-2.0"),
                            ("QWEN_IMAGE_BASE_URL", "https://dashscope.aliyuncs.com")):
            p = patch.object(config, name, value)
            p.start()
            self.addCleanup(p.stop)

    def client(self, handler):
        transport = httpx.MockTransport(handler)
        http = httpx.Client(transport=transport)
        self.addCleanup(http.close)
        return QwenImageClient(http_client=http)

    def test_official_request_and_parse(self):
        def handler(request):
            self.assertEqual(request.url.path, "/api/v1/services/aigc/multimodal-generation/generation")
            self.assertEqual(request.headers["Authorization"], "Bearer image-key")
            body = json.loads(request.content)
            self.assertEqual(body["model"], "qwen-image-2.0")
            self.assertEqual(body["parameters"]["size"], "1536*2048")
            self.assertFalse(body["parameters"]["prompt_extend"])
            self.assertTrue(body["parameters"]["watermark"])
            self.assertEqual(body["input"]["messages"][0]["content"], [{"text": "A scene"}])
            return httpx.Response(200, json={"request_id": "request-1", "output": {"choices": [
                {"finish_reason": "stop", "message": {"content": [{"image": URL}]}}]}})
        result = self.client(handler).generate("A scene", "No claims")
        self.assertEqual(result.urls, (URL,))
        self.assertEqual(result.request_id, "request-1")

    def test_five_exact_ratios_reach_provider_without_dimension_changes(self):
        from integrations.llm.image_sizes import ASPECT_RATIO_SIZES
        from schemas.image_generation import ImageGenerationRequest
        from schemas.workflow import WorkflowGenerationRequest
        sizes = []
        def handler(request):
            sizes.append(json.loads(request.content)['parameters']['size'])
            return httpx.Response(200, json={'output': {'choices': [
                {'message': {'content': [{'image': URL}]}}]}})
        client = self.client(handler)
        for ratio, size in ASPECT_RATIO_SIZES.items():
            with self.subTest(ratio=ratio):
                width, height = map(int, size.split('*'))
                rw, rh = map(int, ratio.split(':'))
                self.assertEqual(width * rh, height * rw)
                self.assertEqual(width % 16, 0)
                self.assertEqual(height % 16, 0)
                self.assertEqual(ImageGenerationRequest(size=size).size, size)
                self.assertEqual(WorkflowGenerationRequest(image_size=size).image_size, size)
                client.generate('Scene', 'No claims', size=size)
                self.assertEqual(sizes[-1], size)

    def test_timeout_errors_and_empty_or_partial_responses_are_sanitized(self):
        def timeout(request):
            raise httpx.ReadTimeout("secret response and key", request=request)
        with self.assertRaises(QwenImageError) as error:
            self.client(timeout).generate("scene", "negative")
        self.assertNotIn("secret", str(error.exception))
        for response in (httpx.Response(401, text="image-key"), httpx.Response(200, text=""),
                         httpx.Response(200, json={"code": "InvalidApiKey", "message": "image-key"}),
                         httpx.Response(200, json={"output": {"choices": []}}),
                         httpx.Response(200, json={"output": {"choices": [{"message": {"content": [{"image": URL}]}}]}})):
            with self.subTest(response=response.content[:30]), self.assertRaises(QwenImageError) as error:
                self.client(lambda _: response).generate("scene", "negative", count=2)
            self.assertNotIn("image-key", str(error.exception))

    def test_configuration_preflight_rejects_wrong_model_and_hosts(self):
        for name, value in (("QWEN_IMAGE_MODEL", "text-embedding-v3"), ("QWEN_IMAGE_API_KEY", ""),
                            ("QWEN_IMAGE_BASE_URL", "https://example.com"),
                            ("QWEN_IMAGE_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1")):
            with patch.object(config, name, value), self.assertRaises(config.ConfigurationError):
                QwenImageClient()

    def test_result_url_allowlist_blocks_private_redirect_and_lookalike_hosts(self):
        for url in ("http://127.0.0.1/secret", "https://127.0.0.1/secret", "https://example.com/a.png",
                    "https://dashscope-result-sh.oss-cn-shanghai.aliyuncs.com.evil.org/a.png",
                    "https://user:secret@dashscope-result-sh.oss-cn-shanghai.aliyuncs.com/a.png",
                    "https://dashscope-result-sh.oss-cn-shanghai.aliyuncs.com:444/a.png"):
            with self.subTest(url=url), self.assertRaises(QwenImageError):
                validate_image_url(url)
        self.assertEqual(validate_image_url(URL), URL)

    def test_download_validates_png_dimensions_and_never_follows_redirect(self):
        def handler(request):
            self.assertNotIn("Authorization", request.headers)
            return httpx.Response(200, content=png(), headers={"Content-Type": "image/png"})
        with httpx.Client(transport=httpx.MockTransport(handler)) as downloader:
            self.assertEqual(QwenImageClient._download(downloader, URL, "8*8"), png())
            with self.assertRaises(QwenImageError):
                QwenImageClient._download(downloader, URL, "9*9")
        for response in (httpx.Response(302, headers={"Location": "http://127.0.0.1/secret"}),
                         httpx.Response(200, content=b"<html>error</html>")):
            with httpx.Client(transport=httpx.MockTransport(lambda _: response)) as downloader, self.assertRaises(QwenImageError):
                QwenImageClient._download(downloader, URL, "8*8")
        with patch("integrations.llm.image.MAX_IMAGE_BYTES", 10), self.assertRaises(QwenImageError):
            validate_png(png())

    def test_download_uses_separate_client_without_api_credentials(self):
        def handler(request):
            self.assertNotIn("Authorization", request.headers)
            return httpx.Response(200, content=png())
        with httpx.Client(headers={"Authorization": "Bearer secret-api-key"}) as api_client:
            client = QwenImageClient(http_client=api_client)
            downloader = httpx.Client(transport=httpx.MockTransport(handler))
            with patch("integrations.llm.image.httpx.Client", return_value=downloader):
                self.assertEqual(client.download(URL, size="8*8"), png())

    def test_prompt_agent_separates_facts_and_validates_structured_output(self):
        payload = {"product_knowledge": {"context": "product facts"}, "content": {"body": "body"},
                   "style_requirement": "soft colors"}
        with patch("agents.image_prompt_agent.deepseek.generate_text", return_value=json.dumps(
                {"prompt": "A calm scene", "negative_prompt": "No unsupported claims"})) as model:
            self.assertEqual(image_prompt_agent.generate_prompt(payload).prompt, "A calm scene")
            self.assertEqual(json.loads(model.call_args.kwargs["user_prompt"]), payload)
            self.assertIn("唯一", model.call_args.kwargs["system_prompt"])
        for invalid in ('{"prompt":"first","prompt":"second","negative_prompt":"x"}',
                        '{"prompt":" ","negative_prompt":"x"}', '{"prompt":"scene"}', 'not json'):
            with patch("agents.image_prompt_agent.deepseek.generate_text", return_value=invalid), self.assertRaises(ContentGenerationError):
                image_prompt_agent.generate_prompt(payload)


class ImageAPITest(unittest.TestCase):
    setUpBase = api_tests.BusinessAPITest.setUp
    tearDown = api_tests.BusinessAPITest.tearDown
    post = api_tests.BusinessAPITest.post
    status = api_tests.BusinessAPITest.status
    task_and_content = api_tests.BusinessAPITest.task_and_content
    review = api_tests.BusinessAPITest.review
    register_second_user = api_tests.BusinessAPITest.register_second_user
    generation_mocks = api_tests.BusinessAPITest.generation_mocks

    def setUp(self):
        self.setUpBase()
        self.root = Path(self.storage.name) / "generated_images"
        for target, value in (("config.GENERATED_IMAGE_DIR", self.root),
                              ("services.image_generation.retriever.retrieve", self.generation_mocks()),
                              ("agents.image_prompt_agent.generate_prompt", ImagePromptDraft(prompt="Calm commute scene", negative_prompt="No text")),
                              ("agents.rule_agent.check_content", RuleAIDraft(risks=[]))):
            p = patch(target, return_value=value) if "config." not in target else patch(target, value)
            p.start()
            self.addCleanup(p.stop)
        p = patch("services.image_generation.QwenImageClient")
        self.image = p.start().return_value.__enter__.return_value
        self.addCleanup(p.stop)
        self.image.generate.return_value = ImageResult("qwen-image-2.0", "image-request", (URL,))
        self.image.download.return_value = png()
        with Session(self.engine) as db:
            db.add(PlatformRule(platform_name="rednote", content="No unsupported claims"))
            db.commit()
        self.task_id, self.content_id = self.task_and_content()
        self.endpoint = f"/contents/{self.content_id}/images/generate"

    def test_custom_default_is_used_for_image_prompt_without_changing_image_provider(self):
        from cryptography.fernet import Fernet
        data = {"name": "Custom", "provider": "Another provider", "model": "custom-chat",
                "base_url": "https://api.vendor.example/v1", "api_key": "test-key", "is_default": True}
        with patch("config.MODEL_CREDENTIALS_KEY", Fernet.generate_key().decode()):
            model = self.post("/analysis-models", data)
            result = self.post(self.endpoint, {"style_requirement": "warm colors"})
        metadata = result["content"]["generation_metadata"]["image_generation"]
        self.assertEqual(metadata["prompt_model"], "custom-chat")
        self.assertEqual(metadata["prompt_model_profile"]["id"], model["id"])
        self.assertEqual(metadata["model"], "qwen-image-2.0")
        self.assertNotIn("api_key", json.dumps(metadata))

    def test_generate_saves_new_version_local_files_and_waits_for_review(self):
        result = self.post(self.endpoint, {"style_requirement": "warm colors"})
        new = result["content"]
        self.assertEqual(new["version"], 2)
        self.assertEqual((new["title"], new["body"]), ("Title", "Body"))
        self.assertEqual(result["task_status"], "pending_review")
        self.assertEqual(result["review_status"], "waiting_review")
        self.assertFalse(result["rule_check"]["image_binary_reviewed"])
        self.assertEqual(len(new["image_assets"]), 1)
        self.assertEqual(new["generation_metadata"]["image_generation"]["source_content_id"], self.content_id)
        self.assertIn("人工", result["notices"][-1])
        self.assertEqual(len(self.client.get(f"/contents/{new['id']}").json()["notices"]), 3)
        self.assertNotIn("Signature", json.dumps(result))
        self.assertEqual(self.client.get(result["images"][0]["download_path"]).content, png())
        self.assertEqual(self.client.get(f"/contents/{self.content_id}").json()["image_assets"], [])
        self.post("/publish-records", {"task_id": self.task_id, "content_id": new["id"],
                  "platform": "rednote", "account_id": self.account_id}, 409)
        self.review(self.task_id, new["id"])

    def test_each_ratio_is_saved_and_forwarded_to_image_generation_and_download(self):
        from integrations.llm.image_sizes import ASPECT_RATIO_SIZES
        content_id = self.content_id
        for size in ASPECT_RATIO_SIZES.values():
            result = self.post(f'/contents/{content_id}/images/generate', {'size': size, 'replace_existing': True})
            self.assertEqual(self.image.generate.call_args.kwargs['size'], size)
            self.assertEqual(self.image.download.call_args.kwargs['size'], size)
            content_id = result['content']['id']
            self.assertEqual(result['content']['generation_metadata']['image_generation']['size'], size)

    def test_regenerate_appends_or_replaces_without_changing_old_assets(self):
        first = self.post(self.endpoint, {})
        second = self.post(f"/contents/{first['content']['id']}/images/generate", {})
        self.assertEqual(len(second["content"]["image_assets"]), 2)
        third = self.post(f"/contents/{second['content']['id']}/images/generate", {"replace_existing": True})
        self.assertEqual(len(third["content"]["image_assets"]), 1)
        self.assertEqual(third["content"]["version"], 4)
        self.assertEqual(self.client.get(first["images"][0]["download_path"]).content, png())
        self.post(self.endpoint, {}, 409)

    def test_failure_and_partial_download_do_not_create_content_or_keep_orphan_files(self):
        self.image.generate.return_value = ImageResult("qwen-image-2.0", "req", (URL, URL + "2"))
        self.image.download.side_effect = [png(), QwenImageError("download failed")]
        self.post(self.endpoint, {"count": 2}, 503)
        self.assertEqual(len(self.client.get("/contents").json()), 1)
        self.assertEqual(list(self.root.rglob("*.png")), [])
        task = self.client.get(f"/tasks/{self.task_id}").json()
        self.assertEqual(task["status"], "pending_review")
        self.assertEqual(task["checkpoint"]["image_generation"]["status"], "failed")
        self.image.download.side_effect = None
        self.image.generate.return_value = ImageResult("qwen-image-2.0", "retry", (URL,))
        self.post(self.endpoint, {})

    def test_cancellation_during_paid_call_rejects_save_and_preserves_cancelled_state(self):
        def cancel(*args, **kwargs):
            self.status(self.task_id, "cancelled")
            return ImageResult("qwen-image-2.0", "req", (URL,))
        self.image.generate.side_effect = cancel
        self.post(self.endpoint, {}, 409)
        self.assertEqual(self.client.get(f"/tasks/{self.task_id}").json()["status"], "cancelled")
        self.assertEqual(len(self.client.get("/contents").json()), 1)
        self.assertEqual(list(self.root.rglob("*.png")), [])

    def test_concurrent_edit_rejects_stale_images_without_restoring_old_review(self):
        def edit(*args, **kwargs):
            response = self.client.patch(f"/contents/{self.content_id}", json={"body": "updated body"})
            self.assertEqual(response.status_code, 201)
            return ImageResult("qwen-image-2.0", "req", (URL,))
        self.image.generate.side_effect = edit
        self.post(self.endpoint, {}, 409)
        contents = self.client.get("/contents").json()
        self.assertEqual(len(contents), 2)
        self.assertEqual(contents[0]["body"], "updated body")
        self.assertEqual(contents[0]["image_assets"], [])
        self.assertEqual(self.client.get(f"/tasks/{self.task_id}").json()["status"], "running")
        self.assertEqual(list(self.root.rglob("*.png")), [])

    def test_authentication_and_user_isolation_precede_model_calls(self):
        generated = self.post(self.endpoint, {})
        self.image.generate.reset_mock()
        self.client.headers.pop("Authorization")
        self.post(self.endpoint, {}, 401)
        self.assertEqual(self.client.get(generated["images"][0]["download_path"]).status_code, 401)
        self.register_second_user()
        self.post(self.endpoint, {}, 404)
        self.assertEqual(self.client.get(generated["images"][0]["download_path"]).status_code, 404)
        self.image.generate.assert_not_called()

    def test_no_product_knowledge_or_approved_task_never_generates_images(self):
        result = self.generation_mocks()
        from dataclasses import replace
        with patch("services.image_generation.retriever.retrieve", return_value=replace(result, context="", sources=[])):
            self.post(self.endpoint, {}, 422)
        self.image.generate.assert_not_called()
        self.review(self.task_id, self.content_id)
        self.post(self.endpoint, {}, 409)
        self.image.generate.assert_not_called()

    def test_paid_call_blocks_duplicate_generation_and_review(self):
        def concurrent(*args, **kwargs):
            self.post(self.endpoint, {}, 409)
            self.post(f"/tasks/{self.task_id}/generate", {}, 409)
            self.status(self.task_id, "pending_review", 409)
            self.review(self.task_id, self.content_id, expected=409)
            return ImageResult("qwen-image-2.0", "req", (URL,))
        self.image.generate.side_effect = concurrent
        self.post(self.endpoint, {})

    def test_review_submission_failure_keeps_images_and_can_retry_without_regenerating(self):
        with patch("services.image_generation.submit_for_review", side_effect=RuntimeError("private data")):
            result = self.post(self.endpoint, {})
        self.assertEqual(result["review_status"], "submission_failed")
        self.assertNotIn("private data", result["review_error"])
        self.assertEqual(self.client.get(result["images"][0]["download_path"]).status_code, 200)
        retried = self.post(f"/contents/{result['content']['id']}/images/submit-review", None, 200)
        self.assertEqual(retried["review_status"], "waiting_review")
        self.image.generate.assert_called_once()

    def test_download_rejects_cross_user_keys_and_traversal(self):
        result = self.post(self.endpoint, {})
        new_id = result["content"]["id"]
        for key in ("generated_images/user_999/product_999/a.png", "../../.env",
                    f"generated_images/user_{self.user_id}/product_{self.product_id}/../../.env"):
            with Session(self.engine) as db:
                db.get(Content, new_id).image_assets = [{"storage_key": key}]
                db.commit()
            self.assertEqual(self.client.get(f"/contents/{new_id}/images/0").status_code, 404)

    def test_database_save_failure_cleans_only_new_files(self):
        from sqlalchemy.exc import SQLAlchemyError
        first = self.post(self.endpoint, {})
        with patch("services.image_generation._save", side_effect=SQLAlchemyError("secret sql parameters")):
            self.post(f"/contents/{first['content']['id']}/images/generate", {}, 503)
        self.assertEqual(len(list(self.root.rglob("*.png"))), 1)
        self.assertEqual(self.client.get(first["images"][0]["download_path"]).content, png())
        self.assertEqual(len(self.client.get("/contents").json()), 2)

    def test_missing_rules_are_advisory_and_generated_images_can_be_approved(self):
        with Session(self.engine) as db:
            for rule in db.query(PlatformRule).all():
                rule.is_active = False
            db.commit()
        result = self.post(self.endpoint, {})
        self.assertEqual(result["review_status"], "waiting_review")
        self.assertEqual(result["rule_check"]["status"], "incomplete")
        self.assertEqual(result["task_status"], "pending_review")
        self.review(self.task_id, result["content"]["id"])

    def test_expired_attempt_can_retry_after_process_interruption(self):
        with Session(self.engine) as db:
            task = db.get(Task, self.task_id)
            task.status = "running"
            task.checkpoint = {"image_generation": {"attempt_id": "abandoned", "status": "running",
                                                  "expires_at": "2000-01-01T00:00:00+00:00"}}
            db.commit()
        self.post(self.endpoint, {})

    def test_lost_commit_response_recovers_saved_version_without_deleting_images(self):
        from services import image_generation
        from sqlalchemy.exc import SQLAlchemyError
        save = image_generation._save
        def commit_then_disconnect(*args, **kwargs):
            save(*args, **kwargs)
            raise SQLAlchemyError("commit acknowledgement lost")
        with patch("services.image_generation._save", side_effect=commit_then_disconnect):
            result = self.post(self.endpoint, {})
        self.assertEqual(result["review_status"], "waiting_review")
        self.assertEqual(self.client.get(result["images"][0]["download_path"]).content, png())
        self.assertEqual(len(self.client.get("/contents").json()), 2)
        self.image.generate.assert_called_once()


class ImageWorkflowTest(unittest.TestCase):
    setUp = workflow_tests.WorkflowTest.setUp
    tearDown = workflow_tests.WorkflowTest.tearDown
    post = workflow_tests.WorkflowTest.post
    review = workflow_tests.WorkflowTest.review
    status = workflow_tests.WorkflowTest.status
    generation_mocks = workflow_tests.WorkflowTest.generation_mocks
    start = workflow_tests.WorkflowTest.start

    def test_images_rejoin_graph_checks_and_interrupt_for_latest_version(self):
        saved = self.start()
        with patch("config.GENERATED_IMAGE_DIR", Path(self.storage.name) / "images"), \
             patch("agents.image_prompt_agent.generate_prompt", return_value=ImagePromptDraft(prompt="Scene", negative_prompt="No text")), \
             patch("services.image_generation.QwenImageClient") as mock:
            client = mock.return_value.__enter__.return_value
            client.generate.return_value = ImageResult("qwen-image-2.0", "req", (URL,))
            client.download.return_value = png()
            def download(*args, **kwargs):
                state = self.client.get(self.base).json()
                self.assertEqual(state["status"], "generating_images")
                self.assertIsNone(state["interrupt"])
                return png()
            client.download.side_effect = download
            result = self.post(f"/contents/{saved['content_id']}/images/generate", {})
        self.assertEqual(result["review_status"], "waiting_review")
        state = self.client.get(self.base).json()
        self.assertEqual(state["content_id"], result["content"]["id"])
        self.assertEqual(state["content_version"], 2)
        self.assertEqual(state["interrupt"]["content_version"], 2)
        self.assertEqual(state["task_status"], "pending_review")
        self.assertFalse(state["rule_check"]["image_binary_reviewed"])
        self.review(self.task["id"], state["content_id"])
        approved = self.post(self.base + "/resume", {}, 200)
        self.assertEqual(approved["status"], "approved")
        self.assertEqual(self.client.get("/publish-records").json(), [])
