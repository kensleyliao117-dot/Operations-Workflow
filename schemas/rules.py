"""可配置规则及内容风险检查请求和结果。"""
from datetime import datetime
from typing import Annotated, Literal

from pydantic import ConfigDict, Field, HttpUrl, StringConstraints, model_validator

from .base import Name100, PositiveId, RequestSchema, RequiredText, ResponseSchema
from .notices import user_notices

RuleTarget = Literal["title", "body", "tags", "image_prompt", "video_script", "image_assets"]
Severity = Literal["low", "medium", "high"]


class RuleOptions(RequestSchema):
    content: RequiredText | None = None
    rule_type: Literal["ai", "keyword", "length"] = "ai"
    keywords: list[Name100] = Field(default_factory=list, max_length=20)
    max_length: Annotated[int, Field(strict=True, ge=1, le=50000)] | None = None
    targets: list[RuleTarget] = Field(default_factory=lambda: ["title", "body", "tags", "image_prompt", "video_script"],
                                     min_length=1, max_length=6)
    severity: Severity = "medium"
    is_active: bool = True
    source_url: HttpUrl | None = Field(default=None, max_length=2048)

    @model_validator(mode="after")
    def check_match_options(self):
        if self.rule_type == "keyword" and not self.keywords:
            raise ValueError("关键词规则至少需要一个关键词")
        if self.rule_type == "length" and self.max_length is None:
            raise ValueError("长度规则需要 max_length")
        if self.rule_type != "keyword" and self.keywords:
            raise ValueError("仅关键词规则可以配置 keywords")
        if self.rule_type != "length" and self.max_length is not None:
            raise ValueError("仅长度规则可以配置 max_length")
        if self.rule_type == "ai" and not self.content:
            raise ValueError("AI 辅助规则需要规则说明 content")
        if len(self.targets) != len(set(self.targets)) or len(self.keywords) != len(set(self.keywords)):
            raise ValueError("规则目标和关键词不能重复")
        return self


class CustomRuleCreate(RuleOptions):
    platform_name: Name100 | None = None


class CustomRuleUpdate(CustomRuleCreate):
    pass


class PlatformRuleCreate(RuleOptions):
    platform_name: Name100


class RuleRead(ResponseSchema):
    id: int
    content: str | None
    rule_type: str
    keywords: list[str]
    max_length: int | None
    targets: list[str]
    severity: str
    is_active: bool
    version: int
    source_url: str | None


class CustomRuleRead(RuleRead):
    user_id: int
    platform_name: str | None


class PlatformRuleRead(RuleRead):
    platform_name: str


class AIRisk(RequestSchema):
    model_config = ConfigDict(extra="forbid", strict=True)
    rule_scope: Literal["platform", "custom", "general"]
    rule_id: PositiveId | None
    field: RuleTarget
    excerpt: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=500)]
    severity: Severity
    reason: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=1000)]
    suggestion: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=1000)]

    @model_validator(mode="after")
    def validate_rule_reference(self):
        if (self.rule_scope == "general") != (self.rule_id is None):
            raise ValueError("通用提示不关联规则 ID，平台和自定义风险必须关联规则 ID")
        return self


class RuleRisk(AIRisk):
    source: Literal["keyword", "length", "ai"]


class RuleAIDraft(RequestSchema):
    model_config = ConfigDict(extra="forbid", strict=True)
    risks: list[AIRisk] = Field(max_length=50)


class RuleCheckRequest(RequestSchema):
    content_id: PositiveId
    force: bool = False


class RuleCheckRead(ResponseSchema):
    id: int
    content_id: int
    content_version: int
    task_id: int
    platform_name: str
    content_fingerprint: str
    rules_fingerprint: str
    rules_snapshot: list[dict]
    status: Literal["no_risk_detected", "risk_detected", "incomplete", "error"]
    risks: list[RuleRisk]
    coverage_warnings: list[str]
    model: str
    error_message: str | None
    created_at: datetime
    image_binary_reviewed: Literal[False] = False
    notices: list[str] = Field(default_factory=user_notices)
