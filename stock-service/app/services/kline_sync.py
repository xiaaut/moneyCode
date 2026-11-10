"""全市场日 K 线 Parquet 流式入库 + ETF 前复权日线补偿。

数据源：
- A 股全市场：GET /api/dump/market-dumps/{daily-k,daily-k-10d}/download-url 拿预签名链接，
  再流式下载 Parquet，用 pyarrow iter_batches 分块读取，分批 ON CONFLICT 落库（严禁全量 load 进内存）。
- 场内 ETF：GET /api/fund/market/historical（前复权日线，单只 5 年）。

字段映射（Parquet 日 K → stock_kline_daily）：
    thscode→code, date_ms→trade_date, open/high/low/close_price→open/high/low/close,
    volume→volume, turnover→amount, close→qfq_close(未复权暂代), adjust_factor=1.0。
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import httpx
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.core.config import settings
from app.db.session import AsyncSessionLocal
from app.models import StockKlineDaily
from app.adapters.ths_client import is_fund_ticker, ths_client, to_ths_code

logger = logging.getLogger(__name__)

CN_TZ = timezone(timedelta(hours=8))

DOWNLOAD_URLS = {
    "historical": "/api/dump/market-dumps/daily-k/download-url",
    "incremental": "/api/dump/market-dumps/daily-k-10d/download-url",
}

# 单批 upsert 行数（每行 10 字段，2500 行 = 25000 参数，规避 asyncpg 32767 上限）
UPSERT_BATCH = 2500
# iter_batches 读取块大小
PARQUET_BATCH = 50000

kline_job: dict[str, Any] = {
    "running": False,
    "phase": "idle",  # idle | download | ingest | done | error
    "label": "空闲",
    "done": 0,
    "total": 0,
    "message": "",
    "error": "",
    "started_at": None,
    "finished_at": None,
}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _code_from_ths(thscode: Any) -> str:
    return "".join(ch for ch in str(thscode or "") if ch.isdigit())[-6:]


def _ms_to_date(ms: Any) -> Any:
    try:
        return datetime.fromtimestamp(int(ms) / 1000, tz=CN_TZ).date()
    except (TypeError, ValueError, OSError):
        return None


def _f(value: Any) -> float | None:
    if value is None:
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return None if result != result else result  # NaN → None


def _map_batch(batch: Any) -> list[dict[str, Any]]:
    """pyarrow batch → stock_kline_daily 行列表。"""
    data = batch.to_pydict()
    n = len(data["thscode"])
    rows: list[dict[str, Any]] = []
    for i in range(n):
        code = _code_from_ths(data["thscode"][i])
        trade_date = _ms_to_date(data["date_ms"][i])
        if not code or trade_date is None:
            continue
        close = _f(data["close_price"][i])
        rows.append(
            {
                "code": code,
                "trade_date": trade_date,
                "open": _f(data["open_price"][i]),
                "high": _f(data["high_price"][i]),
                "low": _f(data["low_price"][i]),
                "close": close,
                "volume": _f(data["volume"][i]),
                "amount": _f(data["turnover"][i]),
                "adjust_factor": 1.0,
                "qfq_close": close,
            }
        )
    return rows


# --------------------------------------------------------------------------- #
# 下载与解析
# --------------------------------------------------------------------------- #
async def _get_download_url(kind: str) -> str:
    path = DOWNLOAD_URLS[kind]
    url = f"{settings.THS_BASE_URL}{path}"
    async with httpx.AsyncClient(timeout=httpx.Timeout(30, connect=8)) as client:
        resp = await client.get(url, headers={"X-api-key": settings.THS_API_KEY})
        resp.raise_for_status()
        payload = resp.json()
    if payload.get("code") != 0:
        raise RuntimeError(f"获取下载链接失败 code={payload.get('code')} {payload.get('message')}")
    presigned = (payload.get("data") or {}).get("presigned_url")
    if not presigned:
        raise RuntimeError("下载链接缺失")
    return presigned


async def _download_to_tmp(url: str, dest: str) -> None:
    async with httpx.AsyncClient(timeout=httpx.Timeout(600, connect=15)) as client:
        async with client.stream("GET", url) as resp:
            resp.raise_for_status()
            with open(dest, "wb") as handle:
                async for chunk in resp.aiter_bytes(chunk_size=1 << 20):
                    handle.write(chunk)


def _iter_chunks(path: str) -> Any:
    """同步迭代器：逐批读取 parquet 并映射为行列表。"""
    import pyarrow.parquet as pq

    pf = pq.ParquetFile(path)
    for batch in pf.iter_batches(batch_size=PARQUET_BATCH):
        yield _map_batch(batch)


async def _read_chunk(iterator: Any) -> list[dict[str, Any]]:
    try:
        return next(iterator)
    except StopIteration:
        return []


async def _upsert_chunk(session: Any, rows: list[dict[str, Any]]) -> int:
    table = StockKlineDaily.__table__
    statement = pg_insert(table).values(rows)
    statement = statement.on_conflict_do_update(
        index_elements=["code", "trade_date"],
        set_={
            "open": statement.excluded.open,
            "high": statement.excluded.high,
            "low": statement.excluded.low,
            "close": statement.excluded.close,
            "volume": statement.excluded.volume,
            "amount": statement.excluded.amount,
            "adjust_factor": statement.excluded.adjust_factor,
            "qfq_close": statement.excluded.qfq_close,
            "updated_at": __import__("sqlalchemy").func.now(),
        },
    )
    await session.execute(statement)
    return len(rows)


async def _ingest_parquet(path: str) -> int:
    """流式分块入库，返回总入库行数。"""
    iterator = _iter_chunks(path)
    inserted = 0
    async with AsyncSessionLocal() as session:
        while True:
            rows = await asyncio.to_thread(_read_chunk, iterator)
            if not rows:
                break
            for i in range(0, len(rows), UPSERT_BATCH):
                chunk = rows[i : i + UPSERT_BATCH]
                inserted += await _upsert_chunk(session, chunk)
            await session.commit()
            kline_job["done"] = inserted
    return inserted


# --------------------------------------------------------------------------- #
# 同步任务
# --------------------------------------------------------------------------- #
async def _run_sync(kind: str) -> None:
    from sqlalchemy import func

    global kline_job
    kline_job.update(
        {
            "running": True,
            "phase": "download",
            "label": "获取下载链接并下载 Parquet",
            "done": 0,
            "total": 0,
            "message": "",
            "error": "",
            "started_at": _now_iso(),
            "finished_at": None,
        }
    )
    tmp = f"/tmp/fuyao_{kind}.parquet"
    try:
        presigned = await _get_download_url(kind)
        await _download_to_tmp(presigned, tmp)
        kline_job["phase"] = "ingest"
        kline_job["label"] = "分块解析入库"
        inserted = await _ingest_parquet(tmp)
        kline_job["phase"] = "done"
        kline_job["label"] = "完成"
        kline_job["done"] = inserted
        kline_job["total"] = inserted
        kline_job["message"] = f"入库 {inserted} 行"
    except Exception as exc:  # noqa: BLE001
        logger.exception("K 线同步失败")
        kline_job["phase"] = "error"
        kline_job["label"] = "失败"
        kline_job["error"] = str(exc)[:200]
    finally:
        kline_job["running"] = False
        kline_job["finished_at"] = _now_iso()
        Path(tmp).unlink(missing_ok=True)


def launch_sync(kind: str) -> dict[str, Any]:
    """启动后台同步（historical / incremental），重入返回 None。"""
    if kline_job["running"]:
        return kline_job
    asyncio.create_task(_run_sync(kind))
    return kline_job


# --------------------------------------------------------------------------- #
# 持仓/自选日线同步（ETF 走 fund-market，A 股走 historical-batch）
# --------------------------------------------------------------------------- #
async def sync_etf_klines_range(code: str, start_ms: int, end_ms: int) -> int:
    """拉取单只 ETF 指定时间窗口的前复权日线并写入，返回行数。"""
    thscode = to_ths_code(code)
    url = f"{settings.THS_BASE_URL}/api/fund/market/historical"
    params = {"thscode": thscode, "interval": "1d", "start": start_ms, "end": end_ms}
    async with httpx.AsyncClient(timeout=httpx.Timeout(30, connect=8)) as client:
        resp = await client.get(url, params=params, headers={"X-api-key": settings.THS_API_KEY})
        resp.raise_for_status()
        payload = resp.json()

    if payload.get("code") != 0:
        raise RuntimeError(f"ETF 日线拉取失败 code={payload.get('code')} {payload.get('message')}")

    items = ((payload.get("data") or {}).get("item")) or []
    rows: list[dict[str, Any]] = []
    for item in items:
        trade_date = _ms_to_date(item.get("date_ms"))
        if trade_date is None:
            continue
        close = _f(item.get("close_price"))
        rows.append(
            {
                "code": code,
                "trade_date": trade_date,
                "open": _f(item.get("open_price")),
                "high": _f(item.get("high_price")),
                "low": _f(item.get("low_price")),
                "close": close,
                "volume": _f(item.get("volume")),
                "amount": _f(item.get("turnover")),
                "adjust_factor": 1.0,
                "qfq_close": close,  # fund-market 已是前复权口径
            }
        )

    if not rows:
        return 0

    inserted = 0
    async with AsyncSessionLocal() as session:
        for i in range(0, len(rows), UPSERT_BATCH):
            chunk = rows[i : i + UPSERT_BATCH]
            inserted += await _upsert_chunk(session, chunk)
        await session.commit()
    return inserted


async def sync_etf_klines(code: str, years: int = 5) -> int:
    """拉取单只 ETF 近 N 年前复权日线并写入，返回行数（兼容旧签名）。"""
    now = datetime.now(CN_TZ)
    start_ms = int((now - timedelta(days=365 * years)).timestamp() * 1000)
    end_ms = int(now.timestamp() * 1000)
    return await sync_etf_klines_range(code, start_ms, end_ms)


async def sync_stock_klines(codes: list[str], start_ms: int, end_ms: int) -> int:
    """批量拉取 A 股历史日线（adjust=forward 前复权）并写入，返回入库行数。"""
    if not codes:
        return 0
    grouped = await ths_client.get_historical_batch(codes, start_ms, end_ms)

    rows: list[dict[str, Any]] = []
    for code in codes:
        for item in grouped.get(code, []):
            trade_date = _ms_to_date(item.get("date_ms"))
            if trade_date is None:
                continue
            close = _f(item.get("close_price"))
            rows.append(
                {
                    "code": code,
                    "trade_date": trade_date,
                    "open": _f(item.get("open_price")),
                    "high": _f(item.get("high_price")),
                    "low": _f(item.get("low_price")),
                    "close": close,
                    "volume": _f(item.get("volume")),
                    "amount": _f(item.get("turnover")),
                    "adjust_factor": 1.0,
                    "qfq_close": close,  # adjust=forward 已是前复权
                }
            )

    if not rows:
        return 0

    inserted = 0
    async with AsyncSessionLocal() as session:
        for i in range(0, len(rows), UPSERT_BATCH):
            chunk = rows[i : i + UPSERT_BATCH]
            inserted += await _upsert_chunk(session, chunk)
        await session.commit()
    return inserted


async def _portfolio_codes() -> tuple[list[str], list[str]]:
    """返回 (stocks, funds)：持仓 + 自选去重后按 ETF/A 股分流。"""
    from sqlalchemy import select

    from app.models import Position, WatchlistItem

    async with AsyncSessionLocal() as session:
        positions = (await session.scalars(select(Position.code))).all()
        watchlist = (await session.scalars(select(WatchlistItem.stock_code))).all()

    codes = list(dict.fromkeys([*(positions or []), *(watchlist or [])]))
    codes = [c for c in codes if c and len(c) == 6]
    stocks = [c for c in codes if not is_fund_ticker(c)]
    funds = [c for c in codes if is_fund_ticker(c)]
    return stocks, funds


async def sync_portfolio_klines(years: int = 5) -> dict[str, Any]:
    """全量同步持仓 + 自选标的近 N 年前复权日线，返回统计。"""
    stocks, funds = await _portfolio_codes()
    now = datetime.now(CN_TZ)
    start_ms = int((now - timedelta(days=365 * years)).timestamp() * 1000)
    end_ms = int(now.timestamp() * 1000)

    total = 0
    detail: dict[str, Any] = {
        "total_codes": len(stocks) + len(funds),
        "stocks": len(stocks),
        "funds": len(funds),
        "stock_rows": 0,
        "fund_rows": 0,
    }
    if stocks:
        detail["stock_rows"] = await sync_stock_klines(stocks, start_ms, end_ms)
        total += detail["stock_rows"]
    for code in funds:
        try:
            n = await sync_etf_klines_range(code, start_ms, end_ms)
            detail["fund_rows"] += n
            total += n
        except Exception as exc:  # noqa: BLE001
            logger.warning("ETF %s 日线同步失败：%s", code, exc)
    detail["inserted"] = total
    return detail


async def sync_portfolio_incremental(lookback_days: int = 15) -> int:
    """收盘一键更新用：增量抓取持仓 + 自选最近约 10 个交易日日线，返回入库行数。"""
    stocks, funds = await _portfolio_codes()
    now = datetime.now(CN_TZ)
    start_ms = int((now - timedelta(days=lookback_days)).timestamp() * 1000)
    end_ms = int(now.timestamp() * 1000)

    total = 0
    if stocks:
        total += await sync_stock_klines(stocks, start_ms, end_ms)
    for code in funds:
        try:
            total += await sync_etf_klines_range(code, start_ms, end_ms)
        except Exception as exc:  # noqa: BLE001
            logger.warning("ETF %s 增量日线失败：%s", code, exc)
    return total
