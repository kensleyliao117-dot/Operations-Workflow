"""结构化分析与对应证据快照。"""
from datetime import datetime
from sqlalchemy import DateTime, ForeignKey, JSON, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column
from memory_database import MemoryBase


class Feedback(MemoryBase):
    __tablename__ = "feedback"
    __table_args__ = (UniqueConstraint("post_id", "source_fingerprint", name="uq_memory_feedback_source"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    post_id: Mapped[int] = mapped_column(ForeignKey("published_posts.id"), index=True)
    user_id: Mapped[int] = mapped_column(index=True)
    source_fingerprint: Mapped[str] = mapped_column(String(64))
    user_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    evidence_snapshot: Mapped[dict] = mapped_column(JSON)
    result: Mapped[dict] = mapped_column(JSON)
    model: Mapped[str] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
