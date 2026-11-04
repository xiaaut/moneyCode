"""v1 路由聚合。"""

from fastapi import APIRouter

from app.api.v1.endpoints import grid, portfolio, quote, stocks

api_router = APIRouter()
api_router.include_router(portfolio.router)
api_router.include_router(stocks.router)
api_router.include_router(grid.router)
api_router.include_router(quote.router)

__all__ = ["api_router"]
