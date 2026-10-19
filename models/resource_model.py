"""账号独立的向量和生图模型连接，凭据使用现有部署密钥加密。"""
from sqlalchemy import Boolean, CheckConstraint, ForeignKey, Index, JSON, String, Text, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column
from database import Base


class UserResourceModel(Base):
    __tablename__ = 'user_resource_models'
    __table_args__ = (
        UniqueConstraint('user_id', 'kind', 'name', name='uq_resource_models_owner_kind_name'),
        Index('uq_resource_models_default', 'user_id', 'kind', unique=True,
              postgresql_where=text('is_default'), sqlite_where=text('is_default = 1')),
        CheckConstraint("kind IN ('embedding','image')", name='ck_resource_model_kind'),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey('users.id'), index=True)
    kind: Mapped[str] = mapped_column(String(20))
    name: Mapped[str] = mapped_column(String(100))
    provider: Mapped[str] = mapped_column(String(100))
    model: Mapped[str] = mapped_column(String(100))
    protocol: Mapped[str] = mapped_column(String(30))
    base_url: Mapped[str] = mapped_column(String(2048))
    encrypted_api_key: Mapped[str] = mapped_column(Text)
    dimensions: Mapped[int | None]
    send_dimensions: Mapped[bool] = mapped_column(Boolean, default=True)
    image_sizes: Mapped[list[str]] = mapped_column(JSON, default=list)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    is_default: Mapped[bool] = mapped_column(Boolean, default=False)
