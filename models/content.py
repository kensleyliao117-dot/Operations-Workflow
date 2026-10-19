from datetime import datetime
from sqlalchemy import String, ForeignKey, Text, DateTime, func, JSON, text
from sqlalchemy.orm import Mapped, mapped_column,relationship
from database import Base

from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from .task import Task
    from .review import Review
    from .publish_record import PublishRecord
    
#创建内容表
class Content(Base):
  
    __tablename__ = "contents"
    id:Mapped[int]=mapped_column(primary_key=True)
    task_id:Mapped[int]=mapped_column(ForeignKey("tasks.id"))
    title:Mapped[str]=mapped_column(String(300))
    body:Mapped[str]=mapped_column(Text)
    tags: Mapped[list[str]] = mapped_column(
        JSON, default=list, server_default=text("'[]'"), nullable=False,
    )
    # 工作流重放时复用已提交结果，避免同一生成步骤重复创建内容。
    generation_key: Mapped[str | None] = mapped_column(String(100), nullable=True, unique=True)
    generation_metadata: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    # 轻量图片关联：保存永久存储 key、可选访问 URL、生成 Prompt。
    # 每个内容版本保存独立列表；图片二进制由 storage 管理。
    image_assets: Mapped[list[dict]] = mapped_column(
        JSON, default=list, server_default=text("'[]'"), nullable=False,
    )
    # 生图 Prompt，可选
    image_prompt: Mapped[str | None] = mapped_column(
        Text,
        nullable=True
    )

    
    video_script: Mapped[str | None] = mapped_column(
        Text,
        nullable=True
    )
    version:Mapped[int] = mapped_column(
        default=1
    )
    created_at:Mapped[datetime] = mapped_column(
        DateTime,
        server_default=func.now()
    )
    task:Mapped["Task"]=relationship(back_populates="contents")
    reviews: Mapped[list["Review"]] = relationship(back_populates="content")  
    publish_records: Mapped[list["PublishRecord"]] = relationship(back_populates="content")  
