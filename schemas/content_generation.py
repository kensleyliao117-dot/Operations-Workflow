"""内容生成、任务生成并保存的输入及返回值。"""
from datetime import datetime
from typing import Annotated, Literal

from pydantic import ConfigDict, Field, StringConstraints, field_validator

from .base import Name100, PositiveId, RequestSchema, RequiredText, ResponseSchema
from .rag import RetrievedSourceRead
from .notices import user_notices
from memory.schemas import MemoryReference


class TaskContentGenerationRequest(RequestSchema):
    """产品、平台和运营需求从任务读取，不接受客户端覆盖。"""
    model_config = ConfigDict(extra="forbid", strict=True)

    prompt_id: PositiveId | None = None
    model_id: Name100 | None = None
    analysis_ids: list[PositiveId] = Field(
        default_factory=list, max_length=5,
        description="当前用户已保存的结构化小红书分析 ID，仅用于创作参考；不传时不使用案例。",
    )
    top_k: int = Field(default=5, ge=1, le=20)
    min_similarity: float | None = Field(default=None, ge=-1, le=1, allow_inf_nan=False)
    max_context_chars: int = Field(default=12000, ge=256, le=50000)
    use_memory: bool = True
    memory_limit: int = Field(default=5, ge=1, le=5)

    @field_validator("analysis_ids")
    @classmethod
    def unique_analysis_ids(cls, values):
        if len(values) != len(set(values)):
            raise ValueError("案例分析 ID 不能重复")
        return values


class ContentGenerationRequest(TaskContentGenerationRequest):
    product_id: PositiveId
    user_requirement: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2000)]
    platform: Name100 = "rednote"


class GeneratedDraft(RequestSchema):
    """各平台文本草稿共用的字段，平台子类负责对应的格式约束。"""
    model_config = ConfigDict(extra="forbid", strict=True)

    # 300 对应当前 Content 标题字段容量，不代表任何平台的发布限制。
    title: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=300)]
    body: RequiredText
    tags: list[Name100] = Field(min_length=1, max_length=20)

    @field_validator("title", "body")
    @classmethod
    def check_text(cls, value, info):
        if "\x00" in value or (info.field_name == "title" and any(c in value for c in "\r\n")):
            raise ValueError("正文不能包含 NUL，标题必须为单行文本")
        return value


class RednoteDraft(GeneratedDraft):
    @field_validator("tags")
    @classmethod
    def normalize_tags(cls, values):
        tags = []
        for value in values:
            tag = value.lstrip("#").strip()
            if not tag or "#" in tag or "\x00" in tag or any(c.isspace() for c in tag):
                raise ValueError("话题标签必须为非空词条，不包含内部空白或 #")
            if tag not in tags:
                tags.append(tag)
        return tags


class CreativePatterns(ResponseSchema):
    """案例分析中仅用于创作参考的字段，不构成产品事实依据。"""
    content_structure: list[str]
    hook: str
    emotional_triggers: list[str]
    cta: str
    reusable_patterns: list[str]


class AnalysisReferenceRead(ResponseSchema):
    analysis_id: int
    analysis_version: int
    case_id: int
    analysis_model: str | None
    source_fingerprint: str | None
    analysis_created_at: datetime
    creative_patterns: CreativePatterns


class ContentGenerationResult(ResponseSchema):
    product_id: int
    platform: str
    title: str
    body: str
    tags: list[str]
    generation_model: str
    generation_model_profile: dict | None = None
    embedding_model: str
    embedding_dimensions: int
    prompt_id: int | None
    prompt_version: int | None
    sources: list[RetrievedSourceRead]
    analysis_references: list[AnalysisReferenceRead] = Field(default_factory=list)
    memory_references: list[MemoryReference] = Field(default_factory=list)
    memory_status: Literal["used", "no_matches", "unavailable", "disabled"] = "no_matches"
    validation_scope: Literal["structure"] = "structure"
    review_required: Literal[True] = True
    notices: list[str] = Field(default_factory=user_notices)


class ContentGenerationSavedResult(ContentGenerationResult):
    task_id: int
    content_id: int
    version: int = Field(ge=1)
    task_status: Literal["running", "pending_review"] = "pending_review"
    created_at: datetime
