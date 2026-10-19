"""独立内存数据库集成测试，不连接或改写项目 PostgreSQL。"""
import unittest
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import httpx

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app import app
from database import Base, get_db
from config import CORS_ORIGINS
from models.user import Users
from models.product import ProductDocument
import jwt
from datetime import datetime, timedelta, timezone
from config import require_jwt_secret, JWT_ISSUER, JWT_AUDIENCE
from rag.embeddings import QwenEmbeddings
from rag.vector_store import SearchResult


class BusinessAPITest(unittest.TestCase):
    def setUp(self):
        self.storage = TemporaryDirectory(prefix="operations-api-test-")
        self.addCleanup(self.storage.cleanup)
        self.storage_patch = patch("services.product.UPLOAD_DIR", Path(self.storage.name))
        self.storage_patch.start()
        self.addCleanup(self.storage_patch.stop)
        self.engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                                    poolclass=StaticPool)

        @event.listens_for(self.engine, "connect")
        def enable_foreign_keys(connection, _):
            connection.execute("PRAGMA foreign_keys=ON")

        Base.metadata.create_all(self.engine)

        def test_db():
            with Session(self.engine) as db:
                yield db

        app.dependency_overrides[get_db] = test_db
        self.client = TestClient(app, base_url="http://127.0.0.1", client=("127.0.0.1", 50000))
        self.client.__enter__()
        self.password = "correct-horse-battery-2026"
        user = self.post("/auth/register", {"username": "operator", "password": self.password})
        self.user_id = user["id"]
        self.token = self.post("/auth/login", {"username": "operator", "password": self.password}, 200)["access_token"]
        self.client.headers["Authorization"] = f"Bearer {self.token}"
        self.product_id = self.post("/products", {"name": "Product"})["id"]
        self.account_id = self.post("/platform-accounts", {"platform": "rednote", "platform_user_id": "test-account"})["id"]

    def tearDown(self):
        self.client.__exit__(None, None, None)
        app.dependency_overrides.clear()
        self.engine.dispose()

    def post(self, path, payload, expected=201):
        response = self.client.post(path, json=payload)
        self.assertEqual(response.status_code, expected, response.text)
        return response.json()

    def task_and_content(self):
        task = self.post("/tasks", {"product_id": self.product_id, "task_type": "generate",
                                    "input_text": "Create content", "platform_name": "rednote"})
        content = self.post("/contents", {"task_id": task["id"], "title": "Title", "body": "Body"})
        self.status(task["id"], "running")
        self.status(task["id"], "pending_review")
        return task["id"], content["id"]

    def test_duplicate_platform_account_has_clear_message_and_does_not_change_records(self):
        payload = {"platform": "rednote", "platform_user_id": "test-account", "display_name": "Changed"}
        response = self.client.post("/platform-accounts", json=payload)
        self.assertEqual(response.status_code, 409)
        self.assertIn("已登记", response.json()["detail"])
        self.assertNotIn("数据库约束", response.json()["detail"])
        accounts = self.client.get("/platform-accounts").json()
        self.assertEqual(len(accounts), 1)
        self.assertEqual(accounts[0]["id"], self.account_id)
        self.assertIsNone(accounts[0]["display_name"])
        self.register_second_user()
        other = self.post("/platform-accounts", payload)
        self.assertNotEqual(other["id"], self.account_id)

    def status(self, task_id, value, expected=200):
        response = self.client.patch(f"/tasks/{task_id}/status", json={"status": value})
        self.assertEqual(response.status_code, expected, response.text)

    def review(self, task_id, content_id, status="approved", comment=None, expected=201):
        return self.post("/reviews", {"task_id": task_id, "content_id": content_id,
                                      "status": status,
                                      "comment": comment}, expected)

    def test_review_publish_failure_retry_success(self):
        task_id, content_id = self.task_and_content()
        self.status(task_id, "approved", 409)
        self.status(task_id, "completed", 409)
        payload = {"task_id": task_id, "content_id": content_id, "platform": "rednote", "account_id": self.account_id}
        self.post("/publish-records", payload, 409)
        review = self.review(task_id, content_id)
        self.assertEqual(self.client.get(f"/reviews/{review['id']}").status_code, 200)
        self.post("/publish-records", {**payload, "platform": "other-platform"}, 409)
        record = self.post("/publish-records", payload)
        self.post("/publish-records", payload, 409)
        self.post(f"/publish-records/{record['id']}/result", {"publish_status": "published"}, 422)
        failure = {"publish_status": "failed", "error_message": "Network timeout"}
        self.post(f"/publish-records/{record['id']}/result", failure, 200)
        self.post(f"/publish-records/{record['id']}/result", failure, 200)
        task = self.client.get(f"/tasks/{task_id}").json()
        self.assertEqual(task["status"], "failed")
        self.assertEqual(task["failed_from_status"], "publishing")
        retry = self.post(f"/publish-records/{record['id']}/retry", None)
        self.assertNotEqual(record["id"], retry["id"])
        self.post(f"/publish-records/{record['id']}/retry", None, 409)
        result = {"publish_status": "published", "platform_post_id": "post-123", "account_id": self.account_id}
        success = self.post(f"/publish-records/{retry['id']}/result", result, 200)
        self.assertIsNotNone(success["published_at"])
        self.post(f"/publish-records/{retry['id']}/result", result, 200)
        self.post(f"/publish-records/{retry['id']}/result", failure, 409)
        self.assertEqual(self.client.get(f"/tasks/{task_id}").json()["status"], "completed")
        self.assertEqual(len(self.client.get("/publish-records", params={"task_id": task_id}).json()), 2)

    def test_review_association_rejection_and_new_version(self):
        task_id, content_id = self.task_and_content()
        other_task, other_content = self.task_and_content()
        self.review(task_id, other_content, expected=409)
        self.assertEqual(self.client.get(f"/tasks/{task_id}").json()["status"], "pending_review")
        self.review(task_id, content_id, status="rejected", expected=422)
        self.review(task_id, content_id, status="rejected", comment="Improve title")
        self.review(task_id, content_id, expected=409)
        self.status(task_id, "running")
        response = self.client.patch(f"/contents/{content_id}", json={"title": "New title"})
        self.assertEqual(response.status_code, 201, response.text)
        new_content = response.json()
        self.status(task_id, "pending_review")
        self.review(task_id, content_id, expected=409)
        self.review(task_id, new_content["id"])
        self.assertEqual(self.client.get(f"/contents/{content_id}").json()["title"], "Title")
        self.assertEqual(len(self.client.get("/reviews", params={"task_id": task_id}).json()), 2)

    def test_edit_while_pending_review_requires_resubmission(self):
        task_id, content_id = self.task_and_content()
        response = self.client.patch(f"/contents/{content_id}", json={"body": "Updated"})
        self.assertEqual(response.status_code, 201, response.text)
        self.assertEqual(self.client.get(f"/tasks/{task_id}").json()["status"], "running")
        self.review(task_id, response.json()["id"], expected=409)

    def test_prompt_rules_and_platform_cases(self):
        payload = {"platform": "rednote", "name": "Natural",
                   "prompt_content": "Use natural language"}
        self.post("/prompts", {**payload, "user_id": 999}, 422)
        prompt = self.post("/prompts", payload)
        update = {key: value for key, value in payload.items() if key != "user_id"}
        update.update(prompt_content="Use short sentences", is_active=False)
        response = self.client.put(f"/prompts/{prompt['id']}", json=update)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["version"], 2)
        self.assertEqual(self.client.put(f"/prompts/{prompt['id']}", json=update).json()["version"], 2)
        self.assertEqual(self.client.get("/prompts?is_active=true").json(), [])
        self.post("/rules/platform", {"platform_name": "rednote", "content": "No false claims"})
        with Session(self.engine) as db:
            db.get(Users, self.user_id).is_admin = True
            db.commit()
        for kind in ("custom", "platform"):
            rule_data = {"platform_name": "rednote", "content": "No false claims"}
            rule = self.post(f"/rules/{kind}", rule_data)
            response = self.client.put(f"/rules/{kind}/{rule['id']}",
                                       json={"platform_name": "rednote", "content": "Updated"})
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(self.client.get(f"/rules/{kind}?platform_name=other-platform").json(), [])
        for platform in ("rednote",):
            case = self.post(f"/{platform}/cases", {"name": "Example", "link": f"https://{platform}.example/1"})
            self.post(f"/{platform}/cases", {"name": "Duplicate", "link": case["link"]}, 409)
            self.post(f"/{platform}/analyses", {"cases_id": 999, "content": "Analysis"}, 404)
            analysis = self.post(f"/{platform}/analyses", {"cases_id": case["id"], "content": "Hook analysis"})
            self.assertEqual(self.client.get(f"/{platform}/analyses/{analysis['id']}").status_code, 200)
            self.assertEqual(len(self.client.get(f"/{platform}/analyses?cases_id={case['id']}").json()), 1)
            self.assertEqual(self.client.get(f"/{platform}/cases?offset=1").json(), [])

    def test_missing_and_invalid_parameters(self):
        for path in ("/reviews/999", "/publish-records/999", "/prompts/999", "/rules/custom/999",
                     "/rules/platform/999", "/rednote/cases/999", "/rednote/analyses/999"):
            self.assertEqual(self.client.get(path).status_code, 404, path)
        for path in ("/reviews", "/publish-records", "/prompts", "/rules/custom",
                     "/rednote/cases", "/rednote/analyses"):
            self.assertEqual(self.client.get(path, params={"limit": 0}).status_code, 422, path)
        schema = self.client.get("/openapi.json")
        self.assertEqual(schema.status_code, 200)
        self.assertIn("/reviews", schema.json()["paths"])

    def test_rednote_case_body_is_saved_readable_and_scoped_to_owner(self):
        payload = {"name": "耳机案例", "link": "https://www.xiaohongshu.com/explore/0123456789abcdef01234567",
                   "body": "正文第一行\n正文第二行"}
        case = self.post("/rednote/cases", payload)
        self.assertEqual(self.client.get(f"/rednote/cases/{case['id']}").json()["body"], payload["body"])
        self.assertEqual(self.client.get("/rednote/cases").json()[0]["body"], payload["body"])
        renamed = self.client.put(f"/rednote/cases/{case['id']}", json={"name": "新名称", "link": payload["link"]})
        self.assertEqual(renamed.status_code, 200, renamed.text)
        self.assertEqual(renamed.json()["body"], payload["body"])
        self.register_second_user()
        self.assertEqual(self.client.get(f"/rednote/cases/{case['id']}").status_code, 404)
        self.assertEqual(self.client.get("/rednote/cases").json(), [])
        self.assertEqual(self.post("/rednote/cases", payload)["body"], payload["body"])

    def test_cors_preflight(self):
        if not CORS_ORIGINS:
            self.skipTest("CORS disabled by environment")
        headers = {"Origin": CORS_ORIGINS[0], "Access-Control-Request-Method": "POST",
                   "Access-Control-Request-Headers": "content-type"}
        response = self.client.options("/tasks", headers=headers)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["access-control-allow-origin"], CORS_ORIGINS[0])
        headers["Origin"] = "https://untrusted.example"
        response = self.client.options("/tasks", headers=headers)
        self.assertNotIn("access-control-allow-origin", response.headers)

    def test_task_owner_is_chosen_by_backend(self):
        payload = {"product_id": self.product_id, "task_type": "generate", "input_text": "Create a post"}
        first = self.post("/tasks", payload)
        second = self.post("/tasks", payload)
        self.assertEqual(first["user_id"], second["user_id"])
        self.assertEqual(first["user_id"], self.user_id)
        self.post("/tasks", {**payload, "user_id": self.user_id}, 422)
        definition = self.client.get("/openapi.json").json()["components"]["schemas"]["TaskCreate"]
        self.assertNotIn("user_id", definition["properties"])

    def test_image_references_follow_content_version(self):
        task_id, content_id = self.task_and_content()
        images = [{"storage_key": "generated_images/example.png",
                   "url": "https://assets.example/image.png", "prompt": "Product photo"}]
        response = self.client.patch(f"/contents/{content_id}", json={"image_assets": images})
        self.assertEqual(response.status_code, 201, response.text)
        version2 = response.json()
        self.assertEqual(version2["image_assets"], images)
        self.assertEqual(self.client.get(f"/contents/{content_id}").json()["image_assets"], [])
        response = self.client.patch(f"/contents/{version2['id']}", json={"body": "New text"})
        version3 = response.json()
        self.assertEqual(version3["image_assets"], images)
        response = self.client.patch(f"/contents/{version3['id']}", json={"image_assets": []})
        self.assertEqual(response.status_code, 201, response.text)
        self.assertEqual(response.json()["image_assets"], [])
        self.assertEqual(self.client.get(f"/contents/{version2['id']}").json()["image_assets"], images)
        response = self.client.patch(f"/contents/{response.json()['id']}", json={"image_assets": None})
        self.assertEqual(response.status_code, 422)

    def generation_mocks(self):
        from rag.retriever import RetrievedSource, RetrievalResult

        source = RetrievedSource("[1]", "b" * 64, 10, "manual.txt", 0, 0.9, "适合通勤。", False,
                                 {"user_id": self.user_id, "product_id": self.product_id, "document_id": 10})
        return RetrievalResult(self.product_id, "通勤文案", "text-embedding-v3", 1024,
                               "[1] 适合通勤。", [source])

    def test_ai_generation_endpoint_saves_versions_tags_and_requires_latest_review(self):
        task = self.post("/tasks", {"product_id": self.product_id, "task_type": "generate",
                                    "platform_name": "rednote", "input_text": "写一篇通勤文案"})
        endpoint = f"/tasks/{task['id']}/generate"
        draft = {"title": "通勤小陪伴", "body": "通勤路上听听音乐。", "tags": ["#通勤", "耳机"]}
        with patch("services.content_generation.retriever.retrieve", return_value=self.generation_mocks()), \
             patch("integrations.llm.deepseek.generate_text", return_value=json.dumps(draft)):
            first = self.post(endpoint, {})
            second = self.post(endpoint, {"top_k": 3})
        self.assertEqual((first["task_id"], first["version"], second["version"]), (task["id"], 1, 2))
        self.assertEqual(second["task_status"], "pending_review")
        self.assertTrue(second["review_required"])
        self.assertEqual(second["tags"], ["通勤", "耳机"])
        self.assertEqual(self.client.get(f"/contents/{first['content_id']}").json()["tags"], ["通勤", "耳机"])
        self.assertEqual(len(self.client.get("/contents", params={"task_id": task["id"]}).json()), 2)
        # 手工修改也保留标签，且不能审核旧版本。
        edited = self.client.patch(f"/contents/{second['content_id']}", json={"body": "人工修改的正文"})
        self.assertEqual(edited.status_code, 201, edited.text)
        self.assertEqual(edited.json()["tags"], ["通勤", "耳机"])
        self.status(task["id"], "pending_review")
        self.review(task["id"], second["content_id"], expected=409)
        payload = {"task_id": task["id"], "content_id": edited.json()["id"], "platform": "rednote", "account_id": self.account_id}
        self.post("/publish-records", payload, 409)
        self.review(task["id"], edited.json()["id"])
        self.post("/publish-records", payload)

    def test_ai_generation_endpoint_records_failure_and_checks_authentication_and_ownership(self):
        from integrations.llm.deepseek import DeepSeekTimeoutError

        task = self.post("/tasks", {"product_id": self.product_id, "task_type": "generate",
                                    "input_text": "写一篇通勤文案", "platform_name": "rednote"})
        endpoint = f"/tasks/{task['id']}/generate"
        with patch("services.content_generation.retriever.retrieve", return_value=self.generation_mocks()) as retrieve, \
             patch("integrations.llm.deepseek.generate_text", side_effect=DeepSeekTimeoutError("DeepSeek 请求超时")) as model:
            self.post(endpoint, {}, 503)
            failed = self.client.get(f"/tasks/{task['id']}").json()
            self.assertEqual((failed["status"], failed["failed_from_status"]), ("failed", "running"))
            self.assertIn("超时", failed["error_message"])
            self.assertEqual(self.client.get("/contents", params={"task_id": task["id"]}).json(), [])
            retrieve.reset_mock()
            model.reset_mock()
            for extra in ({"user_id": self.user_id}, {"product_id": self.product_id}, {"user_requirement": "override"}):
                self.post(endpoint, extra, 422)
            authorization = self.client.headers.pop("Authorization")
            self.post(endpoint, {}, 401)
            self.client.headers["Authorization"] = authorization
            self.register_second_user()
            self.post(endpoint, {}, 404)
            retrieve.assert_not_called()
            model.assert_not_called()

    def test_published_url_and_idempotent_backfill(self):
        task_id, content_id = self.task_and_content()
        self.review(task_id, content_id)
        record = self.post("/publish-records", {"task_id": task_id, "content_id": content_id,
                                                "platform": "rednote", "account_id": self.account_id})
        endpoint = f"/publish-records/{record['id']}/result"
        data = {"publish_status": "published", "platform_post_id": "123", "account_id": self.account_id}
        self.post(endpoint, data, 200)
        linked = {**data, "platform_post_url": "https://www.xiaohongshu.com/explore/123"}
        self.assertEqual(self.post(endpoint, linked, 200)["platform_post_url"], linked["platform_post_url"])
        self.post(endpoint, linked, 200)
        self.post(endpoint, data, 200)
        self.post(endpoint, {**linked, "platform_post_url": "https://example.com/other"}, 409)
        self.post(endpoint, {**linked, "platform_post_url": "javascript:alert(1)"}, 422)
        self.assertEqual(self.client.get(f"/publish-records/{record['id']}").json()["platform_post_url"],
                         linked["platform_post_url"])

    def register_second_user(self):
        user = self.post("/auth/register", {"username": "second", "password": self.password})
        token = self.post("/auth/login", {"username": "second", "password": self.password}, 200)["access_token"]
        self.client.headers["Authorization"] = f"Bearer {token}"
        return user, token

    def test_auth_registration_login_logout(self):
        with Session(self.engine) as db:
            user = db.get(Users, self.user_id)
            self.assertTrue(user.password_hash.startswith("$argon2"))
            self.assertNotEqual(user.password_hash, self.password)
        duplicate = {"username": "OPERATOR", "password": self.password}
        self.post("/auth/register", duplicate, 409)
        self.post("/auth/register", {**duplicate, "username": "new", "password": "short"}, 422)
        self.post("/auth/register", {**duplicate, "username": "new", "is_admin": True}, 422)
        self.post("/auth/register", {**duplicate, "username": "new", "name": "Not accepted"}, 422)
        definition = self.client.get("/openapi.json").json()["components"]["schemas"]["RegisterRequest"]
        self.assertNotIn("name", definition["properties"])
        bad = self.post("/auth/login", {"username": "operator", "password": "wrong-password"}, 401)
        missing = self.post("/auth/login", {"username": "missing", "password": "wrong-password"}, 401)
        self.assertEqual(bad, missing)
        other_session = self.post("/auth/login", {"username": "operator", "password": self.password}, 200)["access_token"]
        self.assertEqual(self.client.post("/auth/logout").status_code, 204)
        self.assertEqual(self.client.get("/products").status_code, 401)
        self.client.headers["Authorization"] = f"Bearer {other_session}"
        self.assertEqual(self.client.get("/products").status_code, 200)
        for path in ("/users", f"/users/{self.user_id}"):
            self.assertEqual(self.client.get(path).status_code, 404)

    def test_anonymous_and_invalid_jwt(self):
        self.client.headers.pop("Authorization")
        for path in ("/tasks", "/contents", "/products", "/reviews", "/publish-records",
                     "/prompts", "/rules/custom", "/rules/platform", "/rednote/cases", "/rednote/analyses"):
            self.assertEqual(self.client.get(path).status_code, 401, path)
        now = datetime.now(timezone.utc)
        claims = {"sub": str(self.user_id), "jti": "no-session", "iat": now,
                  "exp": now + timedelta(hours=1), "iss": JWT_ISSUER, "aud": JWT_AUDIENCE, "type": "access"}
        invalid_tokens = ["bad.token.value", jwt.encode(claims, "wrong-key-" * 8, algorithm="HS256"),
                          jwt.encode({**claims, "exp": now - timedelta(seconds=1)}, require_jwt_secret(), algorithm="HS256"),
                          jwt.encode(claims, require_jwt_secret(), algorithm="HS256"),
                          jwt.encode({**claims, "aud": "other-app"}, require_jwt_secret(), algorithm="HS256")]
        for token in invalid_tokens:
            self.client.headers["Authorization"] = f"Bearer {token}"
            response = self.client.get("/products")
            self.assertEqual(response.status_code, 401, response.text)
            self.assertEqual(response.headers["www-authenticate"], "Bearer")

    def test_cross_user_reads_writes_and_lists(self):
        task_id, content_id = self.task_and_content()
        review = self.review(task_id, content_id)
        record = self.post("/publish-records", {"task_id": task_id, "content_id": content_id,
                                                 "platform": "rednote", "account_id": self.account_id})
        prompt = self.post("/prompts", {"platform": "rednote", "name": "Private", "prompt_content": "Private"})
        rule = self.post("/rules/custom", {"content": "Private rule"})
        case = self.post("/rednote/cases", {"name": "Private case", "link": "https://example.com/shared-link"})
        analysis = self.post("/rednote/analyses", {"cases_id": case["id"], "content": "Private analysis"})
        self.register_second_user()
        for path in (f"/products/{self.product_id}", f"/tasks/{task_id}", f"/contents/{content_id}",
                     f"/reviews/{review['id']}", f"/publish-records/{record['id']}", f"/prompts/{prompt['id']}",
                     f"/rules/custom/{rule['id']}", f"/rednote/cases/{case['id']}", f"/rednote/analyses/{analysis['id']}"):
            self.assertEqual(self.client.get(path).status_code, 404, path)
        for path in ("/products", "/tasks", "/contents", "/reviews", "/publish-records", "/prompts",
                     "/rules/custom", "/rednote/cases", "/rednote/analyses"):
            response = self.client.get(path, params={"user_id": self.user_id, "task_id": task_id})
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(response.json(), [], path)
        self.assertEqual(self.client.patch(f"/tasks/{task_id}/status", json={"status": "cancelled"}).status_code, 404)
        self.assertEqual(self.client.patch(f"/contents/{content_id}", json={"body": "Intrusion"}).status_code, 404)
        self.assertEqual(self.client.put(f"/tasks/{task_id}/checkpoint", json={"checkpoint": {}}).status_code, 404)
        self.post(f"/tasks/{task_id}/fail", {"checkpoint": {}, "error_message": "Intrusion"}, 404)
        self.post(f"/tasks/{task_id}/resume", None, 404)
        self.post("/tasks", {"product_id": self.product_id, "task_type": "generate", "input_text": "Intrusion"}, 404)
        self.post("/contents", {"task_id": task_id, "title": "Intrusion", "body": "Intrusion"}, 404)
        self.review(task_id, content_id, expected=404)
        self.post("/publish-records", {"task_id": task_id, "content_id": content_id,
                                       "platform": "rednote", "account_id": self.account_id}, 404)
        self.post(f"/publish-records/{record['id']}/result", {"publish_status": "failed", "error_message": "Intrusion"}, 404)
        self.post(f"/publish-records/{record['id']}/retry", None, 404)
        self.assertEqual(self.client.put(f"/prompts/{prompt['id']}", json={"platform": "rednote", "name": "x", "prompt_content": "x"}).status_code, 404)
        self.assertEqual(self.client.put(f"/rules/custom/{rule['id']}", json={"content": "Intrusion"}).status_code, 404)
        self.assertEqual(self.client.put(f"/rednote/cases/{case['id']}", json={"name": "x", "link": "https://example.com/x"}).status_code, 404)
        self.post("/rednote/analyses", {"cases_id": case["id"], "content": "Intrusion"}, 404)
        # 相同公开链接可由不同用户各自收藏，不能通过全局唯一性泄露对方案例。
        self.post("/rednote/cases", {"name": "Own case", "link": case["link"]})
        self.client.headers["Authorization"] = f"Bearer {self.token}"
        self.assertEqual(self.client.get(f"/tasks/{task_id}").json()["status"], "publishing")

    def test_private_product_upload_and_download(self):
        endpoint = f"/products/{self.product_id}/documents"
        response = self.client.post(endpoint, files={"file": ("../product.md", b"Product facts", "text/markdown")})
        self.assertEqual(response.status_code, 201, response.text)
        document = response.json()
        self.assertEqual(document["filename"], "product.md")
        download = f"{endpoint}/{document['id']}/download"
        self.assertEqual(self.client.get(download).content, b"Product facts")
        for filename, body in (("data.exe", b"bad"), ("empty.txt", b""), ("bad.pdf", b"not pdf")):
            response = self.client.post(endpoint, files={"file": (filename, body)})
            self.assertEqual(response.status_code, 422, response.text)
        with patch("services.product.MAX_UPLOAD_BYTES", 3):
            response = self.client.post(endpoint, files={"file": ("large.txt", b"too big")})
            self.assertEqual(response.status_code, 422)
        self.assertEqual(len(list(Path(self.storage.name).rglob("*.*"))), 1)
        self.register_second_user()
        self.assertEqual(self.client.get(download).status_code, 404)
        self.assertEqual(self.client.get(endpoint).status_code, 404)
        self.assertEqual(self.client.post(endpoint, files={"file": ("x.txt", b"x")}).status_code, 404)
        self.assertEqual(self.client.delete(f"{endpoint}/{document['id']}").status_code, 404)
        self.assertEqual(self.client.delete(f"/products/{self.product_id}").status_code, 404)
        self.assertEqual(self.client.patch(f"/products/{self.product_id}", json={"name": "Intrusion"}).status_code, 404)
        self.assertEqual(self.client.get("/products").json(), [])

    def mock_rag_model(self):
        self.embedding_requests = []
        self.embedding_http_status = 200

        def handler(request):
            self.embedding_requests.append(request)
            data = json.loads(request.content)
            return httpx.Response(self.embedding_http_status, json={"model": data["model"], "data": [
                {"index": i, "embedding": [1.0] + [0.0] * 63} for i, _ in enumerate(data["input"])]})

        http = httpx.Client(transport=httpx.MockTransport(handler))
        self.addCleanup(http.close)
        for target, value in [("config.EMBEDDING_TOKENIZER_PATH", ""),
                              ("config.EMBEDDING_MODEL", "text-embedding-v3"), ("config.EMBEDDING_DIMENSIONS", 64)]:
            patcher = patch(target, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        embedder = QwenEmbeddings(api_key="test-key", base_url="https://embedding.example/v1", max_retries=0,
                                  http_client=http)
        patcher = patch("rag.retriever.QwenEmbeddings", return_value=embedder)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_rag_http_upload_index_retrieve_and_ownership(self):
        self.mock_rag_model()
        endpoint = f"/products/{self.product_id}/rag"
        response = self.client.post(endpoint + "/documents", files={"file": ("facts.txt", b"Product facts")})
        self.assertEqual(response.status_code, 201, response.text)
        result = response.json()
        self.assertEqual(result["status"], "indexed")
        self.assertEqual(result["chunk_count"], 1)
        document_id = result["document_id"]
        self.assertEqual(self.client.get(f"/products/{self.product_id}/documents").json()[0]["status"], "indexed")
        self.post(endpoint + f"/documents/{document_id}/index", {}, 200)
        hit = SearchResult("a" * 64, "Product facts", self.user_id, self.product_id, document_id,
                           "facts.txt", 0, 0.9, {"user_id": self.user_id})
        with patch("rag.retriever.search_vectors", return_value=[hit]):
            retrieved = self.post(endpoint + "/retrieve", {"query": "  Product use?  "}, 200)
        self.assertEqual(retrieved["query"], "Product use?")
        self.assertIn("[1]", retrieved["context"])
        self.assertEqual(retrieved["sources"][0]["filename"], "facts.txt")
        self.assertEqual(retrieved["sources"][0]["document_id"], document_id)
        self.assertNotIn("vector", retrieved["sources"][0])
        self.embedding_requests.clear()
        self.register_second_user()
        self.post(endpoint + "/retrieve", {"query": "Product use?"}, 404)
        self.post(endpoint + f"/documents/{document_id}/index", {}, 404)
        response = self.client.post(endpoint + "/documents", files={"file": ("x.txt", b"x")})
        self.assertEqual(response.status_code, 404)
        self.assertEqual(self.embedding_requests, [])

    def test_rag_failure_returns_document_id_and_retry_works(self):
        self.mock_rag_model()
        self.embedding_http_status = 401
        endpoint = f"/products/{self.product_id}/rag"
        response = self.client.post(endpoint + "/documents", files={"file": ("facts.txt", b"Product facts")})
        self.assertEqual(response.status_code, 503, response.text)
        documents = self.client.get(f"/products/{self.product_id}/documents").json()
        self.assertEqual(len(documents), 1)
        self.assertEqual(documents[0]["status"], "index_failed")
        self.assertIn(str(documents[0]["id"]), response.json()["detail"])
        self.embedding_http_status = 200
        self.post(endpoint + f"/documents/{documents[0]['id']}/index", {}, 200)
        self.assertEqual(len(list(Path(self.storage.name).rglob("*.txt"))), 1)

    def test_rag_http_validation_and_empty_results(self):
        self.mock_rag_model()
        endpoint = f"/products/{self.product_id}/rag"
        for payload in [{"query": "  "}, {"query": "x", "user_id": self.user_id},
                        {"query": "x", "top_k": 21}, {"query": "x", "max_context_chars": 0}]:
            self.post(endpoint + "/retrieve", payload, 422)
        response = self.client.post(endpoint + "/documents?chunk_size=10&chunk_overlap=10",
                                    files={"file": ("facts.txt", b"facts")})
        self.assertEqual(response.status_code, 422, response.text)
        self.assertEqual(self.post(endpoint + "/retrieve", {"query": "x"}, 200)["sources"], [])
        self.assertEqual(self.embedding_requests, [])
        self.client.headers.pop("Authorization")
        self.post(endpoint + "/retrieve", {"query": "x"}, 401)

    def test_product_update_and_delete(self):
        endpoint = f"/products/{self.product_id}"
        response = self.client.patch(endpoint, json={"name": "  Updated  ", "description": "Description"})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["name"], "Updated")
        response = self.client.patch(endpoint, json={"description": None})
        self.assertEqual(response.json()["name"], "Updated")
        self.assertIsNone(response.json()["description"])
        for data in ({}, {"name": None}, {"name": " "}, {"user_id": 999}):
            self.assertEqual(self.client.patch(endpoint, json=data).status_code, 422)
        response = self.client.delete(endpoint)
        self.assertEqual(response.status_code, 204, response.text)
        self.assertEqual(response.content, b"")
        self.assertEqual(self.client.get(endpoint).status_code, 404)

    def test_product_delete_protects_tasks_and_documents(self):
        self.task_and_content()
        self.assertEqual(self.client.delete(f"/products/{self.product_id}").status_code, 409)
        product = self.post("/products", {"name": "Documents only"})
        endpoint = f"/products/{product['id']}"
        document = self.client.post(endpoint + "/documents", files={"file": ("facts.txt", b"facts")}).json()
        self.assertEqual(self.client.delete(endpoint).status_code, 409)
        self.assertEqual(self.client.delete(f"{endpoint}/documents/{document['id']}").status_code, 204)
        self.assertEqual(self.client.delete(endpoint).status_code, 204)

    def test_document_delete_and_storage_failure_retry(self):
        endpoint = f"/products/{self.product_id}/documents"
        document = self.client.post(endpoint, files={"file": ("facts.txt", b"facts")}).json()
        delete_endpoint = f"{endpoint}/{document['id']}"
        with patch("pathlib.Path.unlink", side_effect=PermissionError("file locked")):
            response = self.client.delete(delete_endpoint)
            self.assertEqual(response.status_code, 503, response.text)
        self.assertEqual(self.client.get(endpoint).json()[0]["status"], "deleting")
        self.assertEqual(self.client.get(delete_endpoint + "/download").status_code, 404)
        self.assertEqual(self.client.delete(delete_endpoint).status_code, 204)
        self.assertEqual(self.client.get(endpoint).json(), [])
        self.assertEqual(list(Path(self.storage.name).rglob("*.txt")), [])

    def test_document_delete_database_failure_retry(self):
        endpoint = f"/products/{self.product_id}/documents"
        document = self.client.post(endpoint, files={"file": ("facts.txt", b"facts")}).json()
        delete_endpoint = f"{endpoint}/{document['id']}"
        original_commit = Session.commit
        commits = 0

        def failing_second_commit(session):
            nonlocal commits
            commits += 1
            if commits == 2:
                raise SQLAlchemyError("simulated commit failure")
            return original_commit(session)

        with patch.object(Session, "commit", failing_second_commit):
            response = self.client.delete(delete_endpoint)
            self.assertEqual(response.status_code, 503, response.text)
        self.assertEqual(self.client.get(endpoint).json()[0]["status"], "deleting")
        self.assertEqual(list(Path(self.storage.name).rglob("*.txt")), [])
        self.assertEqual(self.client.delete(delete_endpoint).status_code, 204)
        self.assertEqual(self.client.get(endpoint).json(), [])

    def test_delete_cors_preflight(self):
        if not CORS_ORIGINS:
            self.skipTest("CORS disabled by environment")
        response = self.client.options(f"/products/{self.product_id}", headers={
            "Origin": CORS_ORIGINS[0], "Access-Control-Request-Method": "DELETE",
            "Access-Control-Request-Headers": "authorization",
        })
        self.assertEqual(response.status_code, 200)
        self.assertIn("DELETE", response.headers["access-control-allow-methods"])

    def test_document_delete_rejects_path_outside_product(self):
        endpoint = f"/products/{self.product_id}/documents"
        document = self.client.post(endpoint, files={"file": ("facts.txt", b"facts")}).json()
        outside = Path(self.storage.name) / "outside.txt"
        outside.write_text("must survive", encoding="utf-8")
        with Session(self.engine) as db:
            db.get(ProductDocument, document["id"]).storage_key = "outside.txt"
            db.commit()
        response = self.client.delete(f"{endpoint}/{document['id']}")
        self.assertEqual(response.status_code, 422, response.text)
        self.assertEqual(outside.read_text(encoding="utf-8"), "must survive")
        self.assertEqual(self.client.get(endpoint).json()[0]["status"], "uploaded")

    def test_custom_upload_directory_end_to_end(self):
        from rag.document_loader import load_document
        from services.product import get_document, document_path

        custom = Path(self.storage.name) / "custom-root"
        endpoint = f"/products/{self.product_id}/documents"
        with patch("services.product.UPLOAD_DIR", custom):
            response = self.client.post(endpoint, files={"file": ("manual.md", b"# Product facts")})
            self.assertEqual(response.status_code, 201, response.text)
            doc_id = response.json()["id"]
            with Session(self.engine) as db:
                db.info["user_id"] = self.user_id
                document = get_document(db, self.product_id, doc_id)
                path = document_path(document, user_id=self.user_id)
                self.assertEqual(path.parent, (custom / str(self.user_id) / str(self.product_id)).resolve())
                self.assertTrue(document.storage_key.startswith("uploads/"))
                self.assertEqual(load_document(db, self.product_id, doc_id), "# Product facts")
            self.assertEqual(self.client.get(f"{endpoint}/{doc_id}/download").content, b"# Product facts")
            self.assertEqual(self.client.delete(f"{endpoint}/{doc_id}").status_code, 204)
            self.assertFalse(path.exists())


if __name__ == "__main__":
    unittest.main()
