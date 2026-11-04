"""股票池筛选的请求 / 响应模型。"""

from pydantic import Field

from app.schemas.common import CamelModel


class StockOut(CamelModel):
    code: str
    name: str
    industry: str | None = None
    board: str | None = None
    controller: str | None = None
    controller_category: str | None = None

    pe_ttm: float | None = None
    pb: float | None = None
    roe: float | None = None
    eps_current: float | None = None
    eps_forecast: float | None = None
    price: float | None = None
    change_pct: float | None = None
    market_cap_yi: float | None = None

    is_watchlist: bool = False
    in_msci: bool = False


class StockListResponse(CamelModel):
    total: int = 0
    page: int = 1
    page_size: int = 50
    items: list[StockOut] = Field(default_factory=list)


class StockFilterOptions(CamelModel):
    """筛选项下拉数据。"""

    industries: list[str] = Field(default_factory=list)
    controller_categories: list[dict[str, str]] = Field(default_factory=list)
    boards: list[str] = Field(default_factory=list)
    total: int = 0
