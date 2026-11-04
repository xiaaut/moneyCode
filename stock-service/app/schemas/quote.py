"""行情相关的响应模型。

`/api/quote` 的 diff 直接透传东方财富原始字段（f2/f3/f12/f14/f23/f115...），
前端 Positions.vue / GridTrading.vue 按这些字段名取值，不能改名。
"""

from typing import Any

from pydantic import Field

from app.schemas.common import CamelModel


class QuoteResponse(CamelModel):
    diff: list[dict[str, Any]] = Field(default_factory=list)
    fetched_at: int = 0
    stale: bool = False
    message: str = ""


class RoeItem(CamelModel):
    roe: float | None = None
    ts: int = 0
    from_cache: bool = False
    error: str | None = None


class RoeResponse(CamelModel):
    items: dict[str, RoeItem] = Field(default_factory=dict)
    fetched_at: int = 0
    message: str = ""


class StockNameItem(CamelModel):
    name: str | None = None
    price: float | None = None
    change_pct: float | None = None
    pe_ttm: float | None = None
    pb: float | None = None
    market_cap_yi: float | None = None
    turnover: float | None = None


class StockNameResponse(CamelModel):
    items: dict[str, StockNameItem] = Field(default_factory=dict)
