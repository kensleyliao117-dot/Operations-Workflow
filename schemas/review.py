from datetime import datetime
from enum import Enum

from .base import PositiveId, RequestSchema, ResponseSchema


class ReviewStatus(str, Enum):
    approved = "approved"
    rejected = "rejected"


class ReviewCreate(RequestSchema):
    task_id: PositiveId
    content_id: PositiveId
    status: ReviewStatus
    comment: str | None = None


class ReviewRead(ResponseSchema):
    id: int
    task_id: int
    content_id: int
    reviewer_id: int
    status: ReviewStatus
    comment: str | None
    created_at: datetime
