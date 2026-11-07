"""收盘一键更新：四合一异步同步流水线。

按顺序依次调用已有采集服务，单步报错记录 warning 但不中断后续步骤：
    Step 1 (15%) 刷新持仓现价并重算市值/盈亏
    Step 2 (35%) 同步全市场行情与季报业绩（akshare_sync）
    Step 3 (30%) 同步持仓/自选主力资金流（fund_flow）
    Step 4 (20%) 同步龙虎榜汇总与席位明细（fund_flow）

进度状态：running / current_step / step_label / percent / steps[].status / last_completed_at。
"""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Awaitable, Callable

from sqlalchemy import select

from app.db.session import AsyncSessionLocal
from app.models import Position
from app.services import akshare_sync, fund_flow
from app.services.quotes import quote_service

logger = logging.getLogger(__name__)

STEPS = [
    {"key": "positions", "label": "刷新持仓现价", "weight": 15},
    {"key": "market", "label": "同步全市场行情", "weight": 35},
    {"key": "fundflow", "label": "同步主力资金流", "weight": 30},
    {"key": "lhb", "label": "同步龙虎榜", "weight": 20},
]


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _blank_state() -> dict[str, Any]:
    return {
        "running": False,
        "current_step": 0,
        "step_label": "",
        "percent": 0,
        "steps": [
            {
                "key": s["key"],
                "label": s["label"],
                "weight": s["weight"],
                "status": "pending",
                "duration": 0,
                "summary": "",
            }
            for s in STEPS
        ],
        "last_completed_at": None,
        "error": "",
    }


daily_state: dict[str, Any] = _blank_state()


def launch_daily_sync() -> dict[str, Any]:
    """同步置 running=true 后派发后台任务。"""
    global daily_state
    daily_state = _blank_state()
    daily_state["running"] = True
    daily_state["current_step"] = 1
    daily_state["step_label"] = STEPS[0]["label"]
    daily_state["percent"] = 0
    daily_state["steps"][0]["status"] = "running"
    asyncio.create_task(run_daily_sync())
    return daily_state


async def _step_positions() -> str:
    """刷新持仓现价并重算市值/盈亏/收益率，返回摘要。"""
    async with AsyncSessionLocal() as session:
        positions = (await session.scalars(select(Position))).all()
        if not positions:
            return "无持仓"

        codes = [p.code for p in positions]
        quotes = await quote_service.fetch_tencent(codes)

        updated = 0
        for p in positions:
            q = quotes.get(p.code)
            price = q.get("price") if q else None
            if not price:
                continue
            qty = int(p.shares or 0)
            cost = float(p.cost_price or 0)
            p.current_price = Decimal(str(round(float(price), 4)))
            p.market_value = Decimal(str(round(float(price) * qty, 2)))
            p.profit_amount = Decimal(str(round((float(price) - cost) * qty, 2)))
            p.profit_ratio = (
                Decimal(str(round((float(price) - cost) / cost * 100, 4))) if cost else None
            )
            updated += 1

        await session.commit()
        return f"刷新 {updated}/{len(positions)} 只持仓现价"


def _summary_for(index: int) -> str:
    if index == 1:
        return daily_state.get("_step1_summary", "")
    if index == 2:
        return akshare_sync.akshare_job.message or ""
    if index == 3:
        return fund_flow.fund_flow_job.message or ""
    if index == 4:
        return fund_flow.lhb_job.message or ""
    return ""


async def _run_step(index: int, fn: Callable[[], Awaitable[Any]]) -> None:
    step = daily_state["steps"][index - 1]
    step["status"] = "running"
    daily_state["current_step"] = index
    daily_state["step_label"] = step["label"]
    daily_state["percent"] = sum(s["weight"] for s in daily_state["steps"][: index - 1])

    started = time.time()
    try:
        result = await fn()
        if index == 1:
            daily_state["_step1_summary"] = result or ""
        step["status"] = "done"
        step["summary"] = _summary_for(index)
    except Exception as exc:  # noqa: BLE001 - 单步失败不中断后续步骤
        logger.warning("daily-sync 第 %d 步「%s」失败: %s", index, step["label"], exc)
        step["status"] = "error"
        step["summary"] = str(exc)[:200]
    finally:
        step["duration"] = round(time.time() - started, 1)
        daily_state["percent"] = sum(s["weight"] for s in daily_state["steps"][:index])


async def run_daily_sync() -> None:
    global daily_state
    try:
        await _run_step(1, _step_positions)
        await _run_step(2, akshare_sync.run_akshare_sync)
        await _run_step(3, fund_flow.run_fund_flow_sync)
        await _run_step(4, fund_flow.run_lhb_sync)
    except Exception as exc:  # noqa: BLE001 - 兜底
        logger.exception("daily-sync 整体失败")
        daily_state["error"] = str(exc)[:200]
    finally:
        daily_state["running"] = False
        daily_state["current_step"] = 0
        daily_state["percent"] = 100
        daily_state["last_completed_at"] = _now_iso()
