from datetime import datetime
from typing import Annotated, Any, Literal
from pydantic import ConfigDict, Field, HttpUrl, StringConstraints, field_validator, model_validator

from .base import Name100, PositiveId, RequestSchema, RequiredText, ResponseSchema


class RednoteCaseCreate(RequestSchema):
    name: RequiredText
    link: RequiredText
    body: str | None = None
    image_urls: list[HttpUrl] = Field(default_factory=list, max_length=100)
    metrics: dict[Literal["likes", "favorites", "comments"], int | None] = Field(default_factory=dict)
    metrics_raw: dict[Literal["likes", "favorites", "comments"], str | None] = Field(default_factory=dict)
    author_platform_id: str | None = Field(default=None, max_length=100)

    @field_validator("metrics", mode="before")
    @classmethod
    def validate_metrics(cls, value):
        if not isinstance(value, dict) or any(v is not None and (type(v) is not int or v < 0) for v in value.values()):
            raise ValueError("互动指标必须为非负整数或 null")
        return value


class RednoteCaseRead(ResponseSchema):
    id: int
    name: str
    link: str
    body: str | None
    image_urls: list[str]
    metrics: dict[str, int | None]
    metrics_raw: dict[str, str | None]
    author_platform_id: str | None
    collected_at: datetime | None


class RednoteCaseSearchRead(RednoteCaseRead):
    analysis_count: int
    latest_analysis_id: int | None
    latest_analysis_version: int | None
    already_analyzed: bool
    history_hint: str


class RednoteCollectRequest(RequestSchema):
    url: RequiredText | None = None
    analyze: bool = False
    reanalyze: bool = False
    model_id: Name100 | None = None

    @model_validator(mode="after")
    def validate_analysis_options(self):
        if not self.analyze and (self.reanalyze or self.model_id is not None):
            raise ValueError("指定重新分析或模型时必须设置 analyze=true")
        return self


class RednoteAnalysisDraft(RequestSchema):
    model_config = ConfigDict(extra="forbid", strict=True)
    summary: RequiredText
    content_structure: list[RequiredText] = Field(min_length=1, max_length=20)
    hook: RequiredText
    emotional_triggers: list[RequiredText] = Field(max_length=20)
    pain_points: list[RequiredText] = Field(max_length=20)
    cta: RequiredText
    image_style: RequiredText
    video_structure: RequiredText
    topics: list[RequiredText] = Field(max_length=30)
    reusable_patterns: list[RequiredText] = Field(min_length=1, max_length=20)
    metrics_observations: list[RequiredText] = Field(max_length=20)
    limitations: list[RequiredText] = Field(min_length=1, max_length=20)


class RednoteAnalysisCreate(RequestSchema):
    cases_id: PositiveId
    content: RequiredText


class RednoteAnalyzeRequest(RequestSchema):
    model_config = ConfigDict(extra="forbid", strict=True)
    model_id: Name100 | None = None
    base_analysis_id: PositiveId | None = None
    use_history: bool = True

    @model_validator(mode="after")
    def validate_history(self):
        if not self.use_history and self.base_analysis_id is not None:
            raise ValueError("不参考历史时不能指定基础分析版本")
        return self


class RednoteAnalysisRevisionDraft(RednoteAnalysisDraft):
    new_insights: list[RequiredText] = Field(min_length=1, max_length=20)


class RednoteFeedbackCreate(RequestSchema):
    content: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=5000)]


class RednoteFeedbackRead(ResponseSchema):
    id: int
    analysis_id: int
    content: str
    created_at: datetime


class RednoteReviseRequest(RequestSchema):
    model_config = ConfigDict(extra="forbid", strict=True)
    model_id: Name100 | None = None
    feedback_ids: list[PositiveId] = Field(min_length=1, max_length=20)

    @field_validator("feedback_ids")
    @classmethod
    def unique_feedback(cls, values):
        if len(values) != len(set(values)):
            raise ValueError("反馈 ID 不能重复")
        return values


class RednoteAnalysisRead(ResponseSchema):
    id: int
    cases_id: int
    content: str
    result: RednoteAnalysisDraft | None
    model: str | None
    source_fingerprint: str | None
    version: int
    parent_analysis_id: int | None
    revision_type: str
    provider: str | None
    model_profile: str | None
    feedback_ids: list[int]
    new_insights: list[str]
    created_at: datetime


class RednoteCollectResult(ResponseSchema):
    case: RednoteCaseRead
    analysis: RednoteAnalysisRead | None = None
    analysis_status: Literal["not_requested", "existing", "completed", "failed"] = "not_requested"
    analysis_error: str | None = None
    case_already_saved: bool = False
    analysis_count: int = 0
    already_analyzed: bool = False
    latest_analysis_id: int | None = None
    history_hint: str = "尚无历史分析"
    analysis_source_changed: bool = False


class RednoteAnalysisDifference(ResponseSchema):
    field: str
    left: Any
    right: Any


class RednoteAnalysisModelRead(ResponseSchema):
    id: str
    provider: str
    model: str
    ready: bool


class RednoteAnalysisComparison(ResponseSchema):
    case_id: int
    left: RednoteAnalysisRead
    right: RednoteAnalysisRead
    same_source: bool | None
    same_parent: bool
    same_feedback: bool
    differences: list[RednoteAnalysisDifference]
    comparison_hint: str
