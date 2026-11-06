"""选股策略配置（单行单例，id 恒为 1）。

对应前端 Strategy.vue「Davis 双因子选股策略器」的输入参数，跨设备持久化：
    stockName/stockCode、g 计算（currentEps/targetEps）、PEG（currentPe/historicalAvgPe）、
    均值回归（meanPe/holdYears）、组合约束（portfolioCount/maxIndustryCount）。
epsYears 为前端硬编码常量（3 年），不入库。
"""

from datetime import datetime
from decimal import Decimal

from sqlalchemy import DateTime, Integer, Numeric, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class StrategyConfig(Base):
    __tablename__ = "strategy_config"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)

    stock_name: Mapped[str | None] = mapped_column(String(64), comment="股票名称")
    stock_code: Mapped[str | None] = mapped_column(String(16), comment="股票代码")

    current_eps: Mapped[Decimal | None] = mapped_column(Numeric(14, 4), comment="当前 EPS")
    target_eps: Mapped[Decimal | None] = mapped_column(Numeric(14, 4), comment="目标 EPS")
    current_pe: Mapped[Decimal | None] = mapped_column(Numeric(14, 4), comment="当前 PE")
    historical_avg_pe: Mapped[Decimal | None] = mapped_column(Numeric(14, 4), comment="历史均值 PE")
    mean_pe: Mapped[Decimal | None] = mapped_column(Numeric(14, 4), comment="均值 PE")
    hold_years: Mapped[Decimal | None] = mapped_column(Numeric(10, 2), comment="预计持有年数")

    portfolio_count: Mapped[int | None] = mapped_column(Integer, comment="组合股票数量")
    max_industry_count: Mapped[int | None] = mapped_column(Integer, comment="单行业股票数量")

    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
        comment="更新时间",
    )
