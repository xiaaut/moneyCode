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

    # AKShare 全市场行情
    volume: float | None = None
    turnover_amount: float | None = None
    turnover_rate: float | None = None
    circ_mv: float | None = None
    change_60d: float | None = None
    change_ytd: float | None = None

    # AKShare 最新财报业绩
    revenue: float | None = None
    revenue_yoy: float | None = None
    net_profit: float | None = None
    net_profit_yoy: float | None = None
    gross_margin: float | None = None
    report_period: str | None = None

    is_watchlist: bool = False
    in_msci: bool = False
    state_owned: bool = False


class StockSummary(CamelModel):
    """全市场统计摘要。"""

    total: int = 0
    up_count: int = 0
    down_count: int = 0
    flat_count: int = 0
    state_owned_count: int = 0
    msci_count: int = 0
    last_sync_at: str | None = None


class StockListResponse(CamelModel):
    total: int = 0
    page: int = 1
    page_size: int = 50
    items: list[StockOut] = Field(default_factory=list)
    industries: list[str] = Field(default_factory=list)
    summary: StockSummary | None = None


class StockFilterOptions(CamelModel):
    """筛选项下拉数据。"""

    industries: list[str] = Field(default_factory=list)
    controller_categories: list[dict[str, str]] = Field(default_factory=list)
    boards: list[str] = Field(default_factory=list)
    total: int = 0
