"""接口数据结构的公共配置与字段类型。"""
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

PositiveId = Annotated[int, Field(gt=0)]
RequiredText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
Name100 = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]


class RequestSchema(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ResponseSchema(BaseModel):
    model_config = ConfigDict(from_attributes=True)
