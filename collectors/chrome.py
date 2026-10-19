"""通过 Playwright / CDP 连接已启动的 Chrome，复用手工登录态。

每次采集使用独立实例，在同一线程中创建并关闭。
仅清理未显式保留的新建标签页，保留原有浏览器窗口和登录状态。
浏览器启动、用户目录和业务数据保存分别在外部处理。
"""
import logging
from threading import get_ident
from urllib.parse import urlsplit

from playwright.sync_api import (
    Browser, BrowserContext, Error as PlaywrightError, Page, Playwright,
    TimeoutError as PlaywrightTimeoutError, sync_playwright,
)

import config
from services.exceptions import InvalidInputError
from .base import ChromeConnectionError, ChromeTimeoutError

logger = logging.getLogger(__name__)


class ChromeBrowser:
    def __init__(self, *, endpoint_url: str | None = None,
                 connect_timeout_ms: int | None = None, page_timeout_ms: int | None = None):
        self.endpoint_url = config.chrome_cdp_url(config.CHROME_CDP_URL if endpoint_url is None else endpoint_url)
        self.connect_timeout_ms = config.CHROME_CDP_TIMEOUT_MS if connect_timeout_ms is None else connect_timeout_ms
        self.page_timeout_ms = config.CHROME_PAGE_TIMEOUT_MS if page_timeout_ms is None else page_timeout_ms
        for value in (self.connect_timeout_ms, self.page_timeout_ms):
            if type(value) is not int or value <= 0:
                raise config.ConfigurationError("Chrome 超时必须是正整数，单位为毫秒")
        self._playwright: Playwright | None = None
        self._browser: Browser | None = None
        self._context: BrowserContext | None = None
        self._pages: list[Page] = []
        self._thread_id: int | None = None

    def _check_thread(self):
        if self._thread_id is not None and self._thread_id != get_ident():
            raise ChromeConnectionError("ChromeBrowser 必须在建立连接的同一线程内使用和关闭")

    @property
    def is_connected(self) -> bool:
        self._check_thread()
        return self._browser is not None and self._browser.is_connected()

    @property
    def browser(self) -> Browser:
        if not self.is_connected:
            raise ChromeConnectionError("Chrome 尚未连接或已断开，请先调用 connect()")
        return self._browser

    @property
    def context(self) -> BrowserContext:
        self.browser  # 检查连接和线程，再复用已有登录态的上下文。
        if self._context is None:
            raise ChromeConnectionError("Chrome 没有可用的浏览器上下文")
        return self._context

    def connect(self) -> "ChromeBrowser":
        if self.is_connected:
            return self
        self.disconnect()
        self._thread_id = get_ident()
        try:
            self._playwright = sync_playwright().start()
            self._browser = self._playwright.chromium.connect_over_cdp(
                self.endpoint_url, timeout=self.connect_timeout_ms, no_defaults=True,
            )
            contexts = self._browser.contexts
            if not contexts:
                raise ChromeConnectionError("Chrome 没有可用的默认上下文，请检查浏览器是否正常启动")
            self._context = contexts[0]
            return self
        except PlaywrightTimeoutError:
            self.disconnect()
            raise ChromeTimeoutError("连接 Chrome 超时，请确认已开启远程调试端口") from None
        except PlaywrightError:
            self.disconnect()
            raise ChromeConnectionError("连接 Chrome 失败，请确认 Chrome 已开启 CDP，且 CHROME_CDP_URL 配置正确") from None
        except Exception:
            self.disconnect()
            raise

    def new_page(self) -> Page:
        """新建标签页并记入清理列表，保留浏览器原有页面。"""
        page = None
        try:
            page = self.context.new_page()
            self._pages.append(page)
            page.set_default_timeout(self.page_timeout_ms)
            page.set_default_navigation_timeout(self.page_timeout_ms)
            return page
        except PlaywrightError:
            if page is not None:
                self.close_page(page)
            raise ChromeConnectionError("无法创建 Chrome 标签页，请检查浏览器连接") from None

    def open_page(self, url: str) -> Page:
        """在新标签页中打开目标地址，加载失败时清理本次创建的页面。"""
        try:
            parsed = urlsplit(url) if isinstance(url, str) else None
            valid = (parsed is not None and parsed.scheme in {"http", "https"} and bool(parsed.hostname)
                     and parsed.username is None and parsed.password is None
                     and not any(c.isspace() for c in url))
            if parsed is not None:
                parsed.port
        except ValueError:
            valid = False
        if not valid:
            raise InvalidInputError("页面地址必须是有效的 HTTP/HTTPS URL，不能包含登录凭据")
        page = self.new_page()
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=self.page_timeout_ms)
            return page
        except PlaywrightTimeoutError:
            self.close_page(page)
            raise ChromeTimeoutError("Chrome 页面加载超时，请检查网络或页面状态") from None
        except PlaywrightError:
            self.close_page(page)
            raise ChromeConnectionError("Chrome 页面加载失败，请检查网络和浏览器连接") from None

    def close_page(self, page: Page) -> None:
        self._check_thread()
        if page not in self._pages:
            raise InvalidInputError("只能通过此接口关闭当前 ChromeBrowser 创建的标签页")
        self._pages.remove(page)
        try:
            if not page.is_closed():
                page.close(run_before_unload=False)
        except PlaywrightError as exc:
            logger.debug("Cannot close collector page: %s", type(exc).__name__)

    def preserve_page(self, page: Page) -> None:
        """将本次新建页面交由浏览器保留，断开连接时不关闭编辑现场。"""
        self._check_thread()
        if page not in self._pages:
            raise InvalidInputError("只能保留当前 ChromeBrowser 创建的标签页")
        self._pages.remove(page)

    def disconnect(self) -> None:
        """清理本次创建的标签页并断开连接，保留原有 Chrome 窗口。"""
        self._check_thread()
        for page in list(self._pages):
            self.close_page(page)
        runtime = self._playwright
        self._playwright = None
        self._browser = None
        self._context = None
        self._thread_id = None
        if runtime is not None:
            try:
                runtime.stop()
            except PlaywrightError as exc:
                logger.debug("Cannot stop collector connection: %s", type(exc).__name__)

    def __enter__(self) -> "ChromeBrowser":
        return self.connect()

    def __exit__(self, *_):
        self.disconnect()
