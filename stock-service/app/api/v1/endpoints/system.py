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
    return daily_sync.daily_state
