from datetime import datetime
from sqlalchemy import Boolean, String, ForeignKey, Text, UniqueConstraint, CheckConstraint, JSON, DateTime, func, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from database import Base


#原始案例表
class RednoteCase(Base):
    __tablename__ = "rednote_cases"
    __table_args__ = (UniqueConstraint("user_id", "link", name="uq_rednote_cases_owner_link"),)
    id:Mapped[int]=mapped_column(primary_key=True)
    is_deleted: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"))
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True, index=True)
    name:Mapped[str]=mapped_column(Text)
    # 采集正文和 AI 分析分别保存，旧案例未采集正文时保留空值。
    body: Mapped[str | None] = mapped_column(Text, nullable=True)
    link:Mapped[str]=mapped_column(Text,nullable=False)
    image_urls: Mapped[list[str]] = mapped_column(JSON, default=list, server_default=text("'[]'"))
    metrics: Mapped[dict] = mapped_column(JSON, default=dict, server_default=text("'{}'"))
    metrics_raw: Mapped[dict] = mapped_column(JSON, default=dict, server_default=text("'{}'"))
    author_platform_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    collected_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    analysis:Mapped[list["RednoteAnalysis"]]=relationship(back_populates="cases")
    
#原始案例表
class RednoteAnalysis(Base):
    __tablename__ = "rednote_analysis"
    __table_args__ = (
        UniqueConstraint("cases_id", "version", name="uq_rednote_analysis_case_version"),
        CheckConstraint("version > 0", name="ck_rednote_analysis_version_positive"),
    )
    id:Mapped[int]=mapped_column(primary_key=True)
    content:Mapped[str]=mapped_column(Text)
    cases_id:Mapped[int]=mapped_column(ForeignKey("rednote_cases.id"))
    result: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    model: Mapped[str | None] = mapped_column(String(100), nullable=True)
    source_fingerprint: Mapped[str | None] = mapped_column(String(64), nullable=True)
    version: Mapped[int] = mapped_column(default=1, server_default=text("1"))
    parent_analysis_id: Mapped[int | None] = mapped_column(ForeignKey("rednote_analysis.id"), nullable=True)
    revision_type: Mapped[str] = mapped_column(String(30), default="manual", server_default="manual")
    provider: Mapped[str | None] = mapped_column(String(100), nullable=True)
    model_profile: Mapped[str | None] = mapped_column(String(100), nullable=True)
    feedback_ids: Mapped[list[int]] = mapped_column(JSON, default=list, server_default=text("'[]'"))
    new_insights: Mapped[list[str]] = mapped_column(JSON, default=list, server_default=text("'[]'"))
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    cases:Mapped["RednoteCase"]=relationship(back_populates="analysis")


class RednoteAnalysisFeedback(Base):
    __tablename__ = "rednote_analysis_feedback"
    id: Mapped[int] = mapped_column(primary_key=True)
    analysis_id: Mapped[int] = mapped_column(ForeignKey("rednote_analysis.id"), index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    content: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
