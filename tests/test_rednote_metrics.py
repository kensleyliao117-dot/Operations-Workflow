"""真实零值、作品关联和可见范围变化的采集回归测试。"""
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import patch
import unittest

from collectors.base import CollectedCase
from collectors.rednote_metrics import collect_metrics, collect_creator_metrics
from services.exceptions import InvalidInputError

NOTE_ID, USER_ID = '1' * 24, '2' * 24
URL = f'https://www.xiaohongshu.com/explore/{NOTE_ID}'


class RednoteMetricsTest(unittest.TestCase):
    def collect(self, metrics, precision=None):
        case = CollectedCase('rednote', URL, 'Title', 'Body', metrics=metrics,
            metrics_raw={key: str(value) if value is not None else None for key, value in metrics.items()},
            author_platform_id=USER_ID, metrics_precision=precision or {})
        with patch('collectors.rednote_metrics.ChromeBrowser'), \
             patch('collectors.rednote_metrics.require_local_browser'), \
             patch('collectors.rednote_metrics.read_account', return_value=USER_ID), \
             patch('collectors.rednote_metrics.RednoteCollector') as collector:
            collector.return_value.collect.return_value = case
            return collect_metrics(platform_post_id=NOTE_ID, source_url=URL, platform_user_id=USER_ID)

    def test_all_zero_values_are_returned_and_missing_fields_remain_missing(self):
        result = self.collect({'likes': 0, 'favorites': 0, 'comments': 0}, {'comments': 'page_reported'})
        self.assertEqual(result.metrics, {'likes': 0, 'favorites': 0, 'comments': 0})
        self.assertEqual(result.precision['comments'], 'page_reported')
        self.assertEqual(self.collect({'likes': 0, 'comments': None}).metrics, {'likes': 0})
        with self.assertRaises(InvalidInputError):
            self.collect({'likes': None, 'favorites': None, 'comments': None})

    def test_creator_collection_accepts_current_visibility_without_comparing_publication_visibility(self):
        for visibility in ('public', 'private', 'unknown'):
            with self.subTest(visibility=visibility):
                post = SimpleNamespace(post_id=NOTE_ID, platform_user_id=USER_ID, visibility=visibility,
                    metrics={'likes': 0, 'comments': 0}, observed_at=datetime.now(timezone.utc))
                with patch('collectors.rednote_creator.collect_creator_post', return_value=post):
                    result = collect_creator_metrics(platform_post_id=NOTE_ID, platform_user_id=USER_ID)
                self.assertEqual(result.metrics, {'likes': 0, 'comments': 0})

    def test_wrong_post_url_is_rejected_before_browser_connection(self):
        with patch('collectors.rednote_metrics.ChromeBrowser') as browser:
            with self.assertRaises(InvalidInputError):
                collect_metrics(platform_post_id=NOTE_ID, source_url=URL.replace(NOTE_ID, '3' * 24),
                                platform_user_id=USER_ID)
            browser.assert_not_called()
