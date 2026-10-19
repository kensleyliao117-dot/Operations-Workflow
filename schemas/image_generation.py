"""文案版本的配图生成参数与保存结果。"""
from typing import Literal

from pydantic import ConfigDict, Field

from .base import Name100, RequestSchema, ResponseSchema
from .content import ContentRead
from .notices import IMAGE_NOTICE, user_notices
from .rules import RuleCheckRead
from .workflow import WorkflowRead
from .resource_model import ImageSize
from .base import PositiveId


def image_notices():
    return [*user_notices(), IMAGE_NOTICE]


class ImageGenerationRequest(RequestSchema):
    model_config = ConfigDict(extra="forbid", strict=True)

    style_requirement: str = Field(default="", max_length=1000)
    count: int = Field(default=1, ge=1, le=4)
    size: ImageSize = "1728*2368"
    # 替换仅影响新版本，历史版本的图片始终保留。
    replace_existing: bool = False
    model_id: Name100 | None = None
    image_model_id: PositiveId | None = None


class ImagePromptDraft(RequestSchema):
    model_config = ConfigDict(extra="forbid", strict=True)

    prompt: str = Field(min_length=1, max_length=600)
    negative_prompt: str = Field(min_length=1, max_length=500)


class GeneratedImageRead(ResponseSchema):
    index: int
    storage_key: str
    download_path: str


class ImageGenerationSavedResult(ResponseSchema):
    content: ContentRead
    image_model: str
    prompt_model: str
    request_id: str | None
    images: list[GeneratedImageRead]
    task_status: str
    review_status: Literal["saved", "waiting_review", "waiting_rule_changes", "submission_failed"]
    review_error: str | None = None
    rule_check: RuleCheckRead | None = None
    workflow: WorkflowRead | None = None
    review_required: Literal[True] = True
    notices: list[str] = Field(default_factory=image_notices)
