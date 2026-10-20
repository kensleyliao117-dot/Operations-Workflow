from datetime import datetime
from enum import Enum
from pydantic import JsonValue

from .base import Name100, PositiveId, RequestSchema, RequiredText, ResponseSchema


class TaskStatus(str, Enum):
    pending = "pending"
    running = "running"
    pending_review = "pending_review"
    approved = "approved"
    rejected = "rejected"
    publishing = "publishing"
    completed = "completed"
    failed = "failed"
    cancelled = "cancelled"


class TaskCreate(RequestSchema):
    """任务归属由后端提供，不接受请求中指定用户。"""
    product_id: PositiveId
    platform_name: Name100 | None = None
    task_type: Name100
    input_text: RequiredText


class TaskStatusUpdate(RequestSchema):
    # 状态转换是否合法，由 service 层判断。
    status: TaskStatus


class TaskCheckpointSave(RequestSchema):
    # 每次替换整个快照；不要存 API 密钥、Session 或异常对象。
    checkpoint: dict[str, JsonValue]


class TaskFailureSave(TaskCheckpointSave):
    error_message: RequiredText


class TaskRead(ResponseSchema):
    is_deleted: bool = False
    id: int
    user_id: int
    product_id: int | None
    platform_name: str | None
    task_type: str
    input_text: str
    status: TaskStatus
    checkpoint: dict[str, JsonValue] | None
    failed_from_status: TaskStatus | None
    error_message: str | None
    created_at: datetime
    updated_at: datetime
