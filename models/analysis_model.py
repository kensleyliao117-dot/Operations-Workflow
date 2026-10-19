"""用户独立的文本模型连接配置及加密凭据。"""
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, Text, UniqueConstraint, Index, func, text
from sqlalchemy.orm import Mapped, mapped_column

from database import Base


class UserAnalysisModel(Base):
    __tablename__ = "user_analysis_models"
    __table_args__ = (UniqueConstraint("user_id", "name", name="uq_analysis_models_owner_name"),
        Index("uq_analysis_models_default_owner", "user_id", unique=True,
              postgresql_where=text("is_default"), sqlite_where=text("is_default = 1")))

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    name: Mapped[str] = mapped_column(String(100))
    provider: Mapped[str] = mapped_column(String(100))
    model: Mapped[str] = mapped_column(String(100))
    is_active: Mapped[bool] = mapped_column(default=True)
    base_url: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    encrypted_api_key: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_default: Mapped[bool] = mapped_column(default=False, server_default=text("false"))
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

