"""持仓 / 自选股 / 清仓记录接口。

兼容口径（与前端原 vite 中间件完全一致）：
    GET  /api/portfolio           -> 纯数组
    POST /api/portfolio           -> 数组整体覆盖，返回 {"ok":true}
    GET  /api/watchlist           -> 纯数组
    POST /api/watchlist           -> 数组整体覆盖
    GET  /api/closed-positions    -> 纯数组
    POST /api/closed-positions    -> 数组整体覆盖

在此之上补充单条 REST 接口（新增/修改/清仓归档/删除），供后续前端逐步替换整数组写入。
"""

from __future__ import annotations

import asyncio
import time
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.models import Position, TradeHistory, WatchlistItem
from app.schemas.common import OkResponse, fmt_num, fmt_pct, num_or_dash, parse_number
from app.services import onboarding
from app.schemas.portfolio import (
    ClosedPositionCreate,
    PositionIn,
    PositionOut,
    TradeHistoryIn,
    TradeHistoryOut,
    WatchlistItemIn,
    WatchlistItemOut,
)

router = APIRouter(tags=["portfolio"])

DASH = "-"


# --------------------------------------------------------------------------- #
# 转换工具
# --------------------------------------------------------------------------- #
def _decimal(value: Any) -> Decimal | None:
    return parse_number(value)


def _float(value: Any, default: float = 0.0) -> float:
    number = parse_number(value)
    return float(number) if number is not None else default


def _position_to_out(position: Position, total_market_value: float) -> PositionOut:
    extra = position.extra or {}
    quantity = int(position.shares or 0)
    cost_price = float(position.cost_price or 0)
    current_price = float(position.current_price or 0)
    total_cost = round(cost_price * quantity, 2)
    market_value = round(current_price * quantity, 2)
    profit_amount = round(market_value - total_cost, 2)
    profit_ratio = (profit_amount / total_cost * 100) if total_cost > 0 else 0.0
    position_ratio = (market_value / total_market_value * 100) if total_market_value > 0 else 0.0

    return PositionOut(
        id=int(position.id),
        code=position.code,
        name=position.name,
        sector=position.sector,
        cost_price=round(cost_price, 3),
        quantity=quantity,
        current_price=round(current_price, 2),
        total_cost=total_cost,
        market_value=market_value,
        profit_amount=profit_amount,
        profit_ratio=f"{profit_ratio:.2f}%",
        position_ratio=f"{position_ratio:.2f}%",
        pb=_float_or_none(extra.get("pb")),
        pe=_float_or_none(extra.get("pe")),
        roe=fmt_pct(extra.get("roe")) if extra.get("roe") is not None else DASH,
        avg_pe=num_or_dash(extra.get("avg_pe")),
        eps=num_or_dash(extra.get("eps")),
        eps3y=num_or_dash(extra.get("eps_3y")),
        g=fmt_pct(extra.get("g")) if extra.get("g") is not None else DASH,
        # 前端原格式：peg 是字符串（(pe/g).toFixed(2)），avgPe/eps/eps3y 是数字或 "-"
        peg=fmt_num(extra.get("peg")) if extra.get("peg") is not None else DASH,
        expected_return=(
            fmt_pct(extra.get("expected_return")) if extra.get("expected_return") is not None else DASH
        ),
    )


def _float_or_none(value: Any) -> float | None:
    number = parse_number(value)
    return round(float(number), 2) if number is not None else None


def _position_row(payload: PositionIn) -> dict[str, Any]:
    code = str(payload.code or "").strip()
    if not code:
        raise ValueError("持仓缺少 code")
    name = (payload.name or "").strip() or code
    shares = int(_float(payload.quantity))
    cost_price = _decimal(payload.cost_price)
    current_price = _decimal(payload.current_price)
    total_cost = float(cost_price or 0) * shares
    market_value = float(current_price or 0) * shares
    profit_amount = market_value - total_cost
    return {
        "code": code,
        "name": name,
        "sector": payload.sector,
        "shares": shares,
        "cost_price": cost_price,
        "current_price": current_price,
        "market_value": Decimal(str(round(market_value, 2))),
        "profit_amount": Decimal(str(round(profit_amount, 2))),
        "profit_ratio": (
            Decimal(str(round(profit_amount / total_cost * 100, 4))) if total_cost > 0 else None
        ),
        "extra": {
            "pb": _float_or_none(payload.pb),
            "pe": _float_or_none(payload.pe),
            "roe": _float_or_none(payload.roe),
            "avg_pe": _float_or_none(payload.avg_pe),
            "eps": _float_or_none(payload.eps),
            "eps_3y": _float_or_none(payload.eps3y),
            "g": _float_or_none(payload.g),
            "peg": _float_or_none(payload.peg),
            "expected_return": _float_or_none(payload.expected_return),
        },
    }


