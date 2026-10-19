"""真实指标、运营分析和经验查询协议。"""
from datetime import datetime, timezone
from typing import Annotated, Literal
from pydantic import ConfigDict, Field, StrictInt, StringConstraints, field_validator, model_validator

from schemas.base import PositiveId, RequestSchema, ResponseSchema

RequestKey = Annotated[str, Field(min_length=8, max_length=100, pattern=r"^[a-zA-Z0-9_-]+$")]


class MetricValues(RequestSchema):
    model_config = ConfigDict(extra="forbid", strict=True)
    impressions: int | None = Field(default=None, ge=0)
    views: int | None = Field(default=None, ge=0)
    clicks: int | None = Field(default=None, ge=0)
    likes: int | None = Field(default=None, ge=0)
    favorites: int | None = Field(default=None, ge=0)
    comments: int | None = Field(default=None, ge=0)
    conversions: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def nonempty(self):
        if all(value is None for value in self.model_dump().values()):
            raise ValueError("至少提供一项真实指标，缺失值不得填为零")
        return self


class ManualMetricsRequest(RequestSchema):
    idempotency_key: RequestKey
    account_id: PositiveId
    metrics: MetricValues
    observed_at: datetime

    @field_validator("observed_at")
    @classmethod
    def aware_time(cls, value):
        if value.tzinfo is None or value.utcoffset() is None or value > datetime.now(timezone.utc):
            raise ValueError("observed_at 必须包含时区且不能晚于当前时间")
        return value.astimezone(timezone.utc)


class CollectMetricsRequest(RequestSchema):
    idempotency_key: RequestKey
    source: Literal["auto", "rednote_creator", "rednote_public"] = "auto"
    # 保留可访问的分享参数，但只能指向发布记录中的作品。
    source_url: str | None = Field(default=None, max_length=4096)


class FeedbackRequest(RequestSchema):
    metrics_ids: list[PositiveId] = Field(min_length=1, max_length=20)
    user_note: str | None = Field(default=None, max_length=2000)

    @field_validator("metrics_ids")
    @classmethod
    def unique_ids(cls, values):
        if len(set(values)) != len(values):
            raise ValueError("指标快照 ID 不能重复")
        return values


class Lesson(RequestSchema):
    model_config = ConfigDict(extra="forbid", strict=True)
    category: Literal["hook", "title", "body", "image", "cta", "audience"]
    observation: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=800)]
    next_experiment: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=800)]
    evidence_metric_ids: list[StrictInt] = Field(min_length=1, max_length=20)
    confidence: Literal["low", "medium"]
    conclusion_type: Literal["hypothesis"]


class FeedbackDraft(RequestSchema):
    model_config = ConfigDict(extra="forbid", strict=True)
    summary: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=1200)]
    lessons: list[Lesson] = Field(min_length=1, max_length=8)
    limitations: list[Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=500)]] = Field(min_length=1, max_length=8)


class PublicationRead(ResponseSchema):
    id: int
    user_id: int
    product_id: int
    task_id: int
    content_id: int
    content_version: int
    publish_record_id: int
    account_id: int
    platform: str
    platform_user_id: str
    platform_post_id: str | None
    platform_post_url: str | None
    published_at: datetime | None
    content_snapshot: dict
    source_fingerprint: str
    created_at: datetime


class MetricsRead(ResponseSchema):
    id: int
    post_id: int
    metrics: dict[str, int]
    metrics_raw: dict
    precision: dict
    source: Literal["manual", "rednote_public", "rednote_creator"]
    observed_at: datetime
    created_at: datetime
    published_at: datetime | None = None
    elapsed_since_publish_seconds: int | None = None


class FeedbackPublicationRead(ResponseSchema):
    id: int
    task_id: int
    content_id: int
    content_version: int | None
    product_id: int
    product_name: str
    title: str
    platform: str
    account_id: int | None
    visibility: Literal["public", "private"]
    publish_status: Literal["published"]
    published_at: datetime | None
    platform_post_id: str | None
    platform_post_url: str | None


class MemoryReference(ResponseSchema):
    id: int
    version: int
    product_id: int
    platform: str
    account_id: int
    post_id: int
    feedback_id: int
    result: FeedbackDraft
    created_at: datetime


class MemoryRead(MemoryReference):
    is_active: bool


class FeedbackRead(ResponseSchema):
    id: int
    post_id: int
    source_fingerprint: str
    user_note: str | None
    evidence_snapshot: dict
    result: FeedbackDraft
    model: str
    created_at: datetime


class MemoryUpdate(RequestSchema):
    is_active: bool


class SyncRead(ResponseSchema):
    publish_record_id: int
    status: str
    attempts: int
    error_message: str | None
