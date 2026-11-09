"""网格策略与档位成交接口。

兼容口径：
    GET  /api/grid  -> 策略数组（camelCase，trades 内嵌）
    POST /api/grid  -> 单对象=按 id upsert；数组=整体覆盖（前端删除策略就是这么做的）
    两者都返回 {"ok":true,"count":N,"data":[...]}，前端用 data 里的同 id 对象刷新本地状态。

每次写入都会重算 current_shares / diluted_cost / realized_profit / step_value / step_pct / grid_amount。
"""

from __future__ import annotations

import time
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.models import GridStrategy, GridTradeRecord
from app.schemas.common import OkResponse, parse_number
from app.schemas.grid import GridSaveResponse, GridStrategyIn, GridStrategyOut, GridTradeIn, GridTradeOut
from app.services import grid_backtest as grid_backtest_service

router = APIRouter(tags=["grid"])


class GridDeleteResponse(OkResponse):
    deleted: str = ""


# --------------------------------------------------------------------------- #
# 计算与转换
# --------------------------------------------------------------------------- #
def _dec(value: Any) -> Decimal | None:
    return parse_number(value)


def _flt(value: Any, default: float = 0.0) -> float:
    number = parse_number(value)
    return float(number) if number is not None else default


def _int(value: Any, default: int = 0) -> int:
    number = parse_number(value)
    return int(number) if number is not None else default


def compute_metrics(
    *,
    mode: str,
    base_price: float,
    upper_price: float,
    lower_price: float,
    grid_count: int,
    shares_per_grid: int,
    initial_shares: int,
    initial_cost: float,
    trades: list[dict[str, Any]],
) -> dict[str, Any]:
    """按券商口径滚动推导步长、单格资金、当前持仓、摊薄成本与累计套利。"""
    step_value: float | None = None
    step_pct: float | None = None
    if grid_count > 0 and upper_price > lower_price:
        if mode == "geometric" and lower_price > 0:
            ratio = (upper_price / lower_price) ** (1 / grid_count)
            step_pct = round((ratio - 1) * 100, 4)
        else:
            step_value = round((upper_price - lower_price) / grid_count, 4)

    grid_amount = round(base_price * shares_per_grid, 2) if base_price > 0 else None

    initial_amount = initial_cost * initial_shares
    buy_cash = sell_cash = Decimal("0")
    buy_shares = sell_shares = 0
    realized = Decimal("0")

    for trade in trades:
        price = _dec(trade.get("price")) or Decimal("0")
        shares = _int(trade.get("shares"))
        fee = _dec(trade.get("fee")) or Decimal("0")
        if trade.get("side") == "buy":
            buy_cash += price * shares + fee
            buy_shares += shares
        else:
            sell_cash += price * shares - fee
            sell_shares += shares
            realized += _dec(trade.get("realized_profit")) or Decimal("0")

    current_shares = initial_shares + buy_shares - sell_shares
    net_invested = Decimal(str(initial_amount)) + buy_cash - sell_cash
    diluted_cost = (
        round(float(net_invested / current_shares), 4) if current_shares > 0 else None
    )

    return {
        "step_value": step_value,
        "step_pct": step_pct,
        "grid_amount": grid_amount,
        "current_shares": current_shares,
        "diluted_cost": diluted_cost,
        "realized_profit": Decimal(str(round(float(realized), 2))),
    }


