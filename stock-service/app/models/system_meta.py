"""系统元信息（单行单例，id 恒为 1）。

持久化「收盘一键更新」等全局状态，避免进程重启后内存态丢失。
"""

from datetime import datetime

from sqlalchemy import DateTime, Integer, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class SystemMeta(Base):
    __tablename__ = "system_meta"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)

    last_daily_sync_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), comment="最近一次收盘一键更新完成时间"
    )

    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
        comment="更新时间",
    )
