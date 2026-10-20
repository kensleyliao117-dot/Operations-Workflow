"""真实浏览器验证指标选择器；使用内存 HTML，不请求小红书。"""
import os
import unittest

from playwright.sync_api import sync_playwright

from collectors.rednote import _metadata


@unittest.skipUnless(os.getenv('REDNOTE_DOM_TEST') == '1', 'Set REDNOTE_DOM_TEST=1 to use installed Chrome')
class RednoteMetricsDOMTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.runtime = sync_playwright().start()
        cls.browser = cls.runtime.chromium.launch(channel='chrome', headless=True)

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.runtime.stop()

    def read(self, bar, *, state=''):
        note_id = 'a' * 24
        html = '''<div id="noteContainer"><h1 id="detail-title">Post</h1>
            <div class="comments-container"><div class="comment-item"><div class="interactions">
            <span class="like-wrapper"><span class="count">3</span></span>
            </div></div></div>''' + bar + '</div>' + state
        page = self.browser.new_page()
        try:
            page.route('**/*', lambda route: route.fulfill(content_type='text/html', body=html))
            page.goto('https://www.xiaohongshu.com/explore/' + note_id)
            return _metadata(page)
        finally:
            page.close()

    @staticmethod
    def bar(likes='133', favorites='73', comments='97', style=''):
        return f'''<div class="interactions engage-bar" style="{style}"><div class="interact-container">
            <span class="like-wrapper"><span class="count">{likes}</span></span>
            <span class="collect-wrapper"><span class="count">{favorites}</span></span>
            <span class="chat-wrapper"><span class="count">{comments}</span></span>
            </div></div>'''

    def test_comment_likes_are_not_post_likes(self):
        _, metrics, raw, _, _ = self.read(self.bar())
        self.assertEqual(metrics, {'likes': 133, 'favorites': 73, 'comments': 97})
        self.assertEqual(raw['likes'], '133')

    def test_comment_only_page_does_not_invent_post_counts(self):
        _, metrics, _, _, _ = self.read('')
        self.assertEqual(metrics, {'likes': None, 'favorites': None, 'comments': None})

    def test_visible_post_bar_wins_over_hidden_bar_and_stale_initial_state(self):
        state = """<script>window.__INITIAL_STATE__={note:{noteDetailMap:{
            ['a'.repeat(24)]:{note:{noteId:'a'.repeat(24),interactInfo:{likedCount:'132'}}}
        }}};</script>"""
        _, metrics, _, _, _ = self.read(self.bar('999', style='display:none') + self.bar(), state=state)
        self.assertEqual(metrics['likes'], 133)

    def test_matching_page_state_can_supply_real_zeros_for_button_labels(self):
        state = """<script>window.__INITIAL_STATE__={note:{noteDetailMap:{
            ['a'.repeat(24)]:{note:{noteId:'a'.repeat(24),interactInfo:{likedCount:'0',collectedCount:0,commentCount:'0'}}}
        }}};</script>"""
        _, metrics, _, _, precision = self.read(self.bar('Like', 'Save', 'Comment'), state=state)
        self.assertEqual(metrics, {'likes': 0, 'favorites': 0, 'comments': 0})
        self.assertEqual(set(precision.values()), {'page_reported'})
