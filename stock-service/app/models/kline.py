"""全市场复权日线表 stock_kline_daily。

联合主键 (code, trade_date)；数据来自同花顺 market-dumps Parquet（A 股）
与 fund-market/historical（场内 ETF 前复权日线）。
"""

from datetime import date, datetime

from sqlalchemy import Date, DateTime, Index, Numeric, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class StockKlineDaily(Base):
    __tablename__ = "stock_kline_daily"

    code: Mapped[str] = mapped_column(String(20), primary_key=True, comment="证券代码")
    trade_date: Mapped[date] = mapped_column(Date, primary_key=True, comment="交易日")

    open: Mapped[float | None] = mapped_column(Numeric(10, 3), comment="开盘价")
    high: Mapped[float | None] = mapped_column(Numeric(10, 3), comment="最高价")
    low: Mapped[float | None] = mapped_column(Numeric(10, 3), comment="最低价")
    close: Mapped[float | None] = mapped_column(Numeric(10, 3), comment="收盘价")
    pre_close: Mapped[float | None] = mapped_column(Numeric(10, 3), comment="昨收价")

    volume: Mapped[float | None] = mapped_column(Numeric(18, 2), comment="成交量（股）")
    amount: Mapped[float | None] = mapped_column(Numeric(18, 2), comment="成交额（元）")
    turnover_ratio: Mapped[float | None] = mapped_column(Numeric(6, 3), comment="换手率(%)")

    adjust_factor: Mapped[float | None] = mapped_column(Numeric(10, 4), comment="复权因子")
    qfq_close: Mapped[float | None] = mapped_column(Numeric(10, 3), comment="前复权收盘价")

    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
        comment="更新时间",
    )

    __table_args__ = (
        Index("ix_kline_trade_date", "trade_date"),
    )
