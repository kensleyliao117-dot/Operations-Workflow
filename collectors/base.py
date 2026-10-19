"""各平台共用的采集接口，页面解析与数据保存分别实现。"""
from abc import ABC, abstractmethod
from dataclasses import dataclass, field

from services.exceptions import ServiceUnavailableError


class CollectorError(ServiceUnavailableError):
    """采集或浏览器操作失败，不回显 Cookie、页面内容或底层连接信息。"""


class ChromeConnectionError(CollectorError):
    """Chrome 未开启 CDP、连接已断开或浏览器操作失败。"""


class ChromeTimeoutError(CollectorError):
    """连接或页面导航超时。"""


@dataclass(frozen=True, slots=True)
class CollectedCase:
    """采集结果结构，由 Service 校验归属并保存。"""
    platform: str
    source_url: str
    title: str
    body: str
    image_urls: tuple[str, ...] = ()
    tags: tuple[str, ...] = ()
    metrics: dict[str, int | None] = field(default_factory=dict)
    metrics_raw: dict[str, str | None] = field(default_factory=dict)
    author_platform_id: str | None = None
    metrics_precision: dict[str, str] = field(default_factory=dict)


class BaseCollector(ABC):
    @abstractmethod
    def collect(self, source_url: str) -> CollectedCase:
        """读取一个公开案例；不执行数据库写入或发布操作。"""
