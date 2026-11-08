"""系统级接口（收盘一键更新流水线）。"""

from __future__ import annotations

from fastapi import APIRouter, Response, status

from app.services import daily_sync

router = APIRouter(tags=["system"])


@router.post("/system/daily-sync", summary="启动收盘一键更新（四合一流水线）")
async def start_daily_sync(response: Response) -> dict:
    if daily_sync.daily_state["running"]:
        response.status_code = status.HTTP_409_CONFLICT
        return {"message": "收盘更新正在进行中", "job": daily_sync.daily_state}
    daily_sync.launch_daily_sync()
    return {"started": True, "job": daily_sync.daily_state}


@router.get("/system/daily-sync/progress", summary="收盘一键更新进度")
async def daily_sync_progress() -> dict:
    state = daily_sync.daily_state
    # 内存态丢失时依次兜底：system_meta 持久化值 → stocks 表最后更新时间
    if not state.get("last_completed_at"):
        last = await daily_sync.load_last_completed()
        if not last:
            last = await daily_sync.load_stocks_last_updated()
        return {**state, "last_completed_at": last}
    return state
