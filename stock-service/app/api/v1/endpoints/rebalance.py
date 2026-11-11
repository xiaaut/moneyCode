"""四维联动调仓雷达与补仓沙盘接口。"""

from __future__ import annotations

from fastapi import APIRouter, Body, HTTPException

from app.services import rebalance_service

router = APIRouter(tags=["rebalance"])


@router.get("/rebalance/dashboard", summary="四维联动调仓雷达总览")
async def dashboard() -> dict:
    try:
        return await rebalance_service.get_dashboard()
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"调仓雷达计算失败：{exc}") from exc


@router.post("/rebalance/plans", summary="新增调仓工单")
async def create_plan(payload: dict = Body(...)) -> dict:
    try:
        return await rebalance_service.create_plan(payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.put("/rebalance/plans/{plan_id}", summary="更新调仓工单")
async def update_plan(plan_id: int, payload: dict = Body(...)) -> dict:
    try:
        return await rebalance_service.update_plan(plan_id, payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.delete("/rebalance/plans/{plan_id}", summary="删除调仓工单")
async def delete_plan(plan_id: int) -> dict:
    await rebalance_service.delete_plan(plan_id)
    return {"deleted": plan_id}


@router.post("/rebalance/plans/{plan_id}/done", summary="标记工单已执行")
async def mark_done(plan_id: int) -> dict:
    return await rebalance_service.mark_done(plan_id)
