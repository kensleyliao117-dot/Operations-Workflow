"""文本模型连接配置；API Key 仅用于写入，不出现在响应中。"""
from typing import Annotated, Literal

from pydantic import SecretStr, StringConstraints, field_validator

from integrations.llm.compatible import base_url as validate_base_url

from .base import Name100, RequestSchema, ResponseSchema


class AnalysisModelWrite(RequestSchema):
    name: Name100
    provider: Name100
    model: Annotated[str, StringConstraints(strip_whitespace=True, pattern=r"^[a-zA-Z0-9][a-zA-Z0-9._:/-]{0,99}$")]
    is_active: bool = True
    base_url: Annotated[str, StringConstraints(max_length=2048)] | None = None
    api_key: SecretStr | None = None
    is_default: bool = False

    @field_validator("base_url")
    @classmethod
    def valid_base_url(cls, value):
        return validate_base_url(value) if value is not None else None

    @field_validator("api_key")
    @classmethod
    def valid_api_key(cls, value):
        if value is not None:
            key = value.get_secret_value().strip()
            if not 1 <= len(key) <= 4096 or any(ord(c) < 33 or ord(c) > 126 for c in key):
                raise ValueError("API Key 必须为 1 到 4096 位有效字符")
            return SecretStr(key)
        return value

    @field_validator("model")
    @classmethod
    def chat_model_only(cls, value):
        if "embedding" in value.lower():
            raise ValueError("分析需要聊天模型，不能使用 Embedding 模型")
        return value


class AnalysisModelRead(ResponseSchema):
    id: str
    provider: str
    model: str
    ready: bool
    name: str
    source: Literal["system", "user"]
    config_id: int | None = None
    is_active: bool = True
    is_default: bool = False
    base_url: str | None = None
    credential_source: Literal["user", "system"] = "system"


class ModelConnectionRead(ResponseSchema):
    ok: Literal[True] = True
    model: str
    message: str = "模型连接成功，已返回有效文字"

