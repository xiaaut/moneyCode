"""资金流向与龙虎榜接口。

    POST /api/fund-flow/sync-portfolio   同步持仓+自选资金流（后台单例）
    GET  /api/fund-flow/sync-progress    同步进度
    GET  /api/fund-flow/stock/{code}     单股资金流 + 龙虎榜（按需补拉）
    GET  /api/fund-flow/mini-summary     近 5 日主力摘要
    GET  /api/fund-flow/market-rank      全市场主力排行
    POST /api/lhb/sync                   龙虎榜同步（后台单例）
    GET  /api/lhb/progress               龙虎榜同步进度
    GET  /api/lhb/daily                  龙虎榜每日总览
"""

from __future__ import annotations

from fastapi import APIRouter, Query, Response, status

from app.services import fund_flow as svc

router = APIRouter(tags=["fund-flow"])


@router.post("/fund-flow/sync-portfolio", summary="同步持仓/自选资金流")
async def sync_portfolio(response: Response) -> dict:
    if svc.fund_flow_job.running:
        response.status_code = status.HTTP_409_CONFLICT
        return {"message": "资金流同步正在进行中", "job": svc.fund_flow_job.model_dump(by_alias=True)}
    svc.launch_fund_flow_sync()
    return {"started": True, "job": svc.fund_flow_job.model_dump(by_alias=True)}


@router.get("/fund-flow/sync-progress", summary="资金流同步进度")
async def sync_progress() -> dict:
    return {"job": svc.fund_flow_job.model_dump(by_alias=True)}


@router.get("/fund-flow/stock/{code}", summary="单股资金流与龙虎榜")
async def stock_fund_flow(code: str, refresh: bool = Query(False)) -> dict:
    return await svc.get_stock_fund_flow_data(code, refresh=refresh)


@router.get("/fund-flow/mini-summary", summary="近 5 日主力摘要")
async def mini_summary(codes: str = Query("", description="逗号分隔的股票代码")) -> dict:
    code_list = [c.strip() for c in codes.split(",") if c.strip()]
    return await svc.mini_summary(code_list)


@router.get("/fund-flow/market-rank", summary="全市场主力资金排行")
async def market_rank(indicator: str = Query("今日", description="今日/5日/10日")) -> dict:
    rows = await svc.market_rank(indicator)
    return {"indicator": indicator, "rows": rows}


@router.post("/lhb/sync", summary="同步龙虎榜")
async def lhb_sync(response: Response) -> dict:
    if svc.lhb_job.running:
        response.status_code = status.HTTP_409_CONFLICT
        return {"message": "龙虎榜同步正在进行中", "job": svc.lhb_job.model_dump(by_alias=True)}
    svc.launch_lhb_sync()
    return {"started": True, "job": svc.lhb_job.model_dump(by_alias=True)}


@router.get("/lhb/progress", summary="龙虎榜同步进度")
async def lhb_progress() -> dict:
    return {"job": svc.lhb_job.model_dump(by_alias=True)}


@router.get("/lhb/daily", summary="龙虎榜每日总览")
async def lhb_daily(inst_only: bool = Query(False, description="仅看有机构净买的")) -> dict:
    return await svc.lhb_daily(inst_only=inst_only)
