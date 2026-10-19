"""创作中心采集的作品定位、真实零值和实际账号校验。"""
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from collectors.base import CollectorError
from collectors.rednote_creator import POSTED_PATH, collect_creator_post, parse_posted_response


NOTE_ID, USER_ID = "1" * 24, "2" * 24


def payload(**changes):
    note = {"id": NOTE_ID, "display_title": "通勤示例", "permission_msg": "仅自己可见",
            "likes": 0, "comments_count": 0, "collected_count": 0, "view_count": 7,
            "time": "2026-10-18 18:52", "xsec_token": "private-token"}
    note.update(changes)
    return {"code": 0, "success": True, "data": {"notes": [note]}}


class CreatorMetricsTest(unittest.TestCase):
    def test_real_zero_values_remain_zero_and_unavailable_metrics_are_omitted(self):
        result = parse_posted_response(payload(), NOTE_ID)
        self.assertEqual(result["metrics"], {"likes": 0, "favorites": 0, "comments": 0, "views": 7})
        self.assertNotIn("impressions", result["metrics"])
        self.assertNotIn("xsec_token", result)
        result = parse_posted_response(payload(comments_count=None, collected_count=True), NOTE_ID)
        self.assertEqual(result["metrics"], {"likes": 0, "views": 7})

    def test_invalid_values_do_not_become_zero(self):
        for value in (-1, True, "0", 1.2, None):
            with self.subTest(value=value), self.assertRaises(CollectorError):
                parse_posted_response(payload(likes=value, comments_count=None, collected_count=None, view_count=None), NOTE_ID)

    def test_wrong_or_ambiguous_notes_and_error_responses_are_rejected(self):
        self.assertIsNone(parse_posted_response(payload(id="3" * 24), NOTE_ID))
        duplicate = payload()
        duplicate["data"]["notes"] *= 2
        for value in (duplicate, {"code": 1}, {"code": 0, "success": False}, {"code": 0, "data": []}):
            with self.subTest(value=value), self.assertRaises(CollectorError):
                parse_posted_response(value, NOTE_ID)

    def browser(self, chrome, *, account=USER_ID, data=None):
        browser = chrome.return_value.__enter__.return_value
        page = browser.new_page.return_value
        page.evaluate.return_value = [account]
        page.locator.return_value.evaluate_all.return_value = [{"title": "通勤示例", "permission": "仅自己可见"}]
        response = SimpleNamespace(url="https://creator.xiaohongshu.com" + POSTED_PATH,
            request=SimpleNamespace(method="GET"), status=200, json=lambda: payload() if data is None else data)
        page.expect_response.return_value.__enter__.return_value = SimpleNamespace(value=response)
        return page

    def test_collection_verifies_actual_account_and_target_before_returning(self):
        with patch("collectors.rednote_creator.ChromeBrowser") as chrome:
            page = self.browser(chrome)
            result = collect_creator_post(platform_post_id=NOTE_ID, platform_user_id=USER_ID)
            self.assertEqual(result.post_id, NOTE_ID)
            self.assertEqual(result.platform_user_id, USER_ID)
            self.assertEqual(result.visibility, "private")
            self.assertIsNotNone(result.observed_at.tzinfo)
            self.assertEqual(page.evaluate.call_count, 2)

    def test_account_change_or_wrong_post_never_returns_observation(self):
        for wrong in ("account", "post", "changed"):
            with self.subTest(wrong=wrong), patch("collectors.rednote_creator.ChromeBrowser") as chrome:
                page = self.browser(chrome, account="4" * 24 if wrong == "account" else USER_ID,
                    data=payload(id="3" * 24) if wrong == "post" else None)
                if wrong == "changed":
                    page.evaluate.side_effect = [[USER_ID], ["4" * 24]]
                with self.assertRaises(CollectorError):
                    collect_creator_post(platform_post_id=NOTE_ID, platform_user_id=USER_ID)

    def test_visibility_label_change_does_not_reject_same_note_metrics(self):
        with patch("collectors.rednote_creator.ChromeBrowser") as chrome:
            page = self.browser(chrome)
            page.locator.return_value.evaluate_all.return_value = [{"title": "通勤示例", "permission": "公开可见"}]
            result = collect_creator_post(platform_post_id=NOTE_ID, platform_user_id=USER_ID)
            self.assertEqual(result.post_id, NOTE_ID)
            self.assertEqual(result.metrics['likes'], 0)


if __name__ == "__main__":
    unittest.main()
