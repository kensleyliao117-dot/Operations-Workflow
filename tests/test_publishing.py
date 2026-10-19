"""本地发布的账号、版本、结果未知、幂等和浏览器提交门禁测试。"""
from io import BytesIO
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
import unittest

from PIL import Image
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from sqlalchemy.orm import Session

from integrations.publishers.base import PublishOutcome, PublishPayload, PublisherError
from integrations.publishers.locking import publishing_lock
from integrations.publishers.rednote import (RednotePublisher, parse_response, read_account, is_publish_response,
                                           _set_visibility, _verify_visibility)
from models.content import Content
from models.platform_account import PlatformAccount
from models.publish_record import PublishRecord
from models.rules import PlatformRule
from models.task import Task
from schemas.rules import RuleAIDraft
import test_business_api as api_tests


USER_ID = "1" * 24
NOTE_ID = "2" * 24


def png():
    data = BytesIO()
    Image.new("RGB", (8, 8), "white").save(data, format="PNG")
    return data.getvalue()


class PublishingAPITest(unittest.TestCase):
    tearDown = api_tests.BusinessAPITest.tearDown
    post = api_tests.BusinessAPITest.post
    status = api_tests.BusinessAPITest.status
    review = api_tests.BusinessAPITest.review
    register_second_user = api_tests.BusinessAPITest.register_second_user

    def setUp(self):
        api_tests.BusinessAPITest.setUp(self)
        self.root = Path(self.storage.name) / "generated_images"
        p = patch("config.GENERATED_IMAGE_DIR", self.root)
        p.start()
        self.addCleanup(p.stop)
        p = patch("agents.rule_agent.check_content", return_value=RuleAIDraft(risks=[]))
        p.start()
        self.addCleanup(p.stop)
        with Session(self.engine) as db:
            db.get(PlatformAccount, self.account_id).platform_user_id = USER_ID
            db.add(PlatformRule(platform_name="rednote", content="No unsupported claims"))
            db.commit()
        filename = "a" * 32 + ".png"
        directory = self.root / f"user_{self.user_id}" / f"product_{self.product_id}"
        directory.mkdir(parents=True)
        self.path = directory / filename
        self.path.write_bytes(png())
        task = self.post("/tasks", {"product_id": self.product_id, "platform_name": "rednote",
                                   "task_type": "generate", "input_text": "Create a post"})
        self.task_id = task["id"]
        content = self.post("/contents", {"task_id": self.task_id, "title": "Title", "body": "Body", "tags": ["music"],
             "image_assets": [{"storage_key": f"generated_images/user_{self.user_id}/product_{self.product_id}/{filename}"}]})
        self.content_id = content["id"]
        self.status(self.task_id, "running")
        self.post("/rules/checks", {"content_id": self.content_id})
        self.status(self.task_id, "pending_review")
        self.approval = self.review(self.task_id, self.content_id)
        self.request = {"task_id": self.task_id, "content_id": self.content_id, "account_id": self.account_id,
                        "platform": "rednote", "publisher_backend": "chrome", "idempotency_key": "publish-request-0001"}
        p = patch("services.publishing.RednotePublisher")
        self.publisher = p.start().return_value
        self.addCleanup(p.stop)
        self.publisher.publish.side_effect = self.success

    def success(self, payload, *, before_submit):
        before_submit()
        return PublishOutcome("published", NOTE_ID, f"https://www.xiaohongshu.com/explore/{NOTE_ID}")

    def record(self):
        return self.post("/publish-records", self.request)

    def execute(self, record, expected=200):
        return self.post(f"/publish-records/{record['id']}/execute", {"confirm_account": True}, expected)

    def result(self, record, body, expected=200):
        return self.post(f"/publish-records/{record['id']}/result", {"account_id": self.account_id, **body}, expected)

    def test_publishes_exact_approved_content_and_records_feedback_link(self):
        record = self.record()
        self.assertEqual(record["review_id"], self.approval["id"])
        self.assertEqual(record["content_version"], 1)
        self.assertEqual(record["delivery_phase"], "prepared")
        saved = self.execute(record)
        self.assertEqual(saved["publish_status"], "published")
        self.assertEqual(saved["platform_post_id"], NOTE_ID)
        self.assertIsNotNone(saved["platform_post_url"])
        self.assertIsNotNone(saved["submitted_at"])
        self.assertEqual(self.client.get(f"/tasks/{self.task_id}").json()["status"], "completed")
        payload = self.publisher.publish.call_args.args[0]
        self.assertEqual((payload.content_id, payload.version, payload.title, payload.images), (self.content_id, 1, "Title", (self.path,)))
        metrics = self.post(f"/publish-records/{record['id']}/metrics", {"metrics": {"likes": 4}})
        self.assertEqual(metrics["account_id"], self.account_id)
        self.assertEqual(self.execute(record)["platform_post_id"], NOTE_ID)
        self.publisher.publish.assert_called_once()

    def test_request_key_replay_before_and_after_publish_never_creates_duplicates(self):
        first = self.record()
        self.assertEqual(self.record()["id"], first["id"])
        self.post("/publish-records", {**self.request, "idempotency_key": "different-request"}, 409)
        self.execute(first)
        self.assertEqual(self.record()["id"], first["id"])
        self.assertEqual(len(self.client.get("/publish-records").json()), 1)
        self.post("/publish-records", {**self.request, "account_id": 999}, 409)

    def test_private_visibility_is_persisted_and_immutable_for_request_replay(self):
        self.request["visibility"] = "private"
        record = self.record()
        self.assertEqual(record["visibility"], "private")
        self.post("/publish-records", {**self.request, "visibility": "public"}, 409)
        self.execute(record)
        self.assertEqual(self.publisher.publish.call_args.args[0].visibility, "private")

    def test_auth_ownership_and_local_origin_guard_precede_browser(self):
        record = self.record()
        self.client.headers.pop("Authorization")
        self.execute(record, 401)
        self.register_second_user()
        self.execute(record, 404)
        self.post("/publish-records", self.request, 404)
        self.publisher.publish.assert_not_called()

    def test_forwarded_or_untrusted_origin_requests_cannot_publish(self):
        record = self.record()
        for headers in ({"X-Forwarded-For": "127.0.0.1"}, {"Origin": "https://example.com"}):
            response = self.client.post(f"/publish-records/{record['id']}/execute", json={"confirm_account": True}, headers=headers)
            self.assertEqual(response.status_code, 403)
        self.publisher.publish.assert_not_called()

    def test_requires_explicit_account_confirmation_and_request_key(self):
        self.post("/publish-records", {**self.request, "idempotency_key": None}, 422)
        record = self.record()
        self.post(f"/publish-records/{record['id']}/execute", {}, 422)
        self.post(f"/publish-records/{record['id']}/execute", {"confirm_account": False}, 422)
        self.publisher.publish.assert_not_called()

    def test_unapproved_or_newer_content_never_starts_publication(self):
        with Session(self.engine) as db:
            db.get(Task, self.task_id).status = "pending_review"
            db.commit()
        self.post("/publish-records", self.request, 409)
        with Session(self.engine) as db:
            task = db.get(Task, self.task_id)
            task.status = "approved"
            db.add(Content(task_id=self.task_id, title="Unreviewed", body="New", version=2, image_assets=[]))
            db.commit()
        self.post("/publish-records", self.request, 409)
        self.publisher.publish.assert_not_called()

    def test_image_change_after_prepare_fails_before_browser_and_preserves_content(self):
        record = self.record()
        self.path.write_bytes(png() + b"different bytes")
        saved = self.execute(record)
        self.assertEqual(saved["publish_status"], "failed")
        self.assertEqual(self.client.get(f"/contents/{self.content_id}").json()["body"], "Body")
        self.publisher.publish.assert_not_called()

    def test_rule_change_is_advisory_and_does_not_block_approved_publication(self):
        record = self.record()
        with Session(self.engine) as db:
            rule = db.query(PlatformRule).first()
            rule.version += 1
            db.commit()
        saved = self.execute(record)
        self.assertEqual(saved["publish_status"], "published")
        self.publisher.publish.assert_called_once()

    def test_prepare_failure_can_retry_new_record_without_overwriting_history(self):
        record = self.record()
        self.publisher.publish.side_effect = PublisherError("Account mismatch")
        self.assertEqual(self.execute(record)["publish_status"], "failed")
        self.post(f"/publish-records/{record['id']}/retry", None, 409)
        retried = self.post(f"/publish-records/{record['id']}/retry", {"idempotency_key": "publish-retry-0002"})
        self.assertNotEqual(retried["id"], record["id"])
        self.assertEqual(self.post(f"/publish-records/{record['id']}/retry", {"idempotency_key": "publish-retry-0002"})["id"], retried["id"])
        self.publisher.publish.side_effect = self.success
        self.execute(retried)
        self.assertEqual(self.client.get(f"/publish-records/{record['id']}").json()["publish_status"], "failed")

    def test_disconnect_after_submit_is_unknown_and_cannot_be_retried_blindly(self):
        def disconnect(payload, *, before_submit):
            before_submit()
            raise RuntimeError("private browser details")
        self.publisher.publish.side_effect = disconnect
        record = self.record()
        saved = self.execute(record)
        self.assertEqual(saved["publish_status"], "unknown")
        self.assertNotIn("private", saved["error_message"])
        self.assertEqual(self.client.get(f"/tasks/{self.task_id}").json()["status"], "publishing")
        self.post(f"/publish-records/{record['id']}/retry", {"idempotency_key": "retry-unknown"}, 409)
        self.assertEqual(self.execute(record)["publish_status"], "unknown")
        self.result(record, {"publish_status": "failed", "error_message": "Not found yet"}, 409)
        confirmed = self.result(record, {"publish_status": "published", "platform_post_id": NOTE_ID})
        self.assertEqual(confirmed["publish_status"], "published")
        self.publisher.publish.assert_called_once()

    def test_unknown_confirmed_not_published_allows_explicit_new_attempt(self):
        def unknown(payload, *, before_submit):
            before_submit()
            return PublishOutcome("unknown", error="No trusted post ID")
        self.publisher.publish.side_effect = unknown
        record = self.record()
        self.execute(record)
        self.result(record, {"publish_status": "failed", "error_message": "Confirmed absent", "confirm_not_published": True})
        new = self.post(f"/publish-records/{record['id']}/retry", {"idempotency_key": "confirmed-retry"})
        self.assertEqual(new["publish_status"], "publishing")

    def test_process_interruption_marker_is_not_replayed(self):
        record = self.record()
        with Session(self.engine) as db:
            db.get(PublishRecord, record["id"]).delivery_phase = "submitting"
            db.commit()
        self.assertEqual(self.execute(record)["publish_status"], "unknown")
        self.publisher.publish.assert_not_called()

    def test_reentrant_execute_cannot_reuse_browser_or_finalize_during_execution(self):
        record = self.record()
        def concurrent(payload, *, before_submit):
            self.execute(record, 409)
            self.result(record, {"publish_status": "failed", "error_message": "Cancel"}, 409)
            before_submit()
            return PublishOutcome("published", NOTE_ID, f"https://www.xiaohongshu.com/explore/{NOTE_ID}")
        self.publisher.publish.side_effect = concurrent
        self.assertEqual(self.execute(record)["publish_status"], "published")

    def test_post_url_must_match_rednote_note_and_cannot_replace_success(self):
        record = self.record()
        self.result(record, {"publish_status": "published", "platform_post_id": NOTE_ID,
                            "platform_post_url": "https://example.com/a"}, 409)
        self.result(record, {"publish_status": "published", "platform_post_id": NOTE_ID})
        self.result(record, {"publish_status": "published", "platform_post_id": "3" * 24}, 409)

    def test_confirmed_historical_publish_time_is_saved_in_utc_and_cannot_be_overwritten(self):
        record = self.record()
        actual = (datetime.now(timezone.utc) - timedelta(minutes=10)).replace(microsecond=0)
        data = {"publish_status": "published", "platform_post_id": NOTE_ID,
                "published_at": actual.astimezone(timezone(timedelta(hours=8))).isoformat()}
        saved = self.result(record, data)
        self.assertEqual(saved["published_at"], actual.replace(tzinfo=None).isoformat())
        self.result(record, {**data, "published_at": (actual + timedelta(minutes=1)).isoformat()}, 409)

    def test_invalid_publish_timestamps_are_rejected(self):
        record = self.record()
        for value in ("2026-01-01T00:00:00", (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()):
            self.result(record, {"publish_status": "published", "platform_post_id": NOTE_ID, "published_at": value}, 422)
        self.result(record, {"publish_status": "failed", "error_message": "Not published",
            "published_at": (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()}, 422)


class RednoteAdapterTest(unittest.TestCase):
    def test_private_selection_requires_verified_final_label(self):
        page, control, option = Mock(), Mock(), Mock()
        for item in (control, option):
            item.count.return_value = 1
            item.nth.return_value = item
            item.is_visible.return_value = True
        control.inner_text.return_value = "公开可见"
        options = Mock()
        options.filter.return_value = option
        page.locator.side_effect = lambda selector: control if selector == 'div.permission-card-wrapper div.d-select-content' else options
        option.click.side_effect = lambda: setattr(control.inner_text, "return_value", "仅自己可见")
        _set_visibility(page, "private")
        option.click.assert_called_once()
        control.inner_text.return_value = "公开可见 仅自己可见"
        with self.assertRaises(PublisherError):
            _verify_visibility(page, "private")

    def test_private_selection_failure_does_not_reach_submission_guard(self):
        payload = PublishPayload(1, 1, "Title", "Body", (), (Path("file.png"),), USER_ID, "private")
        with patch("integrations.publishers.rednote.ChromeBrowser") as chrome, \
                patch("integrations.publishers.rednote.require_local_browser"), \
                patch("integrations.publishers.rednote._set_visibility", side_effect=PublisherError("No private option")):
            page, button = self.page(chrome, payload)
            guard = Mock()
            with self.assertRaises(PublisherError):
                RednotePublisher().publish(payload, before_submit=guard)
            guard.assert_not_called()
            button.click.assert_not_called()

    def page(self, chrome, payload):
        page = chrome.return_value.__enter__.return_value.open_page.return_value
        page.url = "https://creator.xiaohongshu.com/publish/publish"
        page.evaluate.return_value = [USER_ID]
        page.get_by_text.return_value.count.return_value = 0
        def locator():
            result = Mock()
            result.count.return_value = 1
            result.nth.return_value = result
            result.is_visible.return_value = True
            result.is_enabled.return_value = True
            result.get_attribute.return_value = None
            return result
        uploads, body, previews, visibility = locator(), locator(), locator(), locator()
        visibility.inner_text.return_value = "公开可见"
        previews.evaluate_all.return_value = [["uploaded-image"]]
        body.inner_text.return_value = payload.editor_body
        body.evaluate.return_value = payload.editor_body
        page.locator.side_effect = lambda selector: (uploads if 'type="file"' in selector else
                                                   body if selector == '[contenteditable="true"]' else
                                                   visibility if selector == 'div.permission-card-wrapper div.d-select-content' else previews)
        title, button = locator(), locator()
        title.input_value.return_value = payload.title
        page.get_by_placeholder.return_value = title
        page.get_by_role.return_value = button
        response = SimpleNamespace(status=200, json=lambda: {"code": 0, "data": {"id": NOTE_ID}})
        page.expect_response.return_value.__enter__.return_value = page.expect_response.return_value
        page.expect_response.return_value.value = response
        return page, button

    def test_click_occurs_once_after_guard_and_timeout_is_not_reported_as_failure(self):
        payload = PublishPayload(1, 1, "Title", "Body", (), (Path("file.png"),), USER_ID)
        for timeout in (False, True):
            with self.subTest(timeout=timeout), patch("integrations.publishers.rednote.ChromeBrowser") as chrome, \
                    patch("integrations.publishers.rednote.require_local_browser"):
                page, button = self.page(chrome, payload)
                order = []
                def click(**kwargs):
                    order.append("click")
                    if timeout:
                        raise PlaywrightTimeoutError("sensitive response")
                button.click.side_effect = click
                result = RednotePublisher().publish(payload, before_submit=lambda: order.append("guard"))
                self.assertEqual(order, ["guard", "click"])
                self.assertEqual(result.status, "unknown" if timeout else "published")
                chrome.return_value.__enter__.return_value.preserve_page.assert_called_once_with(page)

    def test_guard_rejection_never_clicks_publish(self):
        payload = PublishPayload(1, 1, "Title", "Body", (), (Path("file.png"),), USER_ID)
        with patch("integrations.publishers.rednote.ChromeBrowser") as chrome, \
                patch("integrations.publishers.rednote.require_local_browser"):
            page, button = self.page(chrome, payload)
            with self.assertRaises(PublisherError):
                RednotePublisher().publish(payload, before_submit=Mock(side_effect=PublisherError("Guard denied")))
            chrome.return_value.__enter__.return_value.preserve_page.assert_called_once_with(page)
            button.click.assert_not_called()

    def test_response_requires_trusted_success_id_and_handles_uncertainty(self):
        def response(body, status=200):
            return SimpleNamespace(status=status, json=lambda: body)
        self.assertEqual(parse_response(response({"code": 0, "data": {"id": NOTE_ID}})).status, "published")
        self.assertEqual(parse_response(response({"code": -1, "msg": "secret"})).status, "failed")
        for value in ({"code": 0, "data": {}}, {"code": 0, "data": {"id": "invalid"}}, [], {}):
            self.assertEqual(parse_response(response(value)).status, "unknown")
        self.assertEqual(parse_response(response({"code": 0}, 500)).status, "unknown")

    def test_response_listener_ignores_unrelated_requests(self):
        def response(url, method="POST"):
            return SimpleNamespace(url=url, request=SimpleNamespace(method=method))
        self.assertTrue(is_publish_response(response("https://edith.xiaohongshu.com/api/media/v1/note")))
        self.assertFalse(is_publish_response(response("https://example.com/api/media/v1/note")))
        self.assertFalse(is_publish_response(response("https://edith.xiaohongshu.com/api/media/v1/note/draft")))
        self.assertFalse(is_publish_response(response("https://edith.xiaohongshu.com/api/media/v1/note", "GET")))

    def test_account_identity_must_be_unique(self):
        for value in ([], [USER_ID, "3" * 24], ["nickname"]):
            page = Mock()
            page.evaluate.return_value = value
            with self.assertRaises(PublisherError):
                read_account(page)

    def test_account_mismatch_never_reaches_submit_callback(self):
        payload = PublishPayload(1, 1, "Title", "Body", (), (Path("file.png"),), USER_ID)
        with patch("integrations.publishers.rednote.ChromeBrowser") as chrome, patch("integrations.publishers.rednote.require_local_browser"):
            page = chrome.return_value.__enter__.return_value.open_page.return_value
            page.url = "https://creator.xiaohongshu.com/publish/publish"
            page.evaluate.return_value = ["3" * 24]
            callback = Mock()
            with self.assertRaises(PublisherError):
                RednotePublisher().publish(payload, before_submit=callback)
            callback.assert_not_called()
            page.get_by_role.assert_not_called()


if __name__ == "__main__":
    unittest.main()
