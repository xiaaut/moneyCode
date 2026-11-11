"""调仓工单表 rebalance_plans。

action_type:
    BUY_ADD      逢低加仓
    SELL_PROFIT  分批止盈
    SELL_RISK    排雷减仓
    GRID_T       波段做T
status:
    PENDING   待触发
    TRIGGERED 已到价
    DONE      已执行
"""

from datetime import datetime
from decimal import Decimal

from sqlalchemy import DateTime, Integer, Numeric, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class RebalancePlan(Base):
    __tablename__ = "rebalance_plans"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    code: Mapped[str] = mapped_column(String(20), nullable=False, index=True, comment="证券代码")
    name: Mapped[str | None] = mapped_column(String(50), comment="证券名称")
    account_tag: Mapped[str | None] = mapped_column(String(50), comment="账户标签（兼容多账户持仓）")

    action_type: Mapped[str] = mapped_column(
        String(30), nullable=False, comment="BUY_ADD/SELL_PROFIT/SELL_RISK/GRID_T"
    )
    trigger_condition: Mapped[str | None] = mapped_column(String(10), comment="<= 或 >=")
    target_price: Mapped[Decimal | None] = mapped_column(Numeric(10, 3), comment="触发价")
    plan_shares: Mapped[int | None] = mapped_column(Integer, comment="计划股数")
    current_cost: Mapped[Decimal | None] = mapped_column(Numeric(10, 3), comment="当前成本")

    reason_tag: Mapped[str | None] = mapped_column(String(100), comment="信号原因标签")
    notes: Mapped[str | None] = mapped_column(Text, comment="备注")

    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="PENDING", server_default="PENDING",
        comment="PENDING/TRIGGERED/DONE",
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), comment="创建时间"
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now(), comment="更新时间"
    )
