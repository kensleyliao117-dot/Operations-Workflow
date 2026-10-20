"""工作流入口、人工审核恢复和执行状态。"""
from typing import Literal

from pydantic import Field, model_validator

from .base import RequestSchema, ResponseSchema
from .content_generation import TaskContentGenerationRequest
from .base import Name100
from .notices import user_notices
from .resource_model import ImageSize
from .base import PositiveId


class WorkflowGenerationRequest(TaskContentGenerationRequest):
    output_mode: Literal["text", "images", "text_images"] = "text"
    image_style_requirement: str = Field(default="", max_length=1000)
    image_count: int = Field(default=1, ge=1, le=4)
    image_size: ImageSize = "1536*2048"
    image_model_id: PositiveId | None = None

    def text_options(self):
        return TaskContentGenerationRequest(**self.model_dump(include=set(TaskContentGenerationRequest.model_fields)))


class WorkflowResumeRequest(RequestSchema):
    action: Literal["review", "submit_changes", "submit_review", "regenerate", "recheck"] = "review"
    generation: WorkflowGenerationRequest | None = None

    @model_validator(mode="after")
    def generation_options(self):
        if self.generation is not None and self.action != "regenerate":
            raise ValueError("仅重新生成时可以更换生成参数")
        return self


class WorkflowRead(ResponseSchema):
    task_id: int
    run_id: str
    task_status: str
    status: str
    stage: str | None
    content_id: int | None
    content_version: int | None
    rule_check: dict | None
    next_nodes: list[str]
    interrupt: dict | None
    error_message: str | None
    notices: list[str] = Field(default_factory=user_notices)
