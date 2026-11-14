"""网格策略与档位成交的请求 / 响应模型（对齐 GridTrading.vue）。"""

from typing import Literal

from pydantic import Field, field_validator

from app.schemas.common import CamelModel


class GridCycleOut(CamelModel):
    """配对回合（一开一平）。"""

    id: str
    grid_level: int | None = None
    cycle_type: str = "LONG_FIRST"      # LONG_FIRST / SHORT_FIRST
    status: str = "HOLDING_LONG"        # HOLDING_LONG / WAITING_REBUY / CLOSED

    open_trade_id: str | None = None
    open_direction: str = "BUY"
    open_price: float = 0.0
    open_shares: int = 0
    open_fee: float = 0.0
    open_time: str = ""

    target_close_price: float | None = None

    close_trade_id: str | None = None
    close_price: float | None = None
    close_shares: int | None = None
    close_fee: float | None = None
    close_time: str | None = None

    net_profit: float | None = None


class GridTradeOut(CamelModel):
    id: str
    timestamp: str = ""
    type: Literal["buy", "sell"] = "buy"
    price: float = 0.0
    shares: int = 0
    fee: float = 0.0
    realized_profit: float = 0.0
    level_index: int | None = None
    pair_cycle_id: str | None = None


class GridTradeIn(CamelModel):
    id: str | None = None
    timestamp: str | None = ""
    type: Literal["buy", "sell"] = "buy"
    price: float | str | None = 0
    shares: int | float | str | None = 0
    fee: float | str | None = 0
    realized_profit: float | str | None = 0
    level_index: int | float | str | None = None
    pair_cycle_id: str | None = None


class GridStrategyIn(CamelModel):
    id: str | None = None
    symbol: str | None = None
    name: str | None = None

    base_price: float | str | None = 0
    upper_price: float | str | None = 0
    lower_price: float | str | None = 0
    mode: str = "geometric"
    grid_count: int | float | str | None = 10
    shares_per_grid: int | float | str | None = 100
    initial_shares: int | float | str | None = 0
    initial_cost: float | str | None = 0

    commission_rate: float | str | None = 0.0001
    stamp_duty_rate: float | str | None = 0
    min_commission: float | str | None = 0.2
    free_five: bool | None = False
    gen_mode: str = "step"
    step_type: str = "percent"
    step_input: float | str | None = 0
    grids_up: int | float | str | None = 0
    grids_down: int | float | str | None = 0
    last_price: float | str | None = 0
    note: str | None = ""

    trades: list[GridTradeIn] = Field(default_factory=list)

    @field_validator("symbol")
    @classmethod
    def _normalize_symbol(cls, value: str | None) -> str | None:
        if value is None:
            return None
        code = "".join(ch for ch in str(value).strip() if ch.isdigit())
        return code[-6:] if len(code) >= 6 else code


class GridStrategyOut(CamelModel):
    id: str
    symbol: str
    name: str | None = None

    base_price: float = 0.0
    upper_price: float = 0.0
    lower_price: float = 0.0
    mode: str = "arithmetic"
    grid_count: int = 10
    shares_per_grid: int = 100
    initial_shares: int = 0
    initial_cost: float = 0.0

    commission_rate: float = 0.0001
    stamp_duty_rate: float = 0.0
    min_commission: float = 0.2
    free_five: bool = False
    gen_mode: str = "step"
    step_type: str = "percent"
    step_input: float | None = None
    grids_up: int = 0
    grids_down: int = 0
    last_price: float = 0.0
    note: str | None = ""

    trades: list[GridTradeOut] = Field(default_factory=list)
    cycles: list[GridCycleOut] = Field(default_factory=list)
    created_at: str | None = None
    updated_at: str | None = None

    # 服务端推导字段（前端不依赖，但便于接口自证）
    current_shares: int = 0
    diluted_cost: float | None = None
    realized_profit: float = 0.0
    step_value: float | None = None
    step_pct: float | None = None
    grid_amount: float | None = None


class GridSaveResponse(CamelModel):
    ok: bool = True
    count: int = 0
    data: list[GridStrategyOut] = Field(default_factory=list)
    message: str = ""
