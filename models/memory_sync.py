"""业务库持久化经验同步事件，发布成功不依赖经验库即时可用。"""
from datetime import datetime
from sqlalchemy import DateTime, ForeignKey, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column
from database import Base


class MemorySyncEvent(Base):
    __tablename__ = "memory_sync_events"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    publish_record_id: Mapped[int] = mapped_column(ForeignKey("publish_records.id"), unique=True)
    status: Mapped[str] = mapped_column(String(20), default="pending", server_default="pending")
    attempts: Mapped[int] = mapped_column(default=0, server_default="0")
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    synced_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
