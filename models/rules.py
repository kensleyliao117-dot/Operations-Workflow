"""平台规则、用户自定义规则与版本化内容检查记录。"""
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, JSON, String, Text, func, text
from sqlalchemy.orm import Mapped, mapped_column

from database import Base


class RuleFields:
    content: Mapped[str | None] = mapped_column(Text, nullable=True)
    rule_type: Mapped[str] = mapped_column(String(20), default="ai", server_default="ai")
    keywords: Mapped[list[str]] = mapped_column(JSON, default=list, server_default=text("'[]'"))
    max_length: Mapped[int | None] = mapped_column(nullable=True)
    targets: Mapped[list[str]] = mapped_column(JSON, default=lambda: ["title", "body", "tags", "image_prompt", "video_script"],
        server_default=text("'[\"title\",\"body\",\"tags\",\"image_prompt\",\"video_script\"]'"))
    severity: Mapped[str] = mapped_column(String(20), default="medium", server_default="medium")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default=text("true"))
    version: Mapped[int] = mapped_column(default=1, server_default=text("1"))
    source_url: Mapped[str | None] = mapped_column(String(2048), nullable=True)


class CustomRule(RuleFields, Base):
    __tablename__ = "custom_rules"
    id: Mapped[int] = mapped_column(primary_key=True)
    is_deleted: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"))
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    platform_name: Mapped[str | None] = mapped_column(String(100), nullable=True)


class PlatformRule(RuleFields, Base):
    __tablename__ = "platform_rules"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True, index=True)
    platform_name: Mapped[str] = mapped_column(String(100), nullable=False)


class ContentRuleCheck(Base):
    __tablename__ = "content_rule_checks"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    task_id: Mapped[int] = mapped_column(ForeignKey("tasks.id"), index=True)
    content_id: Mapped[int] = mapped_column(ForeignKey("contents.id"), index=True)
    content_version: Mapped[int]
    platform_name: Mapped[str] = mapped_column(String(100))
    content_fingerprint: Mapped[str] = mapped_column(String(64))
    rules_fingerprint: Mapped[str] = mapped_column(String(64))
    rules_snapshot: Mapped[list[dict]] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(30))
    risks: Mapped[list[dict]] = mapped_column(JSON)
    coverage_warnings: Mapped[list[str]] = mapped_column(JSON)
    model: Mapped[str] = mapped_column(String(100))
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())



