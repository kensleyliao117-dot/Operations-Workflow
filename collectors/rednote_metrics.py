"""采集已发布作品的公开互动指标，核对实际账号及作品作者。"""
from dataclasses import dataclass
from datetime import datetime, timezone
import re

from collectors.chrome import ChromeBrowser
from collectors.rednote import RednoteCollector, _note_url
from integrations.publishers.rednote import CREATOR_URL, read_account, require_local_browser
from services.exceptions import ConflictError, InvalidInputError


@dataclass(frozen=True, slots=True)
class CollectedMetrics:
    post_id: str
    platform_user_id: str
    metrics: dict[str, int]
    metrics_raw: dict
    precision: dict
    observed_at: datetime
    source: str = "rednote_public"


def collect_creator_metrics(*, platform_post_id: str, platform_user_id: str) -> CollectedMetrics:
    from .rednote_creator import collect_creator_post
    post = collect_creator_post(platform_post_id=platform_post_id, platform_user_id=platform_user_id)
    return CollectedMetrics(post.post_id, post.platform_user_id, post.metrics,
        {key: str(value) for key, value in post.metrics.items()},
        {key: "creator_reported" for key in post.metrics}, post.observed_at, "rednote_creator")


def collect_metrics(*, platform_post_id: str, source_url: str, platform_user_id: str) -> CollectedMetrics:
    expected = _note_url(source_url)
    if expected is None or expected.rsplit("/", 1)[-1] != platform_post_id:
        raise InvalidInputError("采集链接必须指向发布记录中的小红书作品")
    require_local_browser()
    with ChromeBrowser() as chrome:
        account_page = chrome.open_page(CREATOR_URL)
        if read_account(account_page) != platform_user_id:
            raise ConflictError("浏览器实际登录账号与发布作品绑定账号不一致")
        case = RednoteCollector(chrome).collect(source_url)
        if case.author_platform_id is None or case.author_platform_id != platform_user_id:
            raise ConflictError("无法确认作品作者，或作品作者与实际发布账号不一致，指标未保存")
        if read_account(account_page) != platform_user_id:
            raise ConflictError("采集期间登录账号发生变化，指标未保存")
    values = {name: value for name, value in case.metrics.items() if value is not None}
    if not values:
        raise InvalidInputError("未读取到真实互动指标，请检查作品页面；缺失指标不会填零")
    raw = case.metrics_raw
    precision = {key: case.metrics_precision.get(key) or (
        "displayed_rounded" if re.search(r"[万千亿kKwWmM+]", raw.get(key) or "") else "displayed")
        for key in values}
    return CollectedMetrics(platform_post_id, platform_user_id, values, raw, precision, datetime.now(timezone.utc))
