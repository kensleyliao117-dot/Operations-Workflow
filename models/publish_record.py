from datetime import datetime
from sqlalchemy import String, ForeignKey, ForeignKeyConstraint, Text, DateTime, func, JSON, UniqueConstraint, Index, text, Boolean, false
from sqlalchemy.orm import Mapped, mapped_column,relationship

from database import Base
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from .task import Task
    from .content import Content

#创建任务表
class PublishRecord(Base):
  
    __tablename__ = "publish_records"
    __table_args__ = (
        UniqueConstraint("id", "account_id", name="uq_publish_record_account"),
        UniqueConstraint("task_id", "idempotency_key", name="uq_publish_task_request"),
        Index("uq_publish_active_task", "task_id", unique=True,
              postgresql_where=text("publish_status IN ('publishing', 'published', 'unknown')"),
              sqlite_where=text("publish_status IN ('publishing', 'published', 'unknown')")),
    )

    id:Mapped[int]=mapped_column(primary_key=True)
    task_id:Mapped[int]=mapped_column(ForeignKey("tasks.id"))
    content_id:Mapped[int]=mapped_column(ForeignKey("contents.id"))
    platform:Mapped[str]=mapped_column(String(100),nullable=False)
    # 历史记录允许为空，新发布必须显式选择实际平台账号。
    account_id: Mapped[int | None] = mapped_column(ForeignKey("platform_accounts.id"), nullable=True, index=True)
    publish_status:Mapped[str]=mapped_column(String(100))
    feedback_deleted: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())
    publisher_backend: Mapped[str] = mapped_column(String(30), default="manual", server_default="manual")
    visibility: Mapped[str] = mapped_column(String(20), default="public", server_default="public")
    idempotency_key: Mapped[str | None] = mapped_column(String(100), nullable=True)
    content_version: Mapped[int | None] = mapped_column(nullable=True)
    review_id: Mapped[int | None] = mapped_column(ForeignKey("reviews.id"), nullable=True)
    content_snapshot: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    content_fingerprint: Mapped[str | None] = mapped_column(String(64), nullable=True)
    publication_details: Mapped[dict] = mapped_column(JSON, default=dict, server_default=text("'{}'"))
    # 提交前落库 submitting；进程重启后不得自动重放该外部副作用。
    delivery_phase: Mapped[str] = mapped_column(String(30), default="prepared", server_default="prepared")
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    platform_post_id:Mapped[str | None]=mapped_column()
    platform_post_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    published_at: Mapped[datetime | None] = mapped_column(
        DateTime,
        nullable=True
    )
    error_message:Mapped[str | None]=mapped_column(Text, nullable=True)
    task:Mapped["Task"]=relationship(back_populates="publish_records")
    content:Mapped["Content"]=relationship(back_populates="publish_records")


class PublishMetrics(Base):
    __tablename__ = "publish_metrics"
    __table_args__ = (ForeignKeyConstraint(
        ["publish_record_id", "account_id"], ["publish_records.id", "publish_records.account_id"],
        name="fk_publish_metrics_record_account"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    publish_record_id: Mapped[int] = mapped_column(ForeignKey("publish_records.id"), index=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("platform_accounts.id"))
    metrics: Mapped[dict] = mapped_column(JSON)
    source: Mapped[str] = mapped_column(String(30), default="manual")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
