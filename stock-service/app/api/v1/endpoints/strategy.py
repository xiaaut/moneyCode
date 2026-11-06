"""选股策略配置接口（单例，id 恒为 1）。

    GET    /api/strategy-config      读取配置（无记录时返回前端默认值）
    PUT    /api/strategy-config      整体 upsert（幂等）
    POST   /api/strategy-config      PUT 的别名
    DELETE /api/strategy-config      重置为默认值（删除单例行）
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.models import StrategyConfig
from app.schemas.strategy import StrategyConfigIn, StrategyConfigOut

router = APIRouter(tags=["strategy"])


def _to_out(row: StrategyConfig | None) -> StrategyConfigOut:
    if row is None:
        return StrategyConfigOut()
    return StrategyConfigOut(
        stock_name=row.stock_name,
        stock_code=row.stock_code,
        current_eps=float(row.current_eps) if row.current_eps is not None else None,
        target_eps=float(row.target_eps) if row.target_eps is not None else None,
        current_pe=float(row.current_pe) if row.current_pe is not None else None,
        historical_avg_pe=float(row.historical_avg_pe) if row.historical_avg_pe is not None else None,
        mean_pe=float(row.mean_pe) if row.mean_pe is not None else None,
        hold_years=float(row.hold_years) if row.hold_years is not None else None,
        portfolio_count=row.portfolio_count,
        max_industry_count=row.max_industry_count,
        updated_at=row.updated_at.isoformat() if row.updated_at else None,
    )


async def _upsert(db: AsyncSession, payload: StrategyConfigIn) -> StrategyConfigOut:
    values = payload.model_dump(exclude_none=False)
    values["id"] = 1

    table = StrategyConfig.__table__
    statement = pg_insert(table).values(values)
    set_map = {
        "stock_name": statement.excluded.stock_name,
        "stock_code": statement.excluded.stock_code,
        "current_eps": statement.excluded.current_eps,
        "target_eps": statement.excluded.target_eps,
        "current_pe": statement.excluded.current_pe,
        "historical_avg_pe": statement.excluded.historical_avg_pe,
        "mean_pe": statement.excluded.mean_pe,
        "hold_years": statement.excluded.hold_years,
        "portfolio_count": statement.excluded.portfolio_count,
        "max_industry_count": statement.excluded.max_industry_count,
        "updated_at": func.now(),
    }
    await db.execute(
        statement.on_conflict_do_update(index_elements=["id"], set_=set_map)
    )
    await db.commit()

    row = await db.scalar(select(StrategyConfig).where(StrategyConfig.id == 1))
    return _to_out(row)


@router.get("/strategy-config", response_model=StrategyConfigOut, summary="读取选股策略配置")
async def get_strategy_config(db: AsyncSession = Depends(get_db)) -> StrategyConfigOut:
    row = await db.scalar(select(StrategyConfig).where(StrategyConfig.id == 1))
    return _to_out(row)


@router.put("/strategy-config", response_model=StrategyConfigOut, summary="保存选股策略配置")
async def put_strategy_config(
    payload: StrategyConfigIn, db: AsyncSession = Depends(get_db)
) -> StrategyConfigOut:
    return await _upsert(db, payload)


@router.post("/strategy-config", response_model=StrategyConfigOut, summary="保存选股策略配置（PUT 别名）")
async def post_strategy_config(
    payload: StrategyConfigIn, db: AsyncSession = Depends(get_db)
) -> StrategyConfigOut:
    return await _upsert(db, payload)


@router.delete("/strategy-config", response_model=StrategyConfigOut, summary="重置选股策略配置")
async def delete_strategy_config(db: AsyncSession = Depends(get_db)) -> StrategyConfigOut:
    row = await db.scalar(select(StrategyConfig).where(StrategyConfig.id == 1))
    if row is not None:
        await db.delete(row)
        await db.commit()
    return StrategyConfigOut()
