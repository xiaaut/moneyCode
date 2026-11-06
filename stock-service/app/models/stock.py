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

    # ---------- 全市场行情（AKShare stock_zh_a_spot_em）----------
    volume: Mapped[Decimal | None] = mapped_column(Numeric(20, 4), comment="成交量（手）")
    turnover_amount: Mapped[Decimal | None] = mapped_column(Numeric(20, 4), comment="成交额（元）")
    turnover_rate: Mapped[Decimal | None] = mapped_column(Numeric(10, 4), comment="换手率(%)")
    circ_mv: Mapped[Decimal | None] = mapped_column(Numeric(20, 4), comment="流通市值（元）")
    change_60d: Mapped[Decimal | None] = mapped_column(Numeric(10, 4), comment="60 日涨跌幅(%)")
    change_ytd: Mapped[Decimal | None] = mapped_column(Numeric(10, 4), comment="年初至今涨跌幅(%)")

    # ---------- 最新财报业绩（AKShare stock_yjbb_em）----------
    revenue: Mapped[Decimal | None] = mapped_column(Numeric(20, 4), comment="营业总收入（元）")
    revenue_yoy: Mapped[Decimal | None] = mapped_column(Numeric(12, 4), comment="营收同比增长(%)")
    net_profit: Mapped[Decimal | None] = mapped_column(Numeric(20, 4), comment="净利润（元）")
    net_profit_yoy: Mapped[Decimal | None] = mapped_column(Numeric(12, 4), comment="净利润同比增长(%)")
    gross_margin: Mapped[Decimal | None] = mapped_column(Numeric(10, 4), comment="销售毛利率(%)")
    report_period: Mapped[str | None] = mapped_column(String(16), comment="最新财报期，如 2026Q3")

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
    state_owned: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default=text("false"),
        index=True,
        comment="是否国企/政府控股（controller_category ∈ sasac/gov/finance）",
    )
    # MSCI 成份股导入时保留的原始列值（如「权重」「序号」），用于 GET /api/msci 原样回显
    msci_extra: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
        server_default=text("'{}'::jsonb"),
        comment="MSCI 导入行原始列值",
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
