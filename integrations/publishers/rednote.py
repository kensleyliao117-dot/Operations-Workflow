"""通过本机 Chrome 创作中心发布图文；页面结构不匹配时停止，不猜测成功。"""
from ipaddress import ip_address
import re
from urllib.parse import urlsplit

from playwright.sync_api import Error as PlaywrightError

import config
from collectors.chrome import ChromeBrowser
from .base import BasePublisher, PublishOutcome, PublisherError


CREATOR_URL = "https://creator.xiaohongshu.com/publish/publish?target=image"
ACCOUNT_SCRIPT = r"""async () => {
    if (location.origin !== 'https://creator.xiaohongshu.com') return [];
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), 10000);
    try {
        const response = await fetch('/api/galaxy/user/info', {
            credentials: 'same-origin', cache: 'no-store', redirect: 'error', signal: controller.signal
        });
        if (!response.ok) return [];
        const payload = await response.json();
        if (payload.success !== true && payload.code !== 0) return [];
        const id = payload.data?.userId;
        return typeof id === 'string' && /^[0-9a-f]{24}$/.test(id) ? [id] : [];
    } catch (_) { return []; }
    finally { clearTimeout(timer); }
}"""
PREVIEW_SELECTOR = ".img-preview-area .pr, .img-list .img-item, .image-list .image-item, .upload-list .upload-item"


def require_local_browser():
    host = urlsplit(config.CHROME_CDP_URL).hostname
    try:
        valid = host == "localhost" or ip_address(host or "").is_loopback
    except ValueError:
        valid = False
    if not valid:
        raise PublisherError("本地发布仅允许连接本机 Chrome CDP")


def read_account(page):
    values = page.evaluate(ACCOUNT_SCRIPT)
    if (not isinstance(values, list) or len(values) != 1 or not isinstance(values[0], str)
            or re.fullmatch(r"[0-9a-f]{24}", values[0]) is None):
        raise PublisherError("无法唯一确认创作中心登录账号，请登录并核对个人主页 ID 后重试")
    return values[0]


def _one(locator, message):
    visible = [locator.nth(i) for i in range(locator.count()) if locator.nth(i).is_visible()]
    if len(visible) != 1:
        raise PublisherError(message)
    return visible[0]


def _publish_button(page):
    buttons = page.get_by_role("button", name="发布", exact=True)
    if not buttons.count():
        buttons = page.locator('xhs-publish-btn:not([is-publish="false"]), .publish-page-publish-btn button.bg-red')
    button = _one(buttons, "无法唯一定位发布按钮")
    if (not button.is_enabled() or button.get_attribute("submit-disabled") == "true"
            or button.get_attribute("aria-disabled") == "true"):
        raise PublisherError("发布按钮当前不可用，请核对编辑器提示")
    return button


def _previews(page):
    return page.locator(PREVIEW_SELECTOR).evaluate_all("""items => items.filter(e => e.getClientRects().length)
        .map(e => Array.from(e.querySelectorAll('img')).map(img => img.currentSrc || img.src))""")


def _editor_text(editor):
    # innerText 会为段落额外插入排版换行；按段落和硬换行读取实际文本。
    value = editor.evaluate("""element => {
        const blocks = Array.from(element.childNodes);
        if (blocks.some(n => n.nodeType !== 1 || n.tagName !== 'P')) return null;
        const text = node => {
            if (node.nodeType === Node.TEXT_NODE) return node.nodeValue;
            if (node.nodeType !== Node.ELEMENT_NODE) return '';
            if (node.tagName === 'BR') return node.classList.contains('ProseMirror-trailingBreak') ? '' : '\\n';
            return Array.from(node.childNodes).map(text).join('');
        };
        return blocks.map(text).join('\\n');
    }""")
    if not isinstance(value, str):
        raise PublisherError("正文编辑器结构无法核对，发布已停止")
    return value.replace("\r\n", "\n").strip()


def _visibility_control(page):
    return _one(page.locator("div.permission-card-wrapper div.d-select-content"), "无法唯一定位可见范围设置，发布已停止")


def _verify_visibility(page, visibility):
    label = {"public": "公开可见", "private": "仅自己可见"}.get(visibility)
    if label is None or _visibility_control(page).inner_text().strip() != label:
        raise PublisherError("页面可见范围与发布记录不一致，发布已停止")


def _set_visibility(page, visibility):
    label = {"public": "公开可见", "private": "仅自己可见"}.get(visibility)
    if label is None:
        raise PublisherError("不支持该可见范围")
    control = _visibility_control(page)
    if control.inner_text().strip() != label:
        control.click()
        option = _one(page.locator("div.d-options-wrapper div.d-grid-item div.custom-option").filter(
            has_text=label), "无法唯一确认目标可见范围选项，发布已停止")
        option.click()
    _verify_visibility(page, visibility)


def is_publish_response(response):
    parsed = urlsplit(response.url)
    return (response.request.method == "POST" and parsed.scheme == "https"
            and parsed.hostname in {"creator.xiaohongshu.com", "edith.xiaohongshu.com"}
            and re.fullmatch(r"/api/media/v\d+/note(?:/publish)?/?", parsed.path) is not None)


