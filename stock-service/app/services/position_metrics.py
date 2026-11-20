"""持仓派生指标统一计算。

目前只有一项：仓位占比 `positions.position_ratio`。

口径（与 `GET /api/portfolio` 输出、前端 `recalcAllPositionRatios` 完全一致，全链路唯一）：
    单只市值 = ROUND(shares × current_price, 2)
    仓位占比 = 单只市值 ÷ Σ单只市值 × 100   （保留 4 位小数为落库精度，接口再格式化成 2 位字符串）

谁需要调用：
- `PATCH /api/portfolio/{code}`、`POST /api/portfolio`（整表覆盖）、`POST /api/portfolio/positions`（新增/改单）
- `daily_sync._step_positions`（收盘刷新现价后）

调用前置条件：本会话内对 shares / current_price / market_value 的修改必须已 flush，
否则 SQL 里的 ROUND(shares × current_price, 2) 读到的还是旧值。
"""

from __future__ import annotations

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Position

# 单只市值表达式：与 _position_to_out() 里的 round(price * qty, 2) 同一个口径。
# coalesce 兜底：current_price 为空的脏行按 0 市值处理，避免把 position_ratio 写成 NULL。
_MARKET_VALUE_EXPR = func.coalesce(func.round(Position.shares * Position.current_price, 2), 0)


async def recalc_position_ratios(db: AsyncSession) -> None:
    """按市值口径重算**全表** position_ratio（派生快照，不触碰任何行的 updated_at）。"""
    total = await db.scalar(select(func.coalesce(func.sum(_MARKET_VALUE_EXPR), 0)))
    total_value = float(total or 0)
    await db.execute(
        update(Position)
        .values(
            position_ratio=func.round(
                _MARKET_VALUE_EXPR * 100 / func.nullif(total_value, 0), 4
            )
        )
        .execution_options(synchronize_session=False)
    )
