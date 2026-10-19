"""项目用户登记的平台账号，独立于本地 Chrome 采集会话。"""
from sqlalchemy import ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from database import Base


class PlatformAccount(Base):
    __tablename__ = "platform_accounts"
    __table_args__ = (UniqueConstraint("user_id", "platform", "platform_user_id", name="uq_platform_account_owner"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    platform: Mapped[str] = mapped_column(String(100))
    platform_user_id: Mapped[str] = mapped_column(String(100))
    display_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    is_active: Mapped[bool] = mapped_column(default=True, server_default="true")
