from datetime import datetime
from sqlalchemy import String, ForeignKey, Text, DateTime, func
from sqlalchemy.orm import Mapped, mapped_column,relationship
from database import Base

from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from .task import Task
    from .content import Content
    from .user import Users

#创建任务表
class Review(Base):
  
    __tablename__ = "reviews"

    id:Mapped[int]=mapped_column(primary_key=True)
    task_id:Mapped[int]=mapped_column(ForeignKey("tasks.id"))
    content_id:Mapped[int]=mapped_column(ForeignKey("contents.id"))
    reviewer_id:Mapped[int]=mapped_column(ForeignKey("users.id"))
    status:Mapped[str]=mapped_column(String(100))
    comment: Mapped[str | None] = mapped_column(
        Text,
        nullable=True
    )
    created_at:Mapped[datetime] = mapped_column(
        DateTime,
        server_default=func.now()
    )
    task:Mapped["Task"]=relationship(back_populates="reviews")
    content:Mapped["Content"]=relationship(back_populates="reviews")
    user:Mapped["Users"]=relationship(back_populates="reviews")
    