"""平台账号登记和查询结构，归属由 JWT 确定。"""
from typing import Literal
from pydantic import field_validator

from .base import Name100, RequestSchema, ResponseSchema


class PlatformAccountCreate(RequestSchema):
    platform: Name100
    platform_user_id: Name100
    display_name: Name100 | None = None

    @field_validator("platform")
    @classmethod
    def normalize_platform(cls, value):
        return value.lower()


class PlatformAccountRead(ResponseSchema):
    id: int
    user_id: int
    platform: str
    platform_user_id: str
    display_name: str | None
    is_active: bool
    registration_method: Literal["manual"] = "manual"
