"""发布作品的归档快照和真实指标观测。"""
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, JSON, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from memory_database import MemoryBase


class PublishedPost(MemoryBase):
    __tablename__ = "published_posts"
    __table_args__ = (UniqueConstraint("user_id", "publish_record_id", name="uq_memory_publication"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(index=True)
    product_id: Mapped[int] = mapped_column(index=True)
    task_id: Mapped[int] = mapped_column()
    content_id: Mapped[int] = mapped_column()
    content_version: Mapped[int] = mapped_column()
    publish_record_id: Mapped[int] = mapped_column(index=True)
    account_id: Mapped[int] = mapped_column(index=True)
    platform: Mapped[str] = mapped_column(String(100))
    platform_user_id: Mapped[str] = mapped_column(String(100))
    platform_post_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    platform_post_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    published_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    content_snapshot: Mapped[dict] = mapped_column(JSON)
    source_fingerprint: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class PostMetrics(MemoryBase):
    __tablename__ = "post_metrics"
    __table_args__ = (UniqueConstraint("user_id", "idempotency_key", name="uq_memory_metrics_request"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    post_id: Mapped[int] = mapped_column(ForeignKey("published_posts.id"), index=True)
    user_id: Mapped[int] = mapped_column(index=True)
    idempotency_key: Mapped[str] = mapped_column(String(100))
    metrics: Mapped[dict] = mapped_column(JSON)
    metrics_raw: Mapped[dict] = mapped_column(JSON, default=dict)
    precision: Mapped[dict] = mapped_column(JSON, default=dict)
    source: Mapped[str] = mapped_column(String(30))
    observed_at: Mapped[datetime] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
