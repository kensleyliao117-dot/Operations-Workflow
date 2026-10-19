"""统一发布输入、结果和适配器接口，不操作业务数据库。"""
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Literal

from services.exceptions import ServiceUnavailableError


class PublisherError(ServiceUnavailableError):
    """发布准备失败；不包含浏览器 Cookie、供应商响应或私密页面内容。"""


@dataclass(frozen=True, slots=True)
class PublishPayload:
    content_id: int
    version: int
    title: str
    body: str
    tags: tuple[str, ...]
    images: tuple[Path, ...]
    platform_user_id: str
    visibility: Literal["public", "private"] = "public"

    @property
    def editor_body(self):
        return self.body + ("\n\n" + " ".join("#" + tag.lstrip("#") for tag in self.tags) if self.tags else "")


@dataclass(frozen=True, slots=True)
class PublishOutcome:
    status: Literal["published", "failed", "unknown"]
    post_id: str | None = None
    post_url: str | None = None
    error: str | None = None


class BasePublisher(ABC):
    @abstractmethod
    def publish(self, payload: PublishPayload, *, before_submit: Callable[[], None]) -> PublishOutcome:
        """实际点击前必须回调 before_submit；提交后结果不明确时返回 unknown。"""
