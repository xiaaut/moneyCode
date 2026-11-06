"""模型包。

统一在此导入全部模型，保证 `Base.metadata` 在 import app.models 后即完整，
init_db / Alembic 无需逐个模块查找。
"""

from app.models.grid import GridStrategy, GridTradeRecord
from app.models.msci_meta import MsciMeta
from app.models.portfolio import Position, TradeHistory
from app.models.stock import Stock
from app.models.strategy import StrategyConfig
from app.models.watchlist import WatchlistItem

__all__ = [
    "Stock",
    "Position",
    "TradeHistory",
    "GridStrategy",
    "GridTradeRecord",
    "WatchlistItem",
    "MsciMeta",
    "StrategyConfig",
]
