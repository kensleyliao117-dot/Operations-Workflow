"""工作流身份和执行结果索引；节点进度由 LangGraph checkpointer 保存。"""
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, JSON, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from database import Base


class TaskWorkflow(Base):
    __tablename__ = "task_workflows"

    task_id: Mapped[int] = mapped_column(ForeignKey("tasks.id"), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    run_id: Mapped[str] = mapped_column(String(32), unique=True)
    options: Mapped[dict] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(30), default="running")
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())
