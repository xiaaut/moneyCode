"""网格策略与档位成交记录（对应 grid-strategies.json）。

JSON 中 trades 内嵌在策略对象里，这里拆成独立表，便于按档位做统计。
"""

from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class GridStrategy(Base):
    """网格策略主表。"""

    __tablename__ = "grid_strategies"

    id: Mapped[str] = mapped_column(String(40), primary_key=True, comment="策略 ID，如 grid_1793558185068")
    code: Mapped[str] = mapped_column(String(8), nullable=False, index=True, comment="标的代码")
    name: Mapped[str | None] = mapped_column(String(64), comment="标的名称")

    base_price: Mapped[Decimal | None] = mapped_column(Numeric(14, 4), comment="基准价")
    upper_price: Mapped[Decimal | None] = mapped_column(Numeric(14, 4), comment="网格上限")
    lower_price: Mapped[Decimal | None] = mapped_column(Numeric(14, 4), comment="网格下限")

    mode: Mapped[str] = mapped_column(
        String(16), nullable=False, default="arithmetic", server_default=text("'arithmetic'"),
        comment="网格模式：arithmetic 等差 / geometric 等比",
    )
    grid_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=10, server_default=text("10"), comment="档位数量"
    )
    step_value: Mapped[Decimal | None] = mapped_column(
        Numeric(14, 4), comment="等差模式每档价差(元)"
    )
    step_pct: Mapped[Decimal | None] = mapped_column(
        Numeric(10, 4), comment="等比模式每档比例(%)"
    )
    grid_amount: Mapped[Decimal | None] = mapped_column(Numeric(18, 2), comment="单格资金(元)")

    shares_per_grid: Mapped[int] = mapped_column(
        Integer, nullable=False, default=100, server_default=text("100"), comment="每格股数"
    )
    initial_shares: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0"), comment="初始建仓股数"
    )
    initial_cost: Mapped[Decimal | None] = mapped_column(Numeric(14, 4), comment="初始建仓成本价")

    commission_rate: Mapped[Decimal] = mapped_column(
        Numeric(10, 6), nullable=False, default=Decimal("0.0001"), server_default=text("0.0001"),
        comment="佣金费率",
    )
    stamp_duty_rate: Mapped[Decimal] = mapped_column(
        Numeric(10, 6), nullable=False, default=Decimal("0"), server_default=text("0"),
        comment="印花税率",
    )
    free_five: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false"), comment="免五"
    )

    last_price: Mapped[Decimal | None] = mapped_column(Numeric(14, 4), comment="最新价")
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="active", server_default=text("'active'"),
        comment="运行状态：active / paused",
    )

    # 由成交记录滚动推导
    current_shares: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0"), comment="当前持仓股数"
    )
    diluted_cost: Mapped[Decimal | None] = mapped_column(
        Numeric(14, 4), comment="动态摊薄成本 =（初始金额+买入含费-卖出净回款）/当前股数"
    )
    realized_profit: Mapped[Decimal | None] = mapped_column(
        Numeric(18, 2), comment="网格累计套利(已实现)"
    )

    note: Mapped[str | None] = mapped_column(Text, comment="备注")

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    trades: Mapped[list["GridTradeRecord"]] = relationship(
        back_populates="strategy",
        cascade="all, delete-orphan",
        lazy="selectin",
        order_by="GridTradeRecord.executed_at",
    )


class GridTradeRecord(Base):
    """单个档位触发的买入/卖出成交记录。"""

    __tablename__ = "grid_trade_records"

    id: Mapped[str] = mapped_column(String(40), primary_key=True, comment="成交 ID，如 t_1793558185068")
    strategy_id: Mapped[str] = mapped_column(
        String(40),
        ForeignKey("grid_strategies.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        comment="所属策略",
    )
    code: Mapped[str] = mapped_column(String(8), nullable=False, index=True, comment="标的代码")

    executed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), comment="成交时间"
    )
    side: Mapped[str] = mapped_column(
        String(8), nullable=False, comment="方向：buy / sell"
    )
    price: Mapped[Decimal | None] = mapped_column(Numeric(14, 4), comment="成交价")
    shares: Mapped[int | None] = mapped_column(Integer, comment="成交股数")
    fee: Mapped[Decimal | None] = mapped_column(
        Numeric(14, 2), default=Decimal("0"), server_default=text("0"), comment="手续费"
    )
    realized_profit: Mapped[Decimal | None] = mapped_column(
        Numeric(18, 2), default=Decimal("0"), server_default=text("0"), comment="本笔已实现盈亏"
    )
    level_index: Mapped[int | None] = mapped_column(Integer, comment="触发档位序号(0 起)")

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    strategy: Mapped[GridStrategy] = relationship(back_populates="trades")
