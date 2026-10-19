from sqlalchemy import String, JSON, text
from sqlalchemy.orm import Mapped, mapped_column,relationship
from database import Base
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from .task import Task
    from .review import Review

#创建用户表
class Users(Base):
  
    __tablename__ = "users"
    id:Mapped[int]=mapped_column(primary_key=True)
    username: Mapped[str | None] = mapped_column(String(100), unique=True, nullable=True)
    password_hash: Mapped[str | None] = mapped_column(String(500), nullable=True)
    is_active: Mapped[bool] = mapped_column(default=True, server_default="true")
    is_admin: Mapped[bool] = mapped_column(default=False, server_default="false")
    platform_rule_overrides: Mapped[dict] = mapped_column(JSON, default=dict, server_default=text("'{}'"))
    hidden_system_models: Mapped[list[str]] = mapped_column(JSON, default=list, server_default=text("'[]'"))
    email: Mapped[str | None] = mapped_column(
        String(200),
        unique=True,
        nullable=True
    )
    tasks: Mapped[list["Task"]] = relationship(back_populates="user")
    reviews: Mapped[list["Review"]] = relationship(back_populates="user")
   
