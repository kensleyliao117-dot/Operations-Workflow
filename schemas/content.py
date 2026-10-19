from datetime import datetime
from typing import Annotated

from pydantic import Field, HttpUrl, StringConstraints, model_validator

from .base import Name100, PositiveId, RequestSchema, RequiredText, ResponseSchema
from .notices import IMAGE_NOTICE, user_notices


class ImageAsset(RequestSchema):
    """图片存储引用；storage_key 相对于 storage，不保存临时生图 URL 作为唯一标识。"""
    storage_key: RequiredText
    url: HttpUrl | None = None
    prompt: str | None = None


class ContentCreate(RequestSchema):
    task_id: PositiveId
    title: Annotated[str, StringConstraints(strip_whitespace=True, max_length=300)] = ""
    body: str = ""
    tags: list[Name100] = Field(default_factory=list, max_length=20)
    image_prompt: str | None = None
    video_script: str | None = None
    image_assets: list[ImageAsset] = Field(default_factory=list)
    # 版本号应由 service 分配，修改内容时创建新版本。

    @model_validator(mode="after")
    def require_material(self):
        if not (self.title.strip() or self.body.strip() or self.image_assets or self.image_prompt):
            raise ValueError("至少提供文案、图片或待生成图片的需求")
        return self


class ContentRead(ResponseSchema):
    id: int
    task_id: int
    title: str
    body: str
    tags: list[str]
    image_prompt: str | None
    video_script: str | None
    image_assets: list[ImageAsset]
    generation_metadata: dict | None
    version: int = Field(ge=1)
    created_at: datetime
    notices: list[str] = Field(default_factory=user_notices)

    @model_validator(mode="after")
    def include_image_notice(self):
        if (self.generation_metadata or {}).get("image_generation") and IMAGE_NOTICE not in self.notices:
            self.notices.append(IMAGE_NOTICE)
        return self


class ContentUpdate(RequestSchema):
    """仅传修改字段；必填文本不能显式置空，可选文本可用 null 清除。"""
    title: Annotated[str, StringConstraints(strip_whitespace=True, max_length=300)] | None = None
    body: str | None = None
    tags: list[Name100] | None = Field(default=None, max_length=20)
    image_prompt: str | None = None
    video_script: str | None = None
    image_assets: list[ImageAsset] | None = None

    @model_validator(mode="after")
    def validate_changes(self):
        if not self.model_fields_set:
            raise ValueError("至少提供一个需要修改的字段")
        for name in ("title", "body", "tags", "image_assets"):
            if name in self.model_fields_set and getattr(self, name) is None:
                raise ValueError(f"{name} 不能为 null")
        return self
