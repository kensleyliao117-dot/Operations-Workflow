from datetime import datetime
from sqlalchemy import String, ForeignKey, Text, DateTime, func, JSON, Boolean, false
from sqlalchemy.orm import Mapped, mapped_column,relationship
from database import Base

from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from .user import Users
    from .content import Content
    from .review import Review
    from .publish_record import PublishRecord

#创建任务表
class Task(Base):
  
    __tablename__ = "tasks"

    id:Mapped[int]=mapped_column(primary_key=True)
    user_id:Mapped[int]=mapped_column(ForeignKey("users.id"))
    # 历史任务保留空值，新任务必须关联当前用户所属的产品。
    product_id: Mapped[int | None] = mapped_column(ForeignKey("products.id"), nullable=True, index=True)
    platform_name:Mapped[str | None]=mapped_column(String(100),nullable=True)
    task_type:Mapped[str]=mapped_column(String(100))
    input_text:Mapped[str]=mapped_column(Text)
    status:Mapped[str]=mapped_column(String(100))
    # 兼容旧版移除记录，并标记待重试的彻底删除流程。
    is_deleted: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())
    # checkpoint 只保存可 JSON 序列化的进度，例如 content_id、当前步骤。
    checkpoint: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    failed_from_status: Mapped[str | None] = mapped_column(String(100), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    # 数据库自动记录创建时间
    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        server_default=func.now()
    )

    # 创建时有时间，后续更新时自动更新时间
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        server_default=func.now(),
        onupdate=func.now()
    )
    user:Mapped["Users"]=relationship(back_populates="tasks")
    contents: Mapped[list["Content"]] = relationship(back_populates="task")
    reviews: Mapped[list["Review"]] = relationship(back_populates="task")
    publish_records: Mapped[list["PublishRecord"]] = relationship(back_populates="task")
