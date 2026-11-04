"""持仓与历史成交流水。

Position        <- stock-web/data/portfolio.json（同一代码多条记录按加权成本合并）
TradeHistory    <- stock-web/data/closed-positions.json（已清仓交易）
"""

from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import BigInteger, Date, DateTime, Integer, Numeric, String, Text, func, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class Position(Base):
    """当前持仓。market_value / profit_amount 为按最新价计算的快照值。"""

    __tablename__ = "positions"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(
        String(8), nullable=False, unique=True, index=True, comment="证券代码"
    )
    name: Mapped[str] = mapped_column(String(64), nullable=False, comment="证券名称")

    shares: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0"), comment="持仓数量(股)"
    )
    cost_price: Mapped[Decimal | None] = mapped_column(Numeric(14, 4), comment="成本价")
    current_price: Mapped[Decimal | None] = mapped_column(Numeric(14, 4), comment="现价")

    market_value: Mapped[Decimal | None] = mapped_column(
        Numeric(18, 2), comment="动态市值 = shares * current_price"
    )
    profit_amount: Mapped[Decimal | None] = mapped_column(
        Numeric(18, 2), comment="浮动盈亏 = market_value - 总成本"
    )
    profit_ratio: Mapped[Decimal | None] = mapped_column(Numeric(10, 4), comment="盈亏比例(%)")

    sector: Mapped[str | None] = mapped_column(String(128), comment="所属行业")
    position_ratio: Mapped[Decimal | None] = mapped_column(
        Numeric(10, 4), comment="占总仓位比例(%)"
    )
    extra: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
        server_default=text("'{}'::jsonb"),
        comment="估值与题材等附加指标",
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class TradeHistory(Base):
    """已清仓/历史买卖交易流水（对应 closed-positions.json）。"""

    __tablename__ = "trade_history"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(String(8), nullable=False, index=True, comment="证券代码")
    name: Mapped[str] = mapped_column(String(64), nullable=False, comment="证券名称")

    quantity: Mapped[int | None] = mapped_column(Integer, comment="成交数量(股)")
    buy_price: Mapped[Decimal | None] = mapped_column(Numeric(14, 4), comment="买入价")
    sell_price: Mapped[Decimal | None] = mapped_column(Numeric(14, 4), comment="卖出价")
    fee: Mapped[Decimal | None] = mapped_column(
        Numeric(14, 2), default=Decimal("0"), server_default=text("0"), comment="手续费合计"
    )

    buy_date: Mapped[date | None] = mapped_column(Date, comment="买入日期")
    sell_date: Mapped[date | None] = mapped_column(Date, index=True, comment="卖出日期")
    hold_days: Mapped[int | None] = mapped_column(Integer, comment="持有天数")

    profit_amount: Mapped[Decimal | None] = mapped_column(Numeric(18, 2), comment="已实现盈亏")
    profit_ratio: Mapped[Decimal | None] = mapped_column(Numeric(10, 4), comment="收益率(%)")
    note: Mapped[str | None] = mapped_column(Text, comment="备注")

    # 迁移幂等键：同一笔交易重复导入不会产生重复行
    source_key: Mapped[str | None] = mapped_column(
        String(80), unique=True, comment="迁移幂等键(sha1)，手工录入时为空"
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
