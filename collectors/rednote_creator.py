"""读取所属账号创作中心的作品和互动指标，不修改或发布内容。"""
from dataclasses import dataclass
from datetime import datetime, timezone
import re
from urllib.parse import urlsplit

from playwright.sync_api import Error as PlaywrightError

from .base import CollectorError
from .chrome import ChromeBrowser
from integrations.publishers.rednote import read_account, require_local_browser


MANAGER_URL = "https://creator.xiaohongshu.com/new/note-manager"
POSTED_PATH = "/api/galaxy/v2/creator/note/user/posted"
CARD_SCRIPT = """(id) => Array.from(document.querySelectorAll('.note-card')).some(e => {
    try { return JSON.parse(e.getAttribute('data-impression')).noteTarget.value.noteId === id; }
    catch (_) { return false; }
})"""


@dataclass(frozen=True, slots=True)
class CreatorObservation:
    post_id: str
    platform_user_id: str
    title: str
    visibility: str
    published_time: str | None
    metrics: dict[str, int]
    observed_at: datetime


def parse_posted_response(payload, post_id):
    """仅提取指定作品的必要字段，不保留账号、签名或分享令牌。"""
    if (not isinstance(payload, dict) or type(payload.get("code")) is not int or payload["code"] != 0
            or payload.get("success") is False):
        raise CollectorError("创作中心作品列表未返回有效结果")
    data = payload.get("data")
    notes = data.get("notes") if isinstance(data, dict) else None
    if not isinstance(notes, list):
        raise CollectorError("创作中心作品列表结构无法识别")
    matches = [n for n in notes if isinstance(n, dict) and n.get("id") == post_id]
    if not matches:
        return None
    if len(matches) != 1:
        raise CollectorError("创作中心返回重复作品，指标未保存")
    note = matches[0]
    title = note.get("display_title")
    if not isinstance(title, str) or not title.strip():
        raise CollectorError("无法确认作品标题")
    visibility = {"仅自己可见": "private", "公开可见": "public"}.get(note.get("permission_msg"), "unknown")
    # view_count 是浏览量，不映射为曝光；未提供的点击、转化也不补零。
    fields = {"likes": "likes", "favorites": "collected_count", "comments": "comments_count", "views": "view_count"}
    metrics = {name: note[key] for name, key in fields.items()
               if type(note.get(key)) is int and 0 <= note[key] <= 10**15}
    if not metrics:
        raise CollectorError("未读取到真实互动指标，缺失指标不会补零")
    published_time = note.get("time")
    if not isinstance(published_time, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}", published_time):
        published_time = None
    return {"post_id": post_id, "title": title.strip(), "visibility": visibility,
            "published_time": published_time, "metrics": metrics}


def is_posted_response(response):
    parsed = urlsplit(response.url)
    return (parsed.scheme == "https" and parsed.hostname == "creator.xiaohongshu.com"
            and parsed.path == POSTED_PATH and response.request.method == "GET")


def collect_creator_post(*, platform_post_id, platform_user_id):
    for value in (platform_post_id, platform_user_id):
        if not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{24}", value) is None:
            raise CollectorError("需要有效的作品 ID 和发布账号 ID")
    require_local_browser()
    try:
        with ChromeBrowser() as chrome:
            page = chrome.new_page()
            focus = chrome.context.new_cdp_session(page)
            focus.send("Emulation.setFocusEmulationEnabled", {"enabled": True})
            # 显式等待响应并读取完整正文，避免异步事件回调尚未解析完就判断数据缺失。
            with page.expect_response(is_posted_response, timeout=chrome.page_timeout_ms) as pending:
                page.goto(MANAGER_URL, wait_until="domcontentloaded", timeout=chrome.page_timeout_ms)
            if read_account(page) != platform_user_id:
                raise CollectorError("创作中心实际账号与发布记录不一致，指标未保存")
            response = pending.value
            if response.status != 200:
                raise CollectorError("创作中心作品列表请求失败")
            try:
                value = parse_posted_response(response.json(), platform_post_id)
            except (ValueError, TypeError):
                raise CollectorError("创作中心作品列表响应无法解析") from None
            if value is None:
                raise CollectorError("当前创作中心列表未找到目标作品，指标未保存")
            page.wait_for_function(CARD_SCRIPT, arg=platform_post_id, timeout=chrome.page_timeout_ms)
            rows = page.locator(".note-card").evaluate_all("""(items, id) => items.filter(e => {
                try { return JSON.parse(e.getAttribute('data-impression')).noteTarget.value.noteId === id; }
                catch (_) { return false; }
            }).map(e => ({title:e.querySelector('.note-card__title')?.innerText.trim(),
                          permission:e.querySelector('.permission_msg')?.innerText.trim()}))""", platform_post_id)
            if (len(rows) != 1 or rows[0]["title"] != value["title"]
                    or read_account(page) != platform_user_id):
                raise CollectorError("页面作品或实际账号已变化，指标未保存")
            return CreatorObservation(**value, platform_user_id=platform_user_id, observed_at=datetime.now(timezone.utc))
    except PlaywrightError:
        raise CollectorError("创作中心读取失败，请检查登录态、目标作品及页面加载状态") from None
