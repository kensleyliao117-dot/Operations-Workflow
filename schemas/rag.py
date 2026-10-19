from typing import Annotated, Any

from pydantic import Field, StringConstraints, model_validator

from .base import PositiveId, RequestSchema, ResponseSchema


class IndexRequest(RequestSchema):
    chunk_size: int = Field(default=800, ge=1, le=2000)
    chunk_overlap: int = Field(default=100, ge=0)

    @model_validator(mode="after")
    def check_overlap(self):
        if self.chunk_overlap >= self.chunk_size:
            raise ValueError("chunk_overlap 必须小于 chunk_size")
        return self


class IndexRead(ResponseSchema):
    product_id: int
    document_id: int
    filename: str
    status: str
    chunk_count: int
    model: str
    dimensions: int
    document_hash: str


class RetrieveRequest(RequestSchema):
    query: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2000)]
    top_k: int = Field(default=5, ge=1, le=20)
    min_similarity: float | None = Field(default=None, ge=-1, le=1, allow_inf_nan=False)
    document_id: PositiveId | None = None
    max_context_chars: int = Field(default=12000, ge=256, le=50000)


class RetrievedSourceRead(ResponseSchema):
    citation: str
    chunk_id: str
    document_id: int
    filename: str
    chunk_index: int
    similarity: float
    text: str
    truncated: bool
    metadata: dict[str, Any]


class RetrievalRead(ResponseSchema):
    product_id: int
    query: str
    model: str
    dimensions: int
    context: str
    sources: list[RetrievedSourceRead]
