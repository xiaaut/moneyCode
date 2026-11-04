"""自选股观察列表。

前端 WatchList.vue / Strategy.vue 使用的完整指标快照，独立成表而不是塞在 stocks.extra，
因为「加入观察列表」保存的是当时的策略计算结果（g / PEG / 年化收益 / 满足条件明细）。
"""

from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import BigInteger, Boolean, DateTime, Integer, Numeric, String, Text, func, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class WatchlistItem(Base):
    """观察列表项（对应原 watchlist.json 的一条记录）。"""

    __tablename__ = "watchlist_items"

    id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, comment="前端生成的毫秒时间戳 ID，用于删除定位"
    )
    stock_code: Mapped[str] = mapped_column(String(8), nullable=False, index=True, comment="股票代码")
    stock_name: Mapped[str | None] = mapped_column(String(64), comment="股票名称")

    current_eps: Mapped[Decimal | None] = mapped_column(Numeric(12, 4), comment="当前 EPS")
    target_eps: Mapped[Decimal | None] = mapped_column(Numeric(12, 4), comment="目标 EPS")
    current_pe: Mapped[Decimal | None] = mapped_column(Numeric(12, 4), comment="当前 PE")
    historical_avg_pe: Mapped[Decimal | None] = mapped_column(Numeric(12, 4), comment="历史平均 PE")
    mean_pe: Mapped[Decimal | None] = mapped_column(Numeric(12, 4), comment="均值 PE")
    hold_years: Mapped[int | None] = mapped_column(Integer, comment="持有年数")

    g_percent: Mapped[Decimal | None] = mapped_column(Numeric(10, 4), comment="盈利增速 g(%)")
    peg: Mapped[Decimal | None] = mapped_column(Numeric(10, 4), comment="PEG")
    annualized_return_percent: Mapped[Decimal | None] = mapped_column(
        Numeric(10, 4), comment="预期年化收益率(%)"
    )

    pass_count: Mapped[int | None] = mapped_column(Integer, comment="满足条件数")
    total_conditions: Mapped[int | None] = mapped_column(Integer, comment="条件总数")
    all_passed: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    details: Mapped[list[Any]] = mapped_column(
        JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb"), comment="条件明细"
    )
    note: Mapped[str | None] = mapped_column(Text, comment="备注")

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