def _watchlist_to_out(item: WatchlistItem) -> WatchlistItemOut:
    return WatchlistItemOut(
        id=int(item.id),
        stock_code=item.stock_code,
        stock_name=item.stock_name,
        created_at=item.created_at.isoformat() if item.created_at else None,
        current_eps=num_or_dash(item.current_eps),
        target_eps=num_or_dash(item.target_eps),
        current_pe=num_or_dash(item.current_pe),
        historical_avg_pe=num_or_dash(item.historical_avg_pe),
        mean_pe=num_or_dash(item.mean_pe),
        hold_years=item.hold_years,
        g_percent=num_or_dash(item.g_percent),
        peg=num_or_dash(item.peg),
        annualized_return_percent=num_or_dash(item.annualized_return_percent),
        pass_count=item.pass_count,
        total_conditions=item.total_conditions,
        all_passed=bool(item.all_passed),
        details=item.details or [],
    )


def _trade_to_out(row: TradeHistory) -> TradeHistoryOut:
    return TradeHistoryOut(
        id=int(row.id),
        code=row.code,
        name=row.name,
        quantity=row.quantity,
        buy_price=_float_or_none(row.buy_price),
        sell_price=_float_or_none(row.sell_price),
        fee=_float_or_none(row.fee),
        buy_date=row.buy_date.isoformat() if row.buy_date else None,
        sell_date=row.sell_date.isoformat() if row.sell_date else None,
        profit_amount=_float_or_none(row.profit_amount),
        profit_ratio=_float_or_none(row.profit_ratio),
        hold_days=row.hold_days,
        note=row.note,
        created_at=row.created_at.isoformat() if row.created_at else None,
    )


def _parse_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if not value:
        return None
    text = str(value).strip().replace("Z", "+00:00")
    for parser in (
        datetime.fromisoformat,
        lambda t: datetime.strptime(t, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc),
        lambda t: datetime.strptime(t, "%Y-%m-%d").replace(tzinfo=timezone.utc),
    ):
        try:
            parsed = parser(text)
            return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
        except (ValueError, TypeError):
            continue
    return None


def _parse_date(value: Any) -> date | None:
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, datetime):
        return value.date()
    if not value:
        return None
    text = str(value).strip()[:10].replace("/", "-")
    try:
        return date.fromisoformat(text)
    except ValueError:
        return None


# --------------------------------------------------------------------------- #
# 持仓
# --------------------------------------------------------------------------- #
@router.get("/portfolio", response_model=list[PositionOut], summary="持仓列表")
async def list_positions(db: AsyncSession = Depends(get_db)) -> list[PositionOut]:
    rows = (await db.scalars(select(Position).order_by(Position.id))).all()
    total_market_value = sum(
        float(row.current_price or 0) * int(row.shares or 0) for row in rows
    )
    return [_position_to_out(row, total_market_value) for row in rows]


@router.post("/portfolio", response_model=OkResponse, summary="整数组覆盖持仓")
async def replace_positions(
    payload: list[PositionIn], db: AsyncSession = Depends(get_db)
) -> OkResponse:
    """前端 Positions.vue 每次保存都提交完整数组，这里按 code 覆盖并删除多余行。"""
    merged: dict[str, dict[str, Any]] = {}
    for item in payload:
        try:
            row = _position_row(item)
        except ValueError:
            continue
        merged[row["code"]] = row

    if not merged:
        await db.execute(delete(Position))
        await db.commit()
        return OkResponse(message="持仓已清空")

    rows = list(merged.values())
    codes = list(merged.keys())

    await db.execute(delete(Position).where(Position.code.notin_(codes)))

    table = Position.__table__
    statement = pg_insert(table).values(rows)
    updatable = [c.name for c in table.columns if c.name not in {"id", "code", "created_at"}]
    statement = statement.on_conflict_do_update(
        index_elements=["code"],
        set_={name: statement.excluded[name] for name in updatable}
        | {"updated_at": func.now()},
    )
    await db.execute(statement)
    await db.commit()

    # 同步股票主数据，保证筛选页能搜到持仓标的（含 ETF）
    await _sync_stocks_from_positions(db, rows)
    return OkResponse(message=f"已保存 {len(rows)} 条持仓")


