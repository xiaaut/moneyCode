"""资金流向与龙虎榜模型。

股票代码统一 6 位数字，与 stocks.code 格式对齐。
"""

from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import Date, DateTime, Numeric, String, func, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class StockFundFlowDaily(Base):
    """个股资金流向日表（联合主键 code + trade_date）。"""

    __tablename__ = "stock_fund_flow_daily"

    code: Mapped[str] = mapped_column(String(20), primary_key=True, comment="证券代码（6 位）")
    trade_date: Mapped[date] = mapped_column(Date, primary_key=True, comment="交易日")

    close_price: Mapped[Decimal | None] = mapped_column(Numeric(10, 2), comment="收盘价")
    change_pct: Mapped[Decimal | None] = mapped_column(Numeric(6, 2), comment="涨跌幅(%)")
    main_net_inflow: Mapped[Decimal | None] = mapped_column(Numeric(18, 2), comment="主力净流入额")
    main_net_ratio: Mapped[Decimal | None] = mapped_column(Numeric(6, 2), comment="主力净占比(%)")
    super_net_inflow: Mapped[Decimal | None] = mapped_column(Numeric(18, 2), comment="超大单净额")
    large_net_inflow: Mapped[Decimal | None] = mapped_column(Numeric(18, 2), comment="大单净额")
    medium_net_inflow: Mapped[Decimal | None] = mapped_column(Numeric(18, 2), comment="中单净额")
    small_net_inflow: Mapped[Decimal | None] = mapped_column(Numeric(18, 2), comment="小单净额")

    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
        comment="更新时间",
    )


class StockLhbDaily(Base):
    """龙虎榜每日明细表（联合主键 code + trade_date + reason）。"""

    __tablename__ = "stock_lhb_daily"

    code: Mapped[str] = mapped_column(String(20), primary_key=True, comment="证券代码（6 位）")
    trade_date: Mapped[date] = mapped_column(Date, primary_key=True, comment="上榜日")
    reason: Mapped[str] = mapped_column(String(120), primary_key=True, comment="上榜原因")

    stock_name: Mapped[str | None] = mapped_column(String(50), comment="股票名称")
    lhb_net_buy: Mapped[Decimal | None] = mapped_column(Numeric(18, 2), comment="龙虎榜净买额")
    lhb_buy_amount: Mapped[Decimal | None] = mapped_column(Numeric(18, 2), comment="龙虎榜买入额")
    lhb_sell_amount: Mapped[Decimal | None] = mapped_column(Numeric(18, 2), comment="龙虎榜卖出额")
    turnover_ratio: Mapped[Decimal | None] = mapped_column(Numeric(6, 2), comment="成交占比(%)")
    inst_net_buy: Mapped[Decimal | None] = mapped_column(Numeric(18, 2), comment="机构席位净买额")

    seat_details: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
        server_default=text("'{}'::jsonb"),
        comment="买卖前五营业部名称/买入额/卖出额/净额/机构·北向标签",
    )

    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
        comment="更新时间",
    )
