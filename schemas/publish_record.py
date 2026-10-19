from datetime import datetime, timezone
from enum import Enum
from typing import Literal
from pydantic import ConfigDict, Field, HttpUrl, StrictBool, field_validator, model_validator

from .base import Name100, PositiveId, RequestSchema, RequiredText, ResponseSchema


class PublishStatus(str, Enum):
    pending = "pending"
    publishing = "publishing"
    published = "published"
    failed = "failed"
    unknown = "unknown"


class PublishRequest(RequestSchema):
    task_id: PositiveId
    content_id: PositiveId
    platform: Name100
    account_id: PositiveId
    publisher_backend: Literal["manual", "chrome"] = "manual"
    visibility: Literal["public", "private"] = "public"
    idempotency_key: str | None = Field(default=None, min_length=8, max_length=100, pattern=r"^[a-zA-Z0-9_-]+$")

    @model_validator(mode="after")
    def require_request_key(self):
        if self.publisher_backend == "chrome" and self.idempotency_key is None:
            raise ValueError("Chrome 发布需提供 idempotency_key，用于安全重试同一请求")
        return self


class PublishRecordRead(ResponseSchema):
    id: int
    task_id: int
    content_id: int
    platform: str
    account_id: int | None
    publish_status: PublishStatus
    platform_post_id: str | None
    platform_post_url: HttpUrl | None
    published_at: datetime | None
    error_message: str | None
    publisher_backend: Literal["manual", "chrome"]
    visibility: Literal["public", "private"]
    idempotency_key: str | None
    content_version: int | None
    review_id: int | None
    delivery_phase: str
    started_at: datetime | None
    submitted_at: datetime | None
    publication_details: dict = Field(default_factory=dict)


class PublishExecuteRequest(RequestSchema):
    confirm_account: Literal[True]

    @field_validator("confirm_account", mode="before")
    @classmethod
    def explicit_boolean(cls, value):
        if type(value) is not bool or not value:
            raise ValueError("需明确传入 confirm_account=true")
        return value


class PublishRetryRequest(RequestSchema):
    idempotency_key: str = Field(min_length=8, max_length=100, pattern=r"^[a-zA-Z0-9_-]+$")


class FinalPublishedContent(RequestSchema):
    title: str | None = Field(default=None, max_length=300)
    body: str | None = Field(default=None, max_length=50000)
    tags: list[Name100] | None = Field(default=None, max_length=20)


class PublishResult(RequestSchema):
    """登记人工发布或发布适配器的真实结果，不调用平台 API。"""
    publish_status: Literal["published", "failed", "unknown"]
    platform_post_id: RequiredText | None = None
    platform_post_url: HttpUrl | None = None
    error_message: RequiredText | None = None
    account_id: PositiveId | None = None
    confirm_not_published: StrictBool = False
    published_at: datetime | None = None
    final_content: FinalPublishedContent | None = None
    external_materials_note: str | None = Field(default=None, max_length=2000)

    @field_validator("published_at")
    @classmethod
    def validate_published_at(cls, value):
        if value is not None:
            if value.tzinfo is None or value.utcoffset() is None or value > datetime.now(timezone.utc):
                raise ValueError("实际发布时间必须包含时区且不能晚于当前时间")
            return value.astimezone(timezone.utc)
        return value

    @model_validator(mode="after")
    def validate_result(self):
        if self.publish_status != "published" and (self.final_content is not None or self.external_materials_note is not None):
            raise ValueError("仅发布成功时可以登记最终文案和平台补充素材说明")
        if self.published_at is not None and self.publish_status != "published":
            raise ValueError("只有发布成功可以提供实际发布时间")
        if self.publish_status == "published":
            if self.account_id is None:
                raise ValueError("发布成功需确认实际发布的平台账号 account_id")
            if (self.platform_post_id is None and self.platform_post_url is None) or self.error_message is not None:
                raise ValueError("发布成功需提供平台帖子 ID 或作品链接，且不能带错误原因")
        elif self.error_message is None or self.platform_post_id is not None or self.platform_post_url is not None:
            raise ValueError("发布失败或结果不明需提供原因，且不能带平台帖子 ID 或作品链接")
        return self


class PublishMetricsValues(RequestSchema):
    model_config = ConfigDict(extra="forbid", strict=True)
    impressions: int | None = Field(default=None, ge=0)
    clicks: int | None = Field(default=None, ge=0)
    likes: int | None = Field(default=None, ge=0)
    favorites: int | None = Field(default=None, ge=0)
    comments: int | None = Field(default=None, ge=0)
    conversions: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def require_values(self):
        if all(value is None for value in self.model_dump().values()):
            raise ValueError("至少提供一个真实运营指标")
        return self


class PublishMetricsCreate(RequestSchema):
    metrics: PublishMetricsValues


class PublishMetricsRead(ResponseSchema):
    id: int
    publish_record_id: int
    account_id: int
    metrics: dict[str, int | None]
    source: Literal["manual"]
    created_at: datetime
