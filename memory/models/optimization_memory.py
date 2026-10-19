"""可检索的运营经验版本，保留历史分析及来源。"""
from datetime import datetime
from sqlalchemy import DateTime, ForeignKey, JSON, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column
from memory_database import MemoryBase


class OptimizationMemory(MemoryBase):
    __tablename__ = "optimization_memories"
    __table_args__ = (UniqueConstraint("post_id", "version", name="uq_memory_post_version"),
                      UniqueConstraint("feedback_id", name="uq_memory_feedback"))

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(index=True)
    product_id: Mapped[int] = mapped_column(index=True)
    platform: Mapped[str] = mapped_column(String(100))
    account_id: Mapped[int] = mapped_column(index=True)
    post_id: Mapped[int] = mapped_column(ForeignKey("published_posts.id"), index=True)
    feedback_id: Mapped[int] = mapped_column(ForeignKey("feedback.id"))
    version: Mapped[int] = mapped_column()
    result: Mapped[dict] = mapped_column(JSON)
    is_active: Mapped[bool] = mapped_column(default=True, server_default="true")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