def parse_response(response):
    """仅接受发布请求的明确结果，不使用列表中的首篇笔记推测作品。"""
    if not 200 <= response.status < 300:
        return PublishOutcome("unknown", error="发布请求未返回明确成功结果，需人工核实创作中心")
    try:
        payload = response.json()
        if not isinstance(payload, dict):
            raise ValueError("invalid payload")
        code = payload.get("code")
        data = payload.get("data")
        data = data if isinstance(data, dict) else {}
        post_id = data.get("id") or data.get("note_id")
        if ((type(code) is int and code == 0) or (code is None and payload.get("success") is True)):
            if isinstance(post_id, str) and re.fullmatch(r"[0-9a-f]{24}", post_id):
                return PublishOutcome("published", post_id, f"https://www.xiaohongshu.com/explore/{post_id}")
        elif type(code) is int and code != 0 and not post_id:
            return PublishOutcome("failed", error="创作中心明确拒绝发布，请核对页面提示、内容及账号状态")
    except (ValueError, TypeError):
        pass
    return PublishOutcome("unknown", error="已提交发布但未获取可信作品 ID，需人工核实后补录结果")


class RednotePublisher(BasePublisher):
    def publish(self, payload, *, before_submit):
        require_local_browser()
        submitted = False
        try:
            with ChromeBrowser() as browser:
                page = browser.open_page(CREATOR_URL)
                # 发布页包含上传及编辑现场，超时或结束后仍保留页面供核实。
                browser.preserve_page(page)
                # 后台窗口会暂停动画帧，焦点模拟保证控件稳定性检查正常执行。
                focus = browser.context.new_cdp_session(page)
                focus.send("Emulation.setFocusEmulationEnabled", {"enabled": True})
                if urlsplit(page.url).hostname != "creator.xiaohongshu.com":
                    raise PublisherError("创作中心页面未就绪，请先完成登录")
                if read_account(page) != payload.platform_user_id:
                    raise PublisherError("创作中心登录账号与所选发布账号不一致，发布已停止")
                image_tab = page.get_by_text("上传图文", exact=True)
                if image_tab.count():
                    _one(image_tab, "无法定位图文发布入口").click()
                uploads = page.locator('input[type="file"][accept*="image"], input[type="file"][accept*=".png"]')
                # 单页应用在 DOMContentLoaded 后仍会异步挂载上传控件。
                uploads.first.wait_for(state="attached", timeout=config.CHROME_PAGE_TIMEOUT_MS)
                if uploads.count() != 1:
                    raise PublisherError("无法唯一定位图片上传控件，请核对创作中心页面结构")
                if len(payload.images) > 1 and uploads.get_attribute("multiple") is None:
                    raise PublisherError("当前上传控件不支持批量选图，请完成页面适配后重试")
                uploads.set_input_files([str(path) for path in payload.images])
                # 仅接受已完成的图片预览，不以选择文件成功代替上传成功。
                page.wait_for_function("""({selector, count}) => {
                    const items = Array.from(document.querySelectorAll(selector)).filter(e => e.getClientRects().length);
                    const busy = Array.from(document.querySelectorAll('.uploading, .upload-progress, [aria-busy="true"]'))
                        .some(e => e.getClientRects().length);
                    return items.length === count && !busy;
                }""", arg={"selector": PREVIEW_SELECTOR, "count": len(payload.images)}, timeout=config.CHROME_PAGE_TIMEOUT_MS)
                preview_sources = _previews(page)
                titles = page.get_by_placeholder(re.compile("填写标题|输入标题|标题"))
                bodies = page.locator('[contenteditable="true"]')
                titles.first.wait_for(state="visible", timeout=config.CHROME_PAGE_TIMEOUT_MS)
                bodies.first.wait_for(state="visible", timeout=config.CHROME_PAGE_TIMEOUT_MS)
                guide = page.locator("button.feature-guide__btn")
                if guide.count() and guide.first.is_visible():
                    _one(guide, "无法唯一确认图片编辑功能引导").click()
                title = _one(titles, "无法唯一定位标题输入框")
                body = _one(bodies, "无法唯一定位正文输入框")
                title.fill(payload.title)
                body.fill(payload.editor_body)
                body.press("Escape")
                _set_visibility(page, payload.visibility)
                if title.input_value() != payload.title or _editor_text(body) != payload.editor_body.strip():
                    raise PublisherError("编辑器内容与已审核版本不一致，可能超出页面限制，发布已停止")
                button = _publish_button(page)
                if read_account(page) != payload.platform_user_id:
                    raise PublisherError("发布按钮不可用或登录账号发生变化，发布已停止")
                before_submit()
                _verify_visibility(page, payload.visibility)
                if (read_account(page) != payload.platform_user_id or title.input_value() != payload.title
                        or _editor_text(body) != payload.editor_body.strip()
                        or _previews(page) != preview_sources):
                    return PublishOutcome("failed", error="点击发布前账号或编辑器内容发生变化，本次未点击发布")
                submitted = True
                with page.expect_response(is_publish_response, timeout=config.CHROME_PAGE_TIMEOUT_MS) as result:
                    button.click(no_wait_after=True)
                return parse_response(result.value)
        except PlaywrightError:
            if submitted:
                return PublishOutcome("unknown", error="提交发布后浏览器超时或连接中断，请核实创作中心，禁止直接重发")
            raise PublisherError("浏览器发布准备失败，请检查登录态、上传进度和页面结构") from None
        except Exception:
            if submitted:
                return PublishOutcome("unknown", error="提交后的发布结果暂时无法确认，请先核实创作中心")
            raise
