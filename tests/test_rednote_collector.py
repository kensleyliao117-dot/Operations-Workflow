"""使用模拟页面检查单篇读取和资源清理，不访问真实小红书。"""
import unittest
from unittest.mock import MagicMock

from playwright.sync_api import Error as PlaywrightError, TimeoutError as PlaywrightTimeoutError

from collectors.base import CollectorError
from collectors.rednote import RednoteCollector, parse_metric
from services.exceptions import InvalidInputError


class RednoteCollectorTest(unittest.TestCase):
    def setUp(self):
        self.url = "https://www.xiaohongshu.com/explore/0123456789abcdef01234567"
        self.chrome = MagicMock()
        self.chrome.page_timeout_ms = 1000
        self.page = MagicMock()
        self.page.evaluate.return_value = {"image_urls": [], "metrics_raw": {}, "author_url": None}
        self.page.url = self.url + "?xsec_token=private-parameter"
        self.chrome.open_page.return_value = self.page
        self.chrome.context.pages = [self.page]
        self.title = MagicMock()
        self.title.count.return_value = 1
        self.title.inner_text.return_value = " 标题 "
        self.body = MagicMock()
        self.body.count.return_value = 1
        self.body.inner_text.return_value = " 正文第一行\n第二行 "
        self.waiting = MagicMock()
        self.page.locator.side_effect = lambda selector: {
            "#detail-title:visible": self.title,
            "#detail-desc:visible": self.body,
            "#detail-title:visible, #detail-desc:visible": self.waiting,
        }[selector]
        self.title.first = self.title
        self.body.first = self.body
        self.waiting.first = self.waiting
        self.collector = RednoteCollector(self.chrome)

    def test_link_read_preserves_navigation_parameters_and_cleans_owned_page(self):
        result = self.collector.collect(self.page.url)
        self.assertEqual((result.title, result.body), ("标题", "正文第一行\n第二行"))
        self.assertEqual(result.source_url, self.url)
        self.chrome.open_page.assert_called_once_with(self.page.url)
        self.chrome.close_page.assert_called_once_with(self.page)

    def test_current_page_is_read_without_navigation_or_closing(self):
        result = self.collector.collect_current()
        self.assertEqual(result.title, "标题")
        self.chrome.open_page.assert_not_called()
        self.chrome.close_page.assert_not_called()

    def test_missing_or_multiple_notes_are_not_guessed(self):
        self.chrome.context.pages = []
        with self.assertRaises(InvalidInputError):
            self.collector.collect_current()
        self.chrome.context.pages = [self.page, self.page]
        with self.assertRaises(InvalidInputError):
            self.collector.collect_current()

    def test_unrelated_urls_are_rejected_before_opening(self):
        for value in ("https://example.com/", "https://www.xiaohongshu.com/", "javascript:alert(1)",
                      "https://www.xiaohongshu.com.evil.example/explore/0123456789abcdef01234567",
                      "https://secret:password@www.xiaohongshu.com/explore/0123456789abcdef01234567"):
            with self.subTest(value=value), self.assertRaises(InvalidInputError):
                self.collector.collect(value)
        self.chrome.open_page.assert_not_called()

    def test_timeout_and_browser_error_do_not_expose_details_and_close_owned_page(self):
        for error in (PlaywrightTimeoutError("private-page-details"), PlaywrightError("private-page-details")):
            self.waiting.wait_for.side_effect = error
            with self.subTest(error=type(error).__name__), self.assertRaises(CollectorError) as caught:
                self.collector.collect(self.url)
            self.assertNotIn("private-page-details", str(caught.exception))
        self.assertEqual(self.chrome.close_page.call_count, 2)

    def test_post_can_have_body_without_title_but_not_be_entirely_empty(self):
        self.title.count.return_value = 0
        result = self.collector.collect_current()
        self.assertEqual(result.title, "")
        self.assertEqual(result.body, "正文第一行\n第二行")
        self.body.inner_text.return_value = " "
        with self.assertRaises(CollectorError):
            self.collector.collect_current()

    def test_redirect_to_login_is_not_returned_as_a_post(self):
        self.page.url = "https://www.xiaohongshu.com/login"
        with self.assertRaises(CollectorError):
            self.collector.collect(self.url)
        self.chrome.close_page.assert_called_once_with(self.page)
        self.page.locator.assert_not_called()

    def test_images_metrics_and_author_are_collected_without_counting_missing_values_as_zero(self):
        self.page.evaluate.return_value = {
            "image_urls": ["https://images.example/a.jpg", "https://images.example/a.jpg", "//images.example/b.jpg",
                           "data:image/jpeg;base64,private", "https://secret:password@images.example/private"],
            "metrics_raw": {"likes": "1.2万", "favorites": "0", "comments": "评论"},
            "author_url": "https://www.xiaohongshu.com/user/profile/abcdef0123456789abcdef01",
        }
        result = self.collector.collect_current()
        self.assertEqual(result.image_urls, ("https://images.example/a.jpg", "https://images.example/b.jpg"))
        self.assertEqual(result.metrics, {"likes": 12000, "favorites": 0, "comments": None})
        self.assertEqual(result.metrics_raw["likes"], "1.2万")
        self.assertEqual(result.author_platform_id, "abcdef0123456789abcdef01")

    def test_metric_display_values_are_parsed_with_unknowns_preserved(self):
        for raw, expected in (("1,234", 1234), ("1.2万+", 12000), ("3千", 3000), ("1.5k", 1500),
                              ("2w", 20000), ("1.1亿", 110000000), ("0", 0), ("", None),
                              ("赞", None), (None, None), ("-1", None), ("1.5", None)):
            with self.subTest(raw=raw):
                self.assertEqual(parse_metric(raw), expected)

    def test_zero_counts_use_explicit_same_note_data_when_buttons_display_labels(self):
        note_id = self.url.rsplit('/', 1)[-1]
        self.page.evaluate.return_value = {'metrics_raw': {'likes': '赞', 'favorites': '收藏', 'comments': '评论'},
            'source_note_id': note_id, 'reported_metrics': {'likes': '0', 'favorites': 0, 'comments': '0'}}
        result = self.collector.collect_current()
        self.assertEqual(result.metrics, {'likes': 0, 'favorites': 0, 'comments': 0})
        self.assertEqual(result.metrics_raw, {'likes': '0', 'favorites': '0', 'comments': '0'})
        self.assertEqual(set(result.metrics_precision.values()), {'page_reported'})

    def test_missing_invalid_or_different_note_counts_are_never_invented_as_zero(self):
        for note_id, values in [('f' * 24, {'likes': '0', 'favorites': '0', 'comments': '0'}),
                                (self.url.rsplit('/', 1)[-1], {'likes': None, 'favorites': True, 'comments': '-'})]:
            with self.subTest(note_id=note_id):
                self.page.evaluate.return_value = {'metrics_raw': {'likes': '赞', 'favorites': '收藏', 'comments': '评论'},
                    'source_note_id': note_id, 'reported_metrics': values}
                self.assertEqual(self.collector.collect_current().metrics,
                                 {'likes': None, 'favorites': None, 'comments': None})


if __name__ == "__main__":
    unittest.main()