@router.post(
    "/portfolio/positions", response_model=PositionOut, status_code=status.HTTP_201_CREATED,
    summary="新增/修改单条持仓",
)
async def upsert_position(
    payload: PositionIn, db: AsyncSession = Depends(get_db)
) -> PositionOut:
    try:
        row = _position_row(payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    table = Position.__table__
    statement = pg_insert(table).values(row)
    updatable = [c.name for c in table.columns if c.name not in {"id", "code", "created_at"}]
    statement = statement.on_conflict_do_update(
        index_elements=["code"],
        set_={name: statement.excluded[name] for name in updatable} | {"updated_at": func.now()},
    )
    await db.execute(statement)
    await db.commit()

    saved = await db.scalar(select(Position).where(Position.code == row["code"]))
    if saved is None:
        raise HTTPException(status_code=500, detail="持仓写入失败")
    total = await db.scalar(
        select(func.coalesce(func.sum(Position.shares * Position.current_price), 0))
    )
    await _sync_stocks_from_positions(db, [row])
    # 新股票自动入驻：异步拉日线 + 财报体检，不阻塞响应
    asyncio.create_task(onboarding.auto_onboard_stock(row["code"]))
    return _position_to_out(saved, float(total or 0))


@router.put("/portfolio/positions/{code}", response_model=PositionOut, summary="按代码修改持仓")
async def update_position(
    code: str, payload: PositionIn, db: AsyncSession = Depends(get_db)
) -> PositionOut:
    payload.code = code
    return await upsert_position(payload, db)


@router.delete(
    "/portfolio/{code}", response_model=OkResponse, summary="删除持仓（可同时归档到清仓记录）"
)
async def delete_position(
    code: str,
    archive: bool = Query(True, description="是否写入 trade_history 清仓记录"),
    sell_price: float | None = Query(None, description="清仓价，默认用最新价"),
    fee: float = Query(0, description="手续费"),
    sell_date: date | None = Query(None, description="清仓日期，默认今天"),
    note: str | None = Query(None),
    db: AsyncSession = Depends(get_db),
) -> OkResponse:
    position = await db.scalar(select(Position).where(Position.code == code))
    if position is None:
        raise HTTPException(status_code=404, detail=f"持仓不存在：{code}")

    archived_id: int | None = None
    if archive:
        quantity = int(position.shares or 0)
        buy_price = float(position.cost_price or 0)
        exit_price = float(sell_price if sell_price is not None else (position.current_price or 0))
        exit_fee = float(fee or 0)
        profit = (exit_price - buy_price) * quantity - exit_fee
        cost_basis = buy_price * quantity
        trade = TradeHistory(
            code=position.code,
            name=position.name,
            quantity=quantity,
            buy_price=Decimal(str(round(buy_price, 4))),
            sell_price=Decimal(str(round(exit_price, 4))),
            fee=Decimal(str(round(exit_fee, 2))),
            sell_date=sell_date or date.today(),
            hold_days=None,
            profit_amount=Decimal(str(round(profit, 2))),
            profit_ratio=Decimal(str(round(profit / cost_basis * 100, 4))) if cost_basis else None,
            note=note or f"由持仓 {position.code} 清仓归档",
        )
        db.add(trade)
        await db.flush()
        archived_id = int(trade.id)

    await db.delete(position)
    await db.commit()
    return OkResponse(
        message=f"已删除持仓 {code}" + (f"，并归档为清仓记录 #{archived_id}" if archived_id else "")
    )


async def _sync_stocks_from_positions(db: AsyncSession, rows: list[dict[str, Any]]) -> None:
    """把持仓的行业/估值指标补进 stocks（只补空值，不覆盖已有主数据）。"""
    from app.models import Stock

    values = []
    for row in rows:
        extra = row.get("extra") or {}
        values.append(
            {
                "code": row["code"],
                "name": row["name"],
                "industry": row.get("sector"),
                "pe_ttm": extra.get("pe"),
                "pb": extra.get("pb"),
                "roe": extra.get("roe"),
                "eps_current": extra.get("eps"),
                "eps_forecast": extra.get("eps_3y"),
                "price": row.get("current_price"),
            }
        )
    if not values:
        return

    table = Stock.__table__
    statement = pg_insert(table).values(values)
    statement = statement.on_conflict_do_update(
        index_elements=["code"],
        set_={
            "name": func.coalesce(statement.excluded.name, table.c.name),
            "industry": func.coalesce(statement.excluded.industry, table.c.industry),
            "pe_ttm": func.coalesce(statement.excluded.pe_ttm, table.c.pe_ttm),
            "pb": func.coalesce(statement.excluded.pb, table.c.pb),
            "roe": func.coalesce(statement.excluded.roe, table.c.roe),
            "eps_current": func.coalesce(statement.excluded.eps_current, table.c.eps_current),
            "eps_forecast": func.coalesce(statement.excluded.eps_forecast, table.c.eps_forecast),
            "price": func.coalesce(statement.excluded.price, table.c.price),
            "updated_at": func.now(),
        },
    )
    await db.execute(statement)
    await db.commit()


# --------------------------------------------------------------------------- #
# 自选股
# --------------------------------------------------------------------------- #
@router.get("/watchlist", response_model=list[WatchlistItemOut], summary="观察列表")
async def list_watchlist(db: AsyncSession = Depends(get_db)) -> list[WatchlistItemOut]:
    rows = (
        await db.scalars(select(WatchlistItem).order_by(WatchlistItem.created_at.desc()))
    ).all()
    return [_watchlist_to_out(row) for row in rows]


@router.post("/watchlist", response_model=OkResponse, summary="整数组覆盖观察列表")
async def replace_watchlist(
    payload: list[WatchlistItemIn], db: AsyncSession = Depends(get_db)
) -> OkResponse:
    await db.execute(delete(WatchlistItem))

    values: list[dict[str, Any]] = []
    seen: set[int] = set()
    for item in payload:
        code = "".join(ch for ch in str(item.stock_code or "") if ch.isdigit())[-6:]
        if not code:
            continue
        item_id = int(item.id) if item.id is not None else int(time.time() * 1000)
        if item_id in seen:
            continue
        seen.add(item_id)
        values.append(
            {
                "id": item_id,
                "stock_code": code,
                "stock_name": item.stock_name,
                "current_eps": _decimal(item.current_eps),
                "target_eps": _decimal(item.target_eps),
                "current_pe": _decimal(item.current_pe),
                "historical_avg_pe": _decimal(item.historical_avg_pe),
                "mean_pe": _decimal(item.mean_pe),
                "hold_years": int(_float(item.hold_years)) if item.hold_years is not None else None,
                "g_percent": _decimal(item.g_percent),
                "peg": _decimal(item.peg),
                "annualized_return_percent": _decimal(item.annualized_return_percent),
                "pass_count": int(_float(item.pass_count)) if item.pass_count is not None else None,
                "total_conditions": (
                    int(_float(item.total_conditions)) if item.total_conditions is not None else None
                ),
                "all_passed": bool(item.all_passed),
                "details": item.details or [],
                "created_at": _parse_datetime(item.created_at) or datetime.now(timezone.utc),
            }
        )

    if values:
        await db.execute(pg_insert(WatchlistItem.__table__).values(values))
    await db.commit()
    return OkResponse(message=f"已保存 {len(values)} 条观察项")


@router.post(
    "/watchlist/items", response_model=WatchlistItemOut, status_code=status.HTTP_201_CREATED,
    summary="新增单条观察项",
)
async def add_watchlist_item(
    payload: WatchlistItemIn, db: AsyncSession = Depends(get_db)
) -> WatchlistItemOut:
    code = "".join(ch for ch in str(payload.stock_code or "") if ch.isdigit())[-6:]
    if not code:
        raise HTTPException(status_code=400, detail="stockCode 必须是 6 位股票代码")

    item = WatchlistItem(
        id=int(payload.id) if payload.id is not None else int(time.time() * 1000),
        stock_code=code,
        stock_name=payload.stock_name,
        current_eps=_decimal(payload.current_eps),
        target_eps=_decimal(payload.target_eps),
        current_pe=_decimal(payload.current_pe),
        historical_avg_pe=_decimal(payload.historical_avg_pe),
        mean_pe=_decimal(payload.mean_pe),
        hold_years=int(_float(payload.hold_years)) if payload.hold_years is not None else None,
        g_percent=_decimal(payload.g_percent),
        peg=_decimal(payload.peg),
        annualized_return_percent=_decimal(payload.annualized_return_percent),
        pass_count=int(_float(payload.pass_count)) if payload.pass_count is not None else None,
        total_conditions=(
            int(_float(payload.total_conditions)) if payload.total_conditions is not None else None
        ),
        all_passed=bool(payload.all_passed),
        details=payload.details or [],
        created_at=_parse_datetime(payload.created_at) or datetime.now(timezone.utc),
    )
    db.add(item)
    await db.commit()
    await db.refresh(item)
    # 新自选股自动入驻：异步拉日线 + 财报体检
    asyncio.create_task(onboarding.auto_onboard_stock(code))
    return _watchlist_to_out(item)


@router.delete("/watchlist/{item_id}", response_model=OkResponse, summary="删除观察项")
async def delete_watchlist_item(item_id: int, db: AsyncSession = Depends(get_db)) -> OkResponse:
    item = await db.scalar(select(WatchlistItem).where(WatchlistItem.id == item_id))
    if item is None:
        raise HTTPException(status_code=404, detail=f"观察项不存在：{item_id}")
    await db.delete(item)
    await db.commit()
    return OkResponse(message=f"已删除观察项 {item_id}")


# --------------------------------------------------------------------------- #
# 清仓记录
# --------------------------------------------------------------------------- #
@router.get("/closed-positions", response_model=list[TradeHistoryOut], summary="清仓记录列表")
async def list_closed_positions(db: AsyncSession = Depends(get_db)) -> list[TradeHistoryOut]:
    rows = (
        await db.scalars(
            select(TradeHistory).order_by(
                TradeHistory.sell_date.desc().nullslast(), TradeHistory.id.desc()
            )
        )
    ).all()
    return [_trade_to_out(row) for row in rows]


@router.post("/closed-positions", response_model=OkResponse, summary="整数组覆盖清仓记录")
async def replace_closed_positions(
    payload: list[TradeHistoryIn], db: AsyncSession = Depends(get_db)
) -> OkResponse:
    await db.execute(delete(TradeHistory))

    values: list[dict[str, Any]] = []
    for item in payload:
        code = str(item.code or "").strip()
        if not code:
            continue
        buy_date = _parse_date(item.buy_date)
        sell_date = _parse_date(item.sell_date)
        quantity = int(_float(item.quantity)) if item.quantity is not None else None
        buy_price = _decimal(item.buy_price)
        profit = _decimal(item.resolved_profit())
        profit_ratio = _decimal(item.profit_ratio)
        hold_days = int(_float(item.hold_days)) if item.hold_days is not None else None

        # 前端未传时服务端兜底推导，保证统计口径一致
        if hold_days is None and buy_date and sell_date:
            hold_days = (sell_date - buy_date).days
        if profit_ratio is None and profit is not None and buy_price and quantity:
            cost_basis = float(buy_price) * quantity
            if cost_basis:
                profit_ratio = Decimal(str(round(float(profit) / cost_basis * 100, 4)))

        values.append(
            {
                "id": int(item.id) if item.id is not None else None,
                "code": code,
                "name": (item.name or code).strip(),
                "quantity": quantity,
                "buy_price": buy_price,
                "sell_price": _decimal(item.sell_price),
                "fee": _decimal(item.fee) or Decimal("0"),
                "buy_date": buy_date,
                "sell_date": sell_date,
                "hold_days": hold_days,
                "profit_amount": profit,
                "profit_ratio": profit_ratio,
                "note": item.note,
                "source_key": None,
            }
        )

    # id 为空的行交给序列号；显式 id 与序列号共存不冲突（前端用毫秒时间戳）
    explicit = [row for row in values if row["id"] is not None]
    auto = [{k: v for k, v in row.items() if k != "id"} for row in values if row["id"] is None]

    if explicit:
        await db.execute(pg_insert(TradeHistory.__table__).values(explicit))
    if auto:
        await db.execute(pg_insert(TradeHistory.__table__).values(auto))
    await db.commit()
    return OkResponse(message=f"已保存 {len(values)} 条清仓记录")
