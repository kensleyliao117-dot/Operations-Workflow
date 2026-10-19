"""模拟 CDP，验证连接生命周期；不启动真实 Chrome 或访问任何网站。"""
import unittest
from unittest.mock import MagicMock, patch

from playwright.sync_api import Error as PlaywrightError, TimeoutError as PlaywrightTimeoutError

from collectors.base import ChromeConnectionError, ChromeTimeoutError
from collectors.chrome import ChromeBrowser
from config import ConfigurationError
from services.exceptions import InvalidInputError


class ChromeBrowserTest(unittest.TestCase):
    def setUp(self):
        self.patch_runtime = patch("collectors.chrome.sync_playwright")
        self.factory = self.patch_runtime.start()
        self.addCleanup(self.patch_runtime.stop)
        self.runtime = self.factory.return_value.start.return_value
        self.browser = self.runtime.chromium.connect_over_cdp.return_value
        self.browser.is_connected.return_value = True
        self.context = MagicMock()
        self.manual_page = MagicMock()
        self.context.pages = [self.manual_page]
        self.browser.contexts = [self.context]
        self.page = self.context.new_page.return_value
        self.page.is_closed.return_value = False

    def test_connection_reuses_login_context_and_cleans_only_owned_pages(self):
        with ChromeBrowser(endpoint_url="http://127.0.0.1:9222/", connect_timeout_ms=5000,
                           page_timeout_ms=15000) as chrome:
            self.assertIs(chrome.context, self.context)
            self.assertIs(chrome.connect(), chrome)
            page = chrome.open_page("https://www.xiaohongshu.com/explore/example?xsec_token=test-token")
            self.assertIs(page, self.page)
            self.runtime.chromium.connect_over_cdp.assert_called_once_with(
                "http://127.0.0.1:9222", timeout=5000, no_defaults=True,
            )
            self.page.goto.assert_called_once_with(
                "https://www.xiaohongshu.com/explore/example?xsec_token=test-token",
                wait_until="domcontentloaded", timeout=15000,
            )
        self.page.close.assert_called_once_with(run_before_unload=False)
        self.manual_page.close.assert_not_called()
        self.browser.close.assert_not_called()
        self.context.close.assert_not_called()
        self.browser.new_context.assert_not_called()
        self.runtime.chromium.launch.assert_not_called()
        self.runtime.stop.assert_called_once()
        self.assertFalse(chrome.is_connected)
        chrome.disconnect()
        self.runtime.stop.assert_called_once()

    def test_connection_failures_release_runtime_and_hide_low_level_details(self):
        for failure, expected in (
            (PlaywrightTimeoutError("private-endpoint-token"), ChromeTimeoutError),
            (PlaywrightError("private-endpoint-token"), ChromeConnectionError),
        ):
            with self.subTest(error=expected):
                self.runtime.chromium.connect_over_cdp.side_effect = failure
                self.runtime.stop.reset_mock()
                chrome = ChromeBrowser()
                with self.assertRaises(expected) as caught:
                    chrome.connect()
                self.assertNotIn("private-endpoint-token", str(caught.exception))
                self.assertFalse(chrome.is_connected)
                self.runtime.stop.assert_called_once()

    def test_preserved_publishing_page_survives_disconnect_after_error(self):
        with self.assertRaisesRegex(ValueError, "interrupted"):
            with ChromeBrowser() as chrome:
                page = chrome.open_page("https://creator.xiaohongshu.com/publish/publish")
                chrome.preserve_page(page)
                raise ValueError("interrupted")
        self.page.close.assert_not_called()
        self.manual_page.close.assert_not_called()
        self.browser.close.assert_not_called()
        self.runtime.stop.assert_called_once()

    def test_preserve_rejects_pages_not_created_by_current_connection(self):
        with ChromeBrowser() as chrome:
            with self.assertRaises(InvalidInputError):
                chrome.preserve_page(self.manual_page)
        self.manual_page.close.assert_not_called()

    def test_missing_context_does_not_create_anonymous_context_or_close_browser(self):
        self.browser.contexts = []
        with self.assertRaises(ChromeConnectionError):
            ChromeBrowser().connect()
        self.browser.new_context.assert_not_called()
        self.browser.close.assert_not_called()
        self.runtime.stop.assert_called_once()

    def test_navigation_failure_removes_new_page_and_keeps_manual_page(self):
        for failure, expected in (
            (PlaywrightTimeoutError("private-page-details"), ChromeTimeoutError),
            (PlaywrightError("private-page-details"), ChromeConnectionError),
        ):
            with self.subTest(error=expected), ChromeBrowser() as chrome:
                self.page.close.reset_mock()
                self.page.goto.side_effect = failure
                with self.assertRaises(expected) as caught:
                    chrome.open_page("https://www.xiaohongshu.com/explore/example")
                self.assertNotIn("private-page-details", str(caught.exception))
                self.page.close.assert_called_once()
                self.manual_page.close.assert_not_called()

    def test_unconnected_access_and_foreign_page_close_are_rejected(self):
        chrome = ChromeBrowser()
        with self.assertRaises(ChromeConnectionError):
            chrome.new_page()
        self.factory.assert_not_called()
        with chrome:
            with self.assertRaises(InvalidInputError):
                chrome.close_page(self.manual_page)
        self.manual_page.close.assert_not_called()

    def test_external_disconnect_is_detected_and_can_reconnect(self):
        chrome = ChromeBrowser().connect()
        self.browser.is_connected.return_value = False
        self.assertFalse(chrome.is_connected)
        with self.assertRaises(ChromeConnectionError):
            chrome.new_page()
        chrome.connect()
        self.assertEqual(self.runtime.chromium.connect_over_cdp.call_count, 2)
        self.browser.is_connected.return_value = True
        chrome.disconnect()
        self.assertEqual(self.runtime.stop.call_count, 2)

    def test_invalid_navigation_urls_do_not_create_or_navigate_pages(self):
        with ChromeBrowser() as chrome:
            for value in (None, "", "javascript:alert(1)", "file:///private.txt", "https://",
                          "https://name:secret@example.com", "https://example.com:bad", "https://bad host/"):
                with self.subTest(url_type=type(value).__name__), self.assertRaises(InvalidInputError):
                    chrome.open_page(value)
            self.context.new_page.assert_not_called()
            self.page.goto.assert_not_called()

    def test_invalid_configuration_does_not_start_playwright(self):
        for values in ({"endpoint_url": "ws://localhost:9222/devtools/page/1"},
                       {"connect_timeout_ms": 0}, {"page_timeout_ms": True}, {"page_timeout_ms": 1.5}):
            with self.subTest(fields=tuple(values)), self.assertRaises(ConfigurationError):
                ChromeBrowser(**values)
        self.factory.assert_not_called()

    def test_context_manager_preserves_original_error_and_disconnects(self):
        with self.assertRaisesRegex(ValueError, "parse failed"):
            with ChromeBrowser() as chrome:
                chrome.new_page()
                raise ValueError("parse failed")
        self.page.close.assert_called_once()
        self.runtime.stop.assert_called_once()

    def test_closed_page_and_driver_error_do_not_break_cleanup(self):
        with ChromeBrowser() as chrome:
            chrome.new_page()
            self.page.is_closed.return_value = True
            self.runtime.stop.side_effect = PlaywrightError("private-driver-details")
        self.page.close.assert_not_called()
        self.assertFalse(chrome.is_connected)

    def test_cross_thread_use_is_rejected_before_browser_operation(self):
        with ChromeBrowser() as chrome:
            with patch("collectors.chrome.get_ident", return_value=-1):
                with self.assertRaises(ChromeConnectionError):
                    chrome.new_page()
                with self.assertRaises(ChromeConnectionError):
                    chrome.disconnect()
            self.context.new_page.assert_not_called()


if __name__ == "__main__":
    unittest.main()