def _parse_trade_time(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if not value:
        return None
    text = str(value).strip().replace("Z", "+00:00")
    for parser in (
        datetime.fromisoformat,
        lambda t: datetime.strptime(t, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc),
        lambda t: datetime.strptime(t, "%Y-%m-%d %H:%M").replace(tzinfo=timezone.utc),
    ):
        try:
            parsed = parser(text)
            return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
        except (ValueError, TypeError):
            continue
    return None


def _strategy_to_out(strategy: GridStrategy, trades: list[GridTradeRecord]) -> GridStrategyOut:
    return GridStrategyOut(
        id=strategy.id,
        symbol=strategy.code,
        name=strategy.name,
        base_price=float(strategy.base_price or 0),
        upper_price=float(strategy.upper_price or 0),
        lower_price=float(strategy.lower_price or 0),
        mode=strategy.mode or "arithmetic",
        grid_count=int(strategy.grid_count or 0),
        shares_per_grid=int(strategy.shares_per_grid or 0),
        initial_shares=int(strategy.initial_shares or 0),
        initial_cost=float(strategy.initial_cost or 0),
        commission_rate=float(strategy.commission_rate or 0),
        stamp_duty_rate=float(strategy.stamp_duty_rate or 0),
        free_five=bool(strategy.free_five),
        last_price=float(strategy.last_price or 0),
        note=strategy.note or "",
        trades=[
            GridTradeOut(
                id=trade.id,
                timestamp=trade.executed_at.strftime("%Y-%m-%d %H:%M:%S") if trade.executed_at else "",
                type=trade.side,  # type: ignore[arg-type]
                price=float(trade.price or 0),
                shares=int(trade.shares or 0),
                fee=float(trade.fee or 0),
                realized_profit=float(trade.realized_profit or 0),
            )
            for trade in sorted(trades, key=lambda item: item.executed_at or datetime.min.replace(tzinfo=timezone.utc))
        ],
        created_at=strategy.created_at.isoformat() if strategy.created_at else None,
        updated_at=strategy.updated_at.isoformat() if strategy.updated_at else None,
        current_shares=int(strategy.current_shares or 0),
        diluted_cost=float(strategy.diluted_cost) if strategy.diluted_cost is not None else None,
        realized_profit=float(strategy.realized_profit or 0),
        step_value=float(strategy.step_value) if strategy.step_value is not None else None,
        step_pct=float(strategy.step_pct) if strategy.step_pct is not None else None,
        grid_amount=float(strategy.grid_amount) if strategy.grid_amount is not None else None,
    )


async def _load_all(db: AsyncSession) -> list[GridStrategyOut]:
    strategies = (
        await db.scalars(select(GridStrategy).order_by(GridStrategy.created_at))
    ).all()
    records = (await db.scalars(select(GridTradeRecord))).all()
    grouped: dict[str, list[GridTradeRecord]] = {}
    for record in records:
        grouped.setdefault(record.strategy_id, []).append(record)
    return [_strategy_to_out(item, grouped.get(item.id, [])) for item in strategies]


async def _load_one(db: AsyncSession, strategy_id: str) -> GridStrategyOut | None:
    strategy = await db.scalar(select(GridStrategy).where(GridStrategy.id == strategy_id))
    if strategy is None:
        return None
    records = (
        await db.scalars(
            select(GridTradeRecord).where(GridTradeRecord.strategy_id == strategy_id)
        )
    ).all()
    return _strategy_to_out(strategy, list(records))


def _trade_rows(strategy_id: str, code: str, payload_trades: list[GridTradeIn]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    base_ms = int(time.time() * 1000)
    for index, trade in enumerate(payload_trades[:500]):
        trade_id = (trade.id or "").strip() or f"t_{base_ms + index}"
        rows.append(
            {
                "id": trade_id[:40],
                "strategy_id": strategy_id[:40],
                "code": code,
                "executed_at": _parse_trade_time(trade.timestamp),
                "side": trade.type,
                "price": _dec(trade.price),
                "shares": _int(trade.shares),
                "fee": _dec(trade.fee) or Decimal("0"),
                "realized_profit": _dec(trade.realized_profit) or Decimal("0"),
                "level_index": None,
            }
        )
    return rows


async def _save_one(db: AsyncSession, payload: GridStrategyIn, *, replace_trades: bool = True) -> str:
    code = "".join(ch for ch in str(payload.symbol or "") if ch.isdigit())[-6:]
    if len(code) != 6:
        raise HTTPException(status_code=400, detail="symbol 必须是 6 位股票代码")

    strategy_id = (payload.id or "").strip() or f"grid_{int(time.time() * 1000)}"
    base_price = _flt(payload.base_price)
    upper_price = _flt(payload.upper_price)
    lower_price = _flt(payload.lower_price)
    grid_count = max(1, min(200, _int(payload.grid_count, 10)))
    shares_per_grid = max(0, _int(payload.shares_per_grid, 100))
    initial_shares = max(0, _int(payload.initial_shares))
    initial_cost = _flt(payload.initial_cost) or base_price
    mode = "geometric" if str(payload.mode).lower() == "geometric" else "arithmetic"

    trade_rows = _trade_rows(strategy_id, code, list(payload.trades))

    metrics = compute_metrics(
        mode=mode,
        base_price=base_price,
        upper_price=upper_price,
        lower_price=lower_price,
        grid_count=grid_count,
        shares_per_grid=shares_per_grid,
        initial_shares=initial_shares,
        initial_cost=initial_cost,
        trades=trade_rows,
    )

    row = {
        "id": strategy_id[:40],
        "code": code,
        "name": (payload.name or "").strip() or code,
        "base_price": _dec(base_price),
        "upper_price": _dec(upper_price),
        "lower_price": _dec(lower_price),
        "mode": mode,
        "grid_count": grid_count,
        "step_value": metrics["step_value"],
        "step_pct": metrics["step_pct"],
        "grid_amount": metrics["grid_amount"],
        "shares_per_grid": shares_per_grid,
        "initial_shares": initial_shares,
        "initial_cost": _dec(initial_cost),
        "commission_rate": _dec(payload.commission_rate) or Decimal("0.0001"),
        "stamp_duty_rate": _dec(payload.stamp_duty_rate) or Decimal("0"),
        "free_five": bool(payload.free_five),
        "last_price": _dec(payload.last_price) or _dec(base_price),
        "current_shares": metrics["current_shares"],
        "diluted_cost": _dec(metrics["diluted_cost"]),
        "realized_profit": metrics["realized_profit"],
        "note": payload.note or "",
    }

    table = GridStrategy.__table__
    statement = pg_insert(table).values(row)
    updatable = [
        c.name
        for c in table.columns
        if c.name not in {"id", "created_at"}
    ]
    statement = statement.on_conflict_do_update(
        index_elements=["id"],
        set_={name: statement.excluded[name] for name in updatable} | {"updated_at": func.now()},
    )
    await db.execute(statement)

    if replace_trades:
        await db.execute(
            delete(GridTradeRecord).where(GridTradeRecord.strategy_id == strategy_id)
        )
    if trade_rows:
        await db.execute(pg_insert(GridTradeRecord.__table__).values(trade_rows))

    return strategy_id


# --------------------------------------------------------------------------- #
# 路由
# --------------------------------------------------------------------------- #
@router.get("/grid", response_model=list[GridStrategyOut], summary="网格策略列表")
async def list_grid(db: AsyncSession = Depends(get_db)) -> list[GridStrategyOut]:
    return await _load_all(db)


@router.post("/grid", response_model=GridSaveResponse, summary="新建/更新网格策略")
async def save_grid(
    payload: GridStrategyIn | list[GridStrategyIn] = Body(...),
    db: AsyncSession = Depends(get_db),
) -> GridSaveResponse:
    if isinstance(payload, list):
        # 整体覆盖：先删掉不在列表里的策略（成交记录随外键级联删除）
        keep_ids = [(item.id or "").strip() for item in payload if (item.id or "").strip()]
        if keep_ids:
            await db.execute(delete(GridStrategy).where(GridStrategy.id.notin_(keep_ids)))
        else:
            await db.execute(delete(GridStrategy))
        saved = [await _save_one(db, item) for item in payload]
    else:
        saved = [await _save_one(db, payload)]

    await db.commit()
    data = await _load_all(db)
    return GridSaveResponse(count=len(saved), data=data)


@router.post(
    "/grid/{strategy_id}/trades", response_model=GridSaveResponse, summary="网格成交打卡"
)
async def add_grid_trade(
    strategy_id: str,
    payload: GridTradeIn,
    db: AsyncSession = Depends(get_db),
) -> GridSaveResponse:
    """新增一笔档位成交，并重算 current_shares / diluted_cost / realized_profit。"""
    strategy = await db.scalar(select(GridStrategy).where(GridStrategy.id == strategy_id))
    if strategy is None:
        raise HTTPException(status_code=404, detail=f"网格策略不存在：{strategy_id}")

    existing = (
        await db.scalars(
            select(GridTradeRecord).where(GridTradeRecord.strategy_id == strategy_id)
        )
    ).all()

    trade_rows = _trade_rows(strategy_id, strategy.code, [payload])
    # 打卡时 realizedProfit 若前端留空，按「卖出价 - 摊薄成本」补算
    if payload.type == "sell" and not _flt(payload.realized_profit):
        price = _flt(payload.price)
        shares = _int(payload.shares)
        fee = _flt(payload.fee)
        basis = float(strategy.diluted_cost or 0)
        if basis > 0 and price > 0 and shares > 0:
            trade_rows[0]["realized_profit"] = Decimal(
                str(round((price - basis) * shares - fee, 2))
            )

    await db.execute(pg_insert(GridTradeRecord.__table__).values(trade_rows))

    all_trades = [
        {
            "side": record.side,
            "price": record.price,
            "shares": record.shares,
            "fee": record.fee,
            "realized_profit": record.realized_profit,
        }
        for record in existing
    ] + trade_rows

    metrics = compute_metrics(
        mode=strategy.mode,
        base_price=float(strategy.base_price or 0),
        upper_price=float(strategy.upper_price or 0),
        lower_price=float(strategy.lower_price or 0),
        grid_count=int(strategy.grid_count or 0),
        shares_per_grid=int(strategy.shares_per_grid or 0),
        initial_shares=int(strategy.initial_shares or 0),
        initial_cost=float(strategy.initial_cost or 0),
        trades=all_trades,
    )
    strategy.current_shares = metrics["current_shares"]
    strategy.diluted_cost = _dec(metrics["diluted_cost"])
    strategy.realized_profit = metrics["realized_profit"]

    await db.commit()
    data = await _load_all(db)
    return GridSaveResponse(count=1, data=data)


@router.delete("/grid/{strategy_id}", response_model=GridDeleteResponse, summary="删除网格策略")
async def delete_grid(strategy_id: str, db: AsyncSession = Depends(get_db)) -> GridDeleteResponse:
    strategy = await db.scalar(select(GridStrategy).where(GridStrategy.id == strategy_id))
    if strategy is None:
        raise HTTPException(status_code=404, detail=f"网格策略不存在：{strategy_id}")
    await db.delete(strategy)
    await db.commit()
    return GridDeleteResponse(deleted=strategy_id, message="策略及其成交记录已删除")


class GridBacktestIn(BaseModel):
    """网格历史回测入参。"""

    code: str
    start_date: str
    end_date: str
    initial_cash: float = Field(100000, gt=0, description="初始现金")
    base_price: float = Field(..., gt=0, description="基准价")
    step_pct: float = Field(1.0, gt=0, description="步长百分比，如 1.0 表示 1%")
    grid_amount: float = Field(10000, gt=0, description="单格金额（元）")
    max_grids: int = Field(10, ge=1, le=100, description="最大网格数")
    commission_rate: float = Field(0.0001, ge=0, description="佣金费率")


@router.post("/grid/backtest", summary="网格历史回测")
async def run_grid_backtest(payload: GridBacktestIn) -> dict:
    code = "".join(ch for ch in payload.code if ch.isdigit())[-6:]
    if len(code) != 6:
        raise HTTPException(status_code=400, detail="code 必须是 6 位股票代码")

    try:
        start = date.fromisoformat(payload.start_date)
        end = date.fromisoformat(payload.end_date)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="日期格式应为 YYYY-MM-DD") from exc
    if start > end:
        raise HTTPException(status_code=400, detail="start_date 不能晚于 end_date")

    klines = await grid_backtest_service.load_klines(code, start, end)
    if not klines:
        raise HTTPException(status_code=404, detail=f"无 K 线数据：{code}，请先同步日线")

    return grid_backtest_service.run_backtest(
        klines,
        {
            "code": code,
            "start_date": payload.start_date,
            "end_date": payload.end_date,
            "initial_cash": payload.initial_cash,
            "base_price": payload.base_price,
            "step_pct": payload.step_pct,
            "grid_amount": payload.grid_amount,
            "max_grids": payload.max_grids,
            "commission_rate": payload.commission_rate,
        },
    )


__all__ = ["router", "compute_metrics"]
