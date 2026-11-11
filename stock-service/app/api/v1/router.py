"""v1 路由聚合。"""

from fastapi import APIRouter

from app.api.v1.endpoints import (
    controller,
    financials,
    fund_flow,
    grid,
    kline,
    msci,
    portfolio,
    quote,
    rebalance,
    state_owned,
    stocks,
    strategy,
    system,
)

api_router = APIRouter()
api_router.include_router(portfolio.router)
api_router.include_router(stocks.router)
api_router.include_router(grid.router)
api_router.include_router(quote.router)
api_router.include_router(controller.router)
api_router.include_router(msci.router)
api_router.include_router(state_owned.router)
api_router.include_router(strategy.router)
api_router.include_router(fund_flow.router)
api_router.include_router(system.router)
api_router.include_router(kline.router)
api_router.include_router(financials.router)
api_router.include_router(rebalance.router)

__all__ = ["api_router"]
