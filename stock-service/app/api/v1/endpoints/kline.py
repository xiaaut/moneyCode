"""K 线同步接口（全市场 Parquet + ETF 前复权日线补偿）。"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from app.services import kline_sync

router = APIRouter(tags=["kline"])


@router.post("/kline/sync-historical", summary="全市场 10 年日 K 全量同步")
async def sync_historical() -> dict:
    if kline_sync.kline_job["running"]:
        raise HTTPException(status_code=409, detail="K 线同步正在进行中")
    job = kline_sync.launch_sync("historical")
    return {"started": True, "job": job}


@router.get("/kline/sync-historical/progress", summary="全量同步进度")
async def sync_historical_progress() -> dict:
    return kline_sync.kline_job


@router.post("/kline/sync-incremental", summary="近 10 交易日日 K 增量同步")
async def sync_incremental() -> dict:
    if kline_sync.kline_job["running"]:
        raise HTTPException(status_code=409, detail="K 线同步正在进行中")
    job = kline_sync.launch_sync("incremental")
    return {"started": True, "job": job}


@router.post("/kline/sync-etf/{code}", summary="单只 ETF 前复权日线补偿")
async def sync_etf(code: str, years: int = Query(5, ge=1, le=5)) -> dict:
    try:
        count = await kline_sync.sync_etf_klines(code, years)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"ETF 日线拉取失败：{exc}") from exc
    return {"code": code, "inserted": count}
