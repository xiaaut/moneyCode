"""股票主体与自选指标。

数据来源合并：state-owned.json（控制人/板块）、msci.json（行业/估值）、
portfolio.json（持仓内标的）、watchlist.json（自选指标）。
"""

from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import Boolean, DateTime, Numeric, String, func, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class Stock(Base):
    """股票/ETF 主数据表，code 即业务主键。"""

    __tablename__ = "stocks"

    code: Mapped[str] = mapped_column(
        String(8), primary_key=True, comment="证券代码，如 600519 / 510300"
    )
    name: Mapped[str] = mapped_column(String(64), nullable=False, index=True, comment="证券名称")

    industry: Mapped[str | None] = mapped_column(String(128), index=True, comment="所属行业")
    board: Mapped[str | None] = mapped_column(String(16), comment="板块：沪主板/深主板/北交所")
    controller: Mapped[str | None] = mapped_column(String(128), comment="实际控制人")
    controller_category: Mapped[str | None] = mapped_column(
        String(32), comment="控制人类型：person/sasac-central/sasac-local/none..."
    )

    # ---------- 关键指标 ----------
    pe_ttm: Mapped[Decimal | None] = mapped_column(Numeric(14, 4), comment="滚动市盈率")
    pb: Mapped[Decimal | None] = mapped_column(Numeric(14, 4), comment="市净率")
    roe: Mapped[Decimal | None] = mapped_column(Numeric(10, 4), comment="净资产收益率(%)")
    eps_current: Mapped[Decimal | None] = mapped_column(Numeric(12, 4), comment="当前 EPS")
    eps_forecast: Mapped[Decimal | None] = mapped_column(
        Numeric(12, 4), comment="预测 EPS（3 年后）"
    )
    price: Mapped[Decimal | None] = mapped_column(Numeric(12, 4), comment="最新价")
    change_pct: Mapped[Decimal | None] = mapped_column(Numeric(10, 4), comment="涨跌幅(%)")
    market_cap_yi: Mapped[Decimal | None] = mapped_column(
        Numeric(18, 4), comment="总市值（亿元）"
    )

    is_watchlist: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false"), comment="是否在自选"
    )
    in_msci: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default=text("false"),
        index=True,
        comment="是否属于 MSCI 成份股（来自 msci.json）",
    )
    extra: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
        server_default=text("'{}'::jsonb"),
        comment="未结构化指标与原始出处",
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), comment="创建时间"
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
        comment="更新时间",
    )
