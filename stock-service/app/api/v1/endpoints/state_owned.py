"""全市场国资 / 政府控股接口。

    GET  /api/state-owned           从 stocks 表组装（board 非空 = 全市场主板 + 北交所）
    POST /api/state-owned/start     启动后台扫描（单例，asyncio.create_task）
    GET  /api/state-owned/progress  实时进度
"""

from __future__ import annotations

from collections import Counter
from typing import Any

from fastapi import APIRouter, Depends, Response, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.models import Stock
from app.services import jobs as jobs_mod
from app.services import ths as ths_mod

router = APIRouter(tags=["state-owned"])


async def _assemble(db: AsyncSession) -> dict[str, Any]:
    rows = (
        await db.scalars(select(Stock).where(Stock.board.is_not(None)).order_by(Stock.code))
    ).all()

    out_rows: list[dict[str, Any]] = []
    for stock in rows:
        extra = stock.extra or {}
        category = stock.controller_category or "unknown"
        out_rows.append(
            {
                "code": stock.code,
                "name": stock.name,
                "board": stock.board,
                "category": category,
                "categoryLabel": extra.get("category_label")
                or ths_mod.STATE_CATEGORY_LABELS.get(category, category),
                "actualHolder": extra.get("actual_holder") or "",
                "finalController": extra.get("final_controller") or "",
                "controllingHolder": extra.get("controlling_holder") or "",
                "source": extra.get("source") or "东方财富数据中心·公司概况",
                "sourceUrl": extra.get("source_url") or "",
                "note": extra.get("note") or "",
                "price": float(stock.price) if stock.price is not None else None,
                "changePct": float(stock.change_pct) if stock.change_pct is not None else None,
                "totalMarketValue": None,
            }
        )

    counts = dict(Counter(r["category"] for r in out_rows))
    latest = await db.scalar(
        select(func.max(Stock.updated_at)).where(Stock.board.is_not(None))
    )
    updated_at = jobs_mod.state_owned_meta.get("updatedAt") or (
        latest.isoformat() if latest else None
    )

    return {
        "updatedAt": updated_at,
        "total": len(out_rows),
        "counts": counts,
        "sourceStats": jobs_mod.state_owned_meta.get("sourceStats") or {},
        "rows": out_rows,
    }


@router.get("/state-owned", summary="全市场国资数据")
async def get_state_owned(db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    data = await _assemble(db)
    data["job"] = jobs_mod.state_owned_job.model_dump(by_alias=True)
    return data


@router.get("/state-owned/progress", summary="国资扫描进度")
async def state_owned_progress() -> dict[str, Any]:
    return {"job": jobs_mod.state_owned_job.model_dump(by_alias=True)}


@router.post("/state-owned/start", summary="启动国资扫描")
async def start_state_owned(
    response: Response,
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if jobs_mod.state_owned_job.running:
        response.status_code = status.HTTP_409_CONFLICT
        return {"message": "刷新任务正在进行中，请等待完成", "job": jobs_mod.state_owned_job.model_dump(by_alias=True)}

    body = payload or {}
    force = bool(body.get("force"))
    limit_raw = body.get("limit")
    limit = int(limit_raw) if limit_raw is not None and int(limit_raw) > 0 else None

    jobs_mod.launch_state_owned(force, limit)
    return {
        "started": True,
        "force": force,
        "limit": limit,
        "job": jobs_mod.state_owned_job.model_dump(by_alias=True),
    }
