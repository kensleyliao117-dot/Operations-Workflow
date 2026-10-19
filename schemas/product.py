from datetime import datetime
from pydantic import model_validator
from .base import Name100, RequestSchema, ResponseSchema


class ProductCreate(RequestSchema):
    name: Name100
    description: str | None = None


class ProductUpdate(RequestSchema):
    name: Name100 | None = None
    description: str | None = None

    @model_validator(mode="after")
    def validate_changes(self):
        if not self.model_fields_set:
            raise ValueError("至少提供一个需要修改的字段")
        if "name" in self.model_fields_set and self.name is None:
            raise ValueError("产品名称不能为 null")
        return self


class ProductRead(ResponseSchema):
    id: int
    user_id: int
    name: str
    description: str | None
    created_at: datetime


class ProductDocumentRead(ResponseSchema):
    id: int
    product_id: int
    filename: str
    size_bytes: int
    content_type: str
    status: str
    created_at: datetime
