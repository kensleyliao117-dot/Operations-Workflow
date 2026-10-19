"""前端会话恢复与本机浏览器只读接口，使用隔离数据库和浏览器替身。"""
import unittest
from unittest.mock import patch

from collectors.base import ChromeConnectionError
import test_business_api as business


class FrontendSupportTest(unittest.TestCase):
    setUp = business.BusinessAPITest.setUp
    tearDown = business.BusinessAPITest.tearDown
    post = business.BusinessAPITest.post

    def test_current_user_returns_only_public_profile_and_rejects_revoked_session(self):
        result = self.client.get("/auth/me")
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.json(), {"id": self.user_id, "username": "operator", "email": None})
        self.assertEqual(self.client.post("/auth/logout").status_code, 204)
        self.assertEqual(self.client.get("/auth/me").status_code, 401)

    def test_browser_status_is_authenticated_local_and_does_not_open_a_page(self):
        with patch("collectors.chrome.ChromeBrowser") as chrome:
            browser = chrome.return_value.__enter__.return_value
            browser.is_connected = True
            result = self.client.get("/rednote/browser-status")
            self.assertEqual(result.json(), {"connected": True, "message": "Chrome 已连接"})
            browser.open_page.assert_not_called()
            self.assertEqual(self.client.get("/rednote/browser-status", headers={"X-Forwarded-For": "127.0.0.1"}).status_code, 403)
            self.client.headers.pop("Authorization")
            self.assertEqual(self.client.get("/rednote/browser-status").status_code, 401)
            self.assertEqual(chrome.call_count, 1)
        self.client.headers["Authorization"] = f"Bearer {self.token}"
        with patch("collectors.chrome.ChromeBrowser", side_effect=ChromeConnectionError("Chrome 尚未开启")):
            self.assertEqual(self.client.get("/rednote/browser-status").json(), {"connected": False, "message": "Chrome 尚未开启"})

    def test_current_browser_account_is_read_only_and_guarded(self):
        with patch("collectors.chrome.ChromeBrowser") as chrome:
            browser = chrome.return_value.__enter__.return_value
            browser.open_page.return_value.evaluate.return_value = ["2" * 24]
            result = self.client.get("/platform-accounts/browser-current")
            self.assertEqual(result.json(), {"platform": "rednote", "platform_user_id": "2" * 24, "confirmation_required": True})
            accounts = self.client.get("/platform-accounts").json()
            self.assertEqual(len(accounts), 1)
            self.assertEqual(self.client.get("/platform-accounts/browser-current", headers={"Origin": "https://untrusted.example"}).status_code, 403)
            self.client.headers.pop("Authorization")
            self.assertEqual(self.client.get("/platform-accounts/browser-current").status_code, 401)
            self.assertEqual(chrome.call_count, 1)
