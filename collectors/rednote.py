"""读取单篇小红书帖子的标题和正文，验证页面采集链路。

运行 python -m collectors.rednote 读取已打开的帖子，也可在命令后传完整网页链接。
采集模块仅读取帖子内容，案例保存和分析由业务层分别处理。
"""
import argparse
import json
import re
import sys
from decimal import Decimal, InvalidOperation
from urllib.parse import parse_qs, urlsplit

from playwright.sync_api import Error as PlaywrightError, Page, TimeoutError as PlaywrightTimeoutError

from config import ConfigurationError
from services.exceptions import InvalidInputError
from .base import BaseCollector, CollectedCase, CollectorError
from .chrome import ChromeBrowser


DETAIL_METADATA_SCRIPT = """(expectedId) => {
    const anchor = Array.from(document.querySelectorAll('#detail-desc, #detail-title'))
        .find(node => node.getClientRects().length && getComputedStyle(node).visibility !== 'hidden');
    const root = anchor?.closest('#noteContainer, .note-container, .note-detail-mask');
    if (!root) return {image_urls: [], metrics_raw: {}, author_url: null};
    const images = Array.from(root.querySelectorAll('.media-container img, .swiper img, img.note-slider-img'))
        .map(img => img.currentSrc || img.getAttribute('src') || img.getAttribute('data-src'))
        .filter(Boolean);
    const raw = {};
    // 评论也使用 interactions / like-wrapper，必须先限定帖子自身的互动栏。
    const commentSelector = '.comment-item, .parent-comment, .comment-container, .comments-container, .comments-el';
    const bars = Array.from(root.querySelectorAll('.engage-bar, .interact-container, .interactions'))
        .filter(node => node.getClientRects().length && getComputedStyle(node).visibility !== 'hidden'
            && !node.closest(commentSelector));
    const bar = bars.find(node => node.querySelector('.like-wrapper')
        && (node.querySelector('.collect-wrapper') || node.querySelector('.chat-wrapper')));
    for (const [key, wrapper] of Object.entries({likes: 'like-wrapper', favorites: 'collect-wrapper', comments: 'chat-wrapper'})) {
        const node = bar ? Array.from(bar.querySelectorAll(`.${wrapper} .count`))
            .find(value => value.getClientRects().length && getComputedStyle(value).visibility !== 'hidden'
                && !value.closest(commentSelector)) : null;
        raw[key] = node ? node.innerText.trim() : null;
    }
    const note = window.__INITIAL_STATE__?.note?.noteDetailMap?.[expectedId]?.note;
    const matches = typeof note?.noteId === 'string' && note.noteId.toLowerCase() === expectedId;
    const info = matches ? note.interactInfo : null;
    return {image_urls: images, metrics_raw: raw,
        source_note_id: matches ? expectedId : null,
        reported_metrics: info ? {likes: info.likedCount, favorites: info.collectedCount, comments: info.commentCount} : {},
        author_url: root.querySelector('.author a[href*="/user/profile/"], .author-container a[href*="/user/profile/"]')?.href || null};
}"""


def parse_metric(value: str | None) -> int | None:
    """解析公开显示值；缩写数值仅代表显示精度，原始文本另行保存。"""
    if not isinstance(value, str):
        return None
    match = re.fullmatch(r"([0-9]+(?:\.[0-9]+)?)(万|千|亿|[kKwWmM])?\+?", value.strip().replace(",", ""))
    if not match:
        return None
    units = {None: 1, "千": 1000, "万": 10000, "亿": 100000000, "k": 1000, "w": 10000, "m": 1000000}
    try:
        number = Decimal(match.group(1)) * units[(match.group(2) or "").lower() or None]
        return int(number) if number == number.to_integral_value() and number <= 10**15 else None
    except (InvalidOperation, ValueError):
        return None


def _metadata(page: Page):
    expected_url = _note_url(page.url)
    expected_id = expected_url.rsplit('/', 1)[-1] if expected_url else None
    raw = page.evaluate(DETAIL_METADATA_SCRIPT, expected_id)
    images = []
    for value in raw.get("image_urls", []):
        if not isinstance(value, str):
            continue
        try:
            parsed = urlsplit("https:" + value if value.startswith("//") else value)
            if (parsed.scheme not in {"http", "https"} or not parsed.hostname
                    or parsed.username is not None or parsed.password is not None):
                continue
            parsed.port
        except ValueError:
            continue
        image = parsed.geturl()
        if image not in images:
            images.append(image)
    metrics_raw = {key: raw.get("metrics_raw", {}).get(key) for key in ("likes", "favorites", "comments")}
    metrics = {key: parse_metric(value) for key, value in metrics_raw.items()}
    precision = {}
    if expected_id and raw.get("source_note_id") == expected_id:
        reported = raw.get("reported_metrics") or {}
        for key in metrics:
            # 零互动可能显示为按钮文字；仅用同一笔记明确返回的计数补充。
            value = reported.get(key)
            if metrics[key] is None and (type(value) is int or isinstance(value, str)):
                parsed = parse_metric(str(value))
                if parsed is not None:
                    metrics[key] = parsed
                    metrics_raw[key] = str(value)
                    precision[key] = "page_reported"
    author = urlsplit(raw.get("author_url") or "")
    author_id = None
    if author.hostname in {"www.xiaohongshu.com", "xiaohongshu.com"}:
        match = re.fullmatch(r"/user/profile/([0-9a-fA-F]{24})/?", author.path)
        author_id = match.group(1) if match else None
    return tuple(images[:100]), metrics, metrics_raw, author_id, precision


