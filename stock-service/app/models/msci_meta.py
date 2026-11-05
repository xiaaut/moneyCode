"""MSCI 数据集元信息（单行单例，id 恒为 1）。

保存 fileName / importedAt / updatedAt / analyzedAt / columns，供 GET /api/msci 组装回显。
"""

from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, Integer, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class MsciMeta(Base):
    __tablename__ = "msci_meta"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    file_name: Mapped[str | None] = mapped_column(String(255), comment="源文件名")
    imported_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    analyzed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    columns: Mapped[list[Any]] = mapped_column(JSONB, default=list, comment="额外列名")
