"""新增股票全自动入驻钩子：日线 + 财报三表体检。

auto_onboard_stock(code) 异步触发，分两步：
    1. 若 stock_kline_daily 无该股日线，自动拉取近 5 年前复权日 K 线落库；
    2. 若该股为 A 股（非 ETF）且 stock_financial_statements 无财报，自动拉三表 + 张氏排雷计算落库。
ETF 跳过三表体检（无企业三表）。
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.db.session import AsyncSessionLocal
from app.models import StockFinancialStatement, StockKlineDaily
from app.models.financial import ALL_MONEY_FIELDS
from app.adapters.ths_client import is_fund_ticker

logger = logging.getLogger(__name__)

CN_TZ = timezone(timedelta(hours=8))


async def _has_kline(code: str) -> bool:
    async with AsyncSessionLocal() as session:
        cnt = await session.scalar(
            select(func.count()).select_from(StockKlineDaily).where(StockKlineDaily.code == code)
        )
    return bool(cnt)


async def _has_financial(code: str) -> bool:
    async with AsyncSessionLocal() as session:
        cnt = await session.scalar(
            select(func.count()).select_from(StockFinancialStatement).where(StockFinancialStatement.code == code)
        )
    return bool(cnt)


async def ensure_kline(code: str, years: int = 5) -> int:
    """若该股无日线，拉近 N 年前复权日 K 线落库，返回写入行数。"""
    if await _has_kline(code):
        return 0
    from app.services import kline_sync

    now = datetime.now(CN_TZ)
    start_ms = int((now - timedelta(days=365 * years)).timestamp() * 1000)
    end_ms = int(now.timestamp() * 1000)
    try:
        if is_fund_ticker(code):
            return await kline_sync.sync_etf_klines_range(code, start_ms, end_ms)
        return await kline_sync.sync_stock_klines([code], start_ms, end_ms)
    except Exception as exc:  # noqa: BLE001
        logger.warning("onboard 日线拉取失败 %s：%s", code, exc)
        return 0


async def _prewarm_financial_one(code: str) -> bool:
    """拉三表 + analyze + 落库，返回是否成功写入。"""
    from app.adapters import data_router
    from app.services import financial_analysis

    items = await data_router.get_financial_statements(code, "annual", 5)
    if not items:
        return False
    financial_analysis.analyze(items)

    table = StockFinancialStatement.__table__
    async with AsyncSessionLocal() as session:
        for item in items:
            row = {
                "code": item.get("code"),
                "report_date": item.get("report_date"),
                "report_label": item.get("report_label"),
                "report_type": item.get("report_type"),
                "source": item.get("source"),
                "derived_metrics": item.get("derived_metrics") or {},
            }
            for field in ALL_MONEY_FIELDS:
                if field in item and item[field] is not None:
                    row[field] = item[field]
            statement = pg_insert(table).values(row)
            updatable = [c.name for c in table.columns if c.name not in {"code", "report_date"}]
            statement = statement.on_conflict_do_update(
                index_elements=["code", "report_date"],
                set_={name: statement.excluded[name] for name in updatable},
            )
            await session.execute(statement)
        await session.commit()
    return True


async def ensure_financial(code: str) -> bool:
    """若 A 股无财报，拉三表体检落库。ETF 返回 False。返回是否本次写入。"""
    if is_fund_ticker(code):
        return False
    if await _has_financial(code):
        return False
    try:
        return await _prewarm_financial_one(code)
    except Exception as exc:  # noqa: BLE001
        logger.warning("onboard 财报体检失败 %s：%s", code, exc)
        return False


async def auto_onboard_stock(code: str) -> dict:
    """新股票自动入驻：日线 + 财报体检，返回结果摘要。"""
    normalized = "".join(ch for ch in str(code) if ch.isdigit())[-6:]
    if len(normalized) != 6:
        return {"code": code, "ok": False, "reason": "无效代码"}
    kline_rows = await ensure_kline(normalized)
    financial_done = await ensure_financial(normalized)
    return {
        "code": normalized,
        "ok": True,
        "kline_rows": kline_rows,
        "financial_done": financial_done,
    }