def _note_url(value: str) -> str | None:
    """识别小红书详情页，返回去掉分享参数的帖子链接。"""
    if not isinstance(value, str) or any(c.isspace() for c in value):
        return None
    try:
        parsed = urlsplit(value)
        if (parsed.scheme != "https" or parsed.hostname not in {"www.xiaohongshu.com", "xiaohongshu.com"}
                or parsed.username is not None or parsed.password is not None or parsed.port not in {None, 443}):
            return None
        match = re.fullmatch(r"/(?:explore|discovery/item)/([0-9a-fA-F]{24})/?", parsed.path)
        note_id = match.group(1) if match else parse_qs(parsed.query).get("modal_id", [""])[0]
        if re.fullmatch(r"[0-9a-fA-F]{24}", note_id):
            return f"https://www.xiaohongshu.com/explore/{note_id}"
    except ValueError:
        pass
    return None


class RednoteCollector(BaseCollector):
    def __init__(self, chrome: ChromeBrowser):
        self.chrome = chrome

    def collect(self, source_url: str) -> CollectedCase:
        expected_url = _note_url(source_url)
        if expected_url is None:
            raise InvalidInputError("请提供小红书帖子完整网页链接，例如 https://www.xiaohongshu.com/explore/帖子ID")
        # 打开页面时保留原链接参数，返回结果时再去掉参数。
        page = self.chrome.open_page(source_url)
        try:
            result = self._read_page(page)
            if result.source_url != expected_url:
                raise CollectorError("页面跳转到其他帖子，本次采集未保存")
            return result
        finally:
            self.chrome.close_page(page)

    def collect_current(self) -> CollectedCase:
        """读取已打开的详情页，保留原有标签页和登录状态。"""
        pages = [page for page in self.chrome.context.pages if _note_url(page.url) is not None]
        if not pages:
            raise InvalidInputError("请先在远程调试 Chrome 中打开一篇小红书帖子详情，或在命令后传入完整网页链接")
        if len(pages) > 1:
            raise InvalidInputError("当前打开了多篇小红书帖子，请在命令后指定要读取的帖子链接")
        return self._read_page(pages[0])

    def _read_page(self, page: Page) -> CollectedCase:
        source_url = _note_url(page.url)
        if source_url is None:
            raise CollectorError("页面未停留在小红书帖子详情，请检查登录状态、帖子链接及页面提示")
        try:
            # 仅匹配可见的帖子内容，避免将推荐卡片或隐藏详情误认为当前帖子。
            page.locator("#detail-title:visible, #detail-desc:visible").first.wait_for(
                state="visible", timeout=self.chrome.page_timeout_ms,
            )
            title_node = page.locator("#detail-title:visible").first
            body_node = page.locator("#detail-desc:visible").first
            title = title_node.inner_text(timeout=self.chrome.page_timeout_ms).strip() if title_node.count() else ""
            body = body_node.inner_text(timeout=self.chrome.page_timeout_ms).strip() if body_node.count() else ""
            if not title and not body:
                raise CollectorError("帖子没有可读取的标题或正文")
            images, metrics, metrics_raw, author_id, precision = _metadata(page)
            if _note_url(page.url) != source_url:
                raise CollectorError("读取期间帖子页面发生变化，请保持目标帖子打开后重试")
            return CollectedCase(platform="rednote", source_url=source_url, title=title, body=body,
                                 image_urls=images, metrics=metrics, metrics_raw=metrics_raw,
                                 author_platform_id=author_id, metrics_precision=precision)
        except PlaywrightTimeoutError:
            raise CollectorError("未能及时读取帖子内容，请检查登录、验证提示及帖子可用性；页面结构变化也可能需要调整选择器") from None
        except PlaywrightError:
            raise CollectorError("读取小红书页面失败，请检查标签页和浏览器连接") from None


def main() -> int:
    parser = argparse.ArgumentParser(description="读取一篇小红书帖子的标题和正文，不写入数据库")
    parser.add_argument("url", nargs="?", help="完整帖子网页链接；不传则读取调试 Chrome 中已打开的一篇帖子")
    args = parser.parse_args()
    try:
        with ChromeBrowser() as chrome:
            collector = RednoteCollector(chrome)
            result = collector.collect(args.url) if args.url else collector.collect_current()
        print(json.dumps({"source_url": result.source_url, "title": result.title, "body": result.body,
                          "image_urls": result.image_urls, "metrics": result.metrics,
                          "metrics_raw": result.metrics_raw, "author_platform_id": result.author_platform_id},
                         ensure_ascii=False, indent=2))
        return 0
    except (CollectorError, InvalidInputError, ConfigurationError) as exc:
        print(f"读取失败：{exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
