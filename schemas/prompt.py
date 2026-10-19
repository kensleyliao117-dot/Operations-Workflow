from datetime import datetime

from .base import Name100, RequestSchema, RequiredText, ResponseSchema


class PromptCreate(RequestSchema):
    platform: Name100
    name: Name100
    prompt_content: RequiredText
    is_active: bool = True


class PromptRead(ResponseSchema):
    id: int
    user_id: int
    platform: str
    name: str
    prompt_content: str
    is_active: bool
    version: int
    created_at: datetime
    updated_at: datetime


class PromptUpdate(RequestSchema):
    """完整替换可编辑字段；不能修改所有者、ID 或版本号。"""
    platform: Name100
    name: Name100
    prompt_content: RequiredText
    is_active: bool = True
