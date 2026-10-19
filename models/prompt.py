from datetime import datetime
from sqlalchemy import Boolean, String, ForeignKey, Text, DateTime, func, text
from sqlalchemy.orm import Mapped, mapped_column

from database import Base

#创建提示词表
class Prompt(Base):
  
    __tablename__ = "prompts"

    id:Mapped[int]=mapped_column(primary_key=True)
    is_deleted: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"))
    user_id:Mapped[int]=mapped_column(ForeignKey("users.id"))
    platform:Mapped[str]=mapped_column(String(100))
    name:Mapped[str]=mapped_column(String(100))
    prompt_content:Mapped[str]=mapped_column(Text)
    #当前prompt是否可用，默认可用
    is_active:Mapped[bool]=mapped_column(default=True)
    version:Mapped[int]=mapped_column(default=1)
    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        server_default=func.now()
    )

    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        server_default=func.now(),
        onupdate=func.now()
    )
