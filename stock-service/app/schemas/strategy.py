"""选股策略配置的请求 / 响应模型（camelCase 对齐前端 Strategy.vue）。"""

from app.schemas.common import CamelModel

# 与前端 Strategy.vue 的默认值保持一致
DEFAULTS = {
    "stockName": "安琪酵母",
    "stockCode": "600298",
    "currentEps": 0.0,
    "targetEps": 0.0,
    "currentPe": 0.0,
    "historicalAvgPe": 0.0,
    "meanPe": 0.0,
    "holdYears": 3,
    "portfolioCount": 12,
    "maxIndustryCount": 4,
}


class StrategyConfigOut(CamelModel):
    stock_name: str | None = DEFAULTS["stockName"]
    stock_code: str | None = DEFAULTS["stockCode"]
    current_eps: float | None = DEFAULTS["currentEps"]
    target_eps: float | None = DEFAULTS["targetEps"]
    current_pe: float | None = DEFAULTS["currentPe"]
    historical_avg_pe: float | None = DEFAULTS["historicalAvgPe"]
    mean_pe: float | None = DEFAULTS["meanPe"]
    hold_years: float | None = DEFAULTS["holdYears"]
    portfolio_count: int | None = DEFAULTS["portfolioCount"]
    max_industry_count: int | None = DEFAULTS["maxIndustryCount"]
    updated_at: str | None = None


class StrategyConfigIn(CamelModel):
    stock_name: str | None = None
    stock_code: str | None = None
    current_eps: float | None = None
    target_eps: float | None = None
    current_pe: float | None = None
    historical_avg_pe: float | None = None
    mean_pe: float | None = None
    hold_years: float | None = None
    portfolio_count: int | None = None
    max_industry_count: int | None = None
