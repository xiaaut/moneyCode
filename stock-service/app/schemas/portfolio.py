"""持仓 / 自选 / 清仓记录 的请求与响应模型。

字段名与前端 `Positions.vue`、`WatchList.vue`、`ClosedPositions.vue` 完全对齐：
    - 输出：camelCase（totalCost / currentPrice / profitRatio ...）
    - 百分数字段输出字符串（"-39.11%"），数值字段无值时输出 "-"
"""

from datetime import date
from typing import Any

from pydantic import Field, field_validator

from app.schemas.common import CamelModel, parse_number

DASH = "-"


class PositionOut(CamelModel):
    """GET /api/portfolio 单行结构（前端直接喂给 el-table）。"""

    id: int
    code: str
    name: str
    sector: str | None = None

    cost_price: float = 0.0
    quantity: int = 0
    current_price: float = 0.0
    total_cost: float = 0.0
    market_value: float = 0.0
    profit_amount: float = 0.0
    profit_ratio: str = "0.00%"
    position_ratio: str = "0.00%"

    pb: float | None = None
    pe: float | None = None
    roe: str = DASH
    avg_pe: float | str = DASH
    eps: float | str = DASH
    # to_camel 会把 eps3y 变成 eps3Y，这里显式指定别名保持前端字段名
    eps3y: float | str = Field(default=DASH, alias="eps3y")
    g: str = DASH
    peg: str = DASH
    expected_return: str = DASH

    # 最近修改时间（ISO 8601），仅在用户主动新增/修改持仓时更新
    updated_at: str | None = None


class PositionIn(CamelModel):
    """POST /api/portfolio 单行入参（整数组覆盖时逐行校验）。"""

    id: int | None = None
    code: str
    name: str | None = None
    sector: str | None = None

    cost_price: float | str | None = 0
    quantity: int | float | str | None = 0
    current_price: float | str | None = 0

    pb: float | str | None = None
    pe: float | str | None = None
    roe: float | str | None = None
    avg_pe: float | str | None = None
    eps: float | str | None = None
    eps3y: float | str | None = Field(default=None, alias="eps3y")
    g: float | str | None = None
    peg: float | str | None = None
    expected_return: float | str | None = None

    @field_validator("code")
    @classmethod
    def _normalize_code(cls, value: str) -> str:
        code = str(value or "").strip()
        digits = "".join(ch for ch in code if ch.isdigit())
        return digits[-6:] if len(digits) >= 6 else code


class WatchlistItemOut(CamelModel):
    """GET /api/watchlist 单项结构。"""

    id: int
    stock_code: str
    stock_name: str | None = None
    created_at: str | None = None

    current_eps: float | str = DASH
    target_eps: float | str = DASH
    current_pe: float | str = DASH
    historical_avg_pe: float | str = DASH
    mean_pe: float | str = DASH
    hold_years: int | None = None

    g_percent: float | str = DASH
    peg: float | str = DASH
    annualized_return_percent: float | str = DASH

    pass_count: int | None = None
    total_conditions: int | None = None
    all_passed: bool = False
    details: list[Any] = Field(default_factory=list)


class WatchlistItemIn(CamelModel):
    id: int | None = None
    stock_code: str | None = Field(default=None, alias="stockCode")
    stock_name: str | None = Field(default=None, alias="stockName")
    created_at: str | None = Field(default=None, alias="createdAt")

    current_eps: float | str | None = None
    target_eps: float | str | None = None
    current_pe: float | str | None = None
    historical_avg_pe: float | str | None = None
    mean_pe: float | str | None = None
    hold_years: int | float | str | None = None

    g_percent: float | str | None = None
    peg: float | str | None = None
    annualized_return_percent: float | str | None = None

    pass_count: int | float | str | None = None
    total_conditions: int | float | str | None = None
    all_passed: bool | None = None
    details: list[Any] = Field(default_factory=list)


class TradeHistoryOut(CamelModel):
    """GET /api/closed-positions 单行结构。"""

    id: int
    code: str
    name: str
    quantity: int | None = None
    buy_price: float | None = None
    sell_price: float | None = None
    fee: float | None = 0.0
    buy_date: str | None = None
    sell_date: str | None = None
    profit_amount: float | None = None
    profit_ratio: float | None = None
    hold_days: int | None = None
    note: str | None = None
    created_at: str | None = None


class TradeHistoryIn(CamelModel):
    id: int | None = None
    code: str
    name: str | None = None
    quantity: int | float | str | None = None
    buy_price: float | str | None = None
    sell_price: float | str | None = None
    fee: float | str | None = 0
    buy_date: str | None = None
    sell_date: str | None = None
    profit_amount: float | str | None = None
    profit_ratio: float | str | None = None
    hold_days: int | float | str | None = None
    note: str | None = None
    created_at: str | None = None

    @field_validator("code")
    @classmethod
    def _normalize_code(cls, value: str) -> str:
        code = str(value or "").strip()
        digits = "".join(ch for ch in code if ch.isdigit())
        return digits[-6:] if len(digits) >= 6 else code

    def resolved_profit(self) -> float | None:
        """前端没传 profitAmount 时按 (卖-买)*数量-费用 推算。"""
        profit = parse_number(self.profit_amount)
        if profit is not None:
            return float(profit)
        buy, sell, qty, fee = (
            parse_number(self.buy_price),
            parse_number(self.sell_price),
            parse_number(self.quantity),
            parse_number(self.fee) or 0,
        )
        if buy is None or sell is None or qty is None:
            return None
        return float((sell - buy) * qty - fee)


class ClosedPositionCreate(CamelModel):
    """DELETE /api/portfolio/{code} 的归档参数。"""

    sell_price: float | str | None = None
    fee: float | str | None = 0
    sell_date: date | None = None
    note: str | None = None


__all__ = [
    "PositionOut",
    "PositionIn",
    "WatchlistItemOut",
    "WatchlistItemIn",
    "TradeHistoryOut",
    "TradeHistoryIn",
    "ClosedPositionCreate",
]
