"""资金流向 / 龙虎榜采集服务。

    * stock_individual_fund_flow()         -> 单股资金流序列（近 120 个交易日）
    * stock_individual_fund_flow_rank()    -> 全市场主力净流入排行（今日/5日/10日）
    * stock_lhb_detail_em()                -> 龙虎榜每日汇总
    * stock_lhb_stock_detail_em()          -> 龙虎榜个股买卖前五席位明细

    关键约束：
    1. akshare 是同步阻塞库，所有调用经 asyncio.to_thread 放入线程池。
    2. 严禁遍历全市场，仅对持仓/自选（约 20~30 只）逐只拉取，并用信号量 + 间隔防封 IP。
    3. NaN / Inf / '-' 彻底清洗为 None；ON CONFLICT DO UPDATE 幂等写入。
"""

from __future__ import annotations

import asyncio
import logging
import math
import re
from datetime import date, datetime, timedelta, timezone
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import AsyncSessionLocal
from app.models import (
    Position,
    StockFundFlowDaily,
    StockLhbDaily,
    WatchlistItem,
)
from app.services.jobs import JobState

logger = logging.getLogger(__name__)

# 逐只拉取防封：并发上限 + 单只间隔
FETCH_CONCURRENCY = 4
FETCH_INTERVAL = 0.3

_NULL_TOKENS = {"", "-", "--", "—", "nan", "none", "null", "n/a", "无", "无数据"}

# stock_individual_fund_flow 中文列 -> 目标字段
FUND_FLOW_COL_MAP = {
    "日期": "trade_date",
    "收盘价": "close_price",
    "涨跌幅": "change_pct",
    "主力净流入-净额": "main_net_inflow",
    "主力净流入-净占比": "main_net_ratio",
    "超大单净流入-净额": "super_net_inflow",
    "大单净流入-净额": "large_net_inflow",
    "中单净流入-净额": "medium_net_inflow",
    "小单净流入-净额": "small_net_inflow",
}

# 龙虎榜汇总列
LHB_COL_MAP = {
    "代码": "code",
    "名称": "stock_name",
    "上榜日": "trade_date",
    "龙虎榜净买额": "lhb_net_buy",
    "龙虎榜买入额": "lhb_buy_amount",
    "龙虎榜卖出额": "lhb_sell_amount",
    "成交额占总成交比": "turnover_ratio",
    "上榜原因": "reason",
}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _clean_scalar(value: Any) -> Any:
    if value is None:
        return None
    if hasattr(value, "item"):
        try:
            value = value.item()
        except Exception:  # noqa: BLE001
            pass
    if isinstance(value, bool):
        return value
    if isinstance(value, float):
        if math.isnan(value) or math.isinf(value):
            return None
        return value
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        text = value.strip()
        if text.lower() in _NULL_TOKENS:
            return None
        return text
    try:
        number = float(value)
    except (TypeError, ValueError):
        return str(value)
    if math.isnan(number) or math.isinf(number):
        return None
    return number


def _to_float(value: Any) -> float | None:
    value = _clean_scalar(value)
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value.replace(",", "").replace("%", ""))
        except ValueError:
            return None
    return None


def _to_date(value: Any) -> date | None:
    value = _clean_scalar(value)
    if value is None:
        return None
    if isinstance(value, (datetime, date)):
        return value.date() if isinstance(value, datetime) else value
    text = str(value).strip()[:10].replace("/", "-")
    try:
        return date.fromisoformat(text)
    except ValueError:
        return None


def _normalize_code(value: Any) -> str | None:
    text = _clean_scalar(value)
    if text is None:
        return None
    digits = "".join(ch for ch in str(text) if ch.isdigit())
    if len(digits) < 6:
        return digits.zfill(6) if digits else None
    return digits[-6:]


def _market_of(code: str) -> str:
    """6 位代码 -> 个股资金流接口所需市场（sh/sz/bj）。"""
    if code[:2] in {"43", "83", "87", "92"}:
        return "bj"
    return "sh" if code[0] in {"6", "5", "9"} else "sz"


def _tag_seat(name: str) -> str:
    if "机构专用" in name:
        return "机构专用"
    if "股通专用" in name:
        return "北向"
    return "游资"


# --------------------------------------------------------------------------- #
# 阻塞抓取（线程池内执行）
# --------------------------------------------------------------------------- #
def _fetch_individual_fund_flow(code: str, market: str) -> Any:
    import akshare as ak

    return ak.stock_individual_fund_flow(stock=code, market=market)


def _fetch_fund_flow_rank(indicator: str) -> Any:
    import akshare as ak

    return ak.stock_individual_fund_flow_rank(indicator=indicator)


def _fetch_lhb_detail(start: str, end: str) -> Any:
    import akshare as ak

    return ak.stock_lhb_detail_em(start_date=start, end_date=end)


def _fetch_lhb_stock_detail(symbol: str, day: str, flag: str) -> Any:
    import akshare as ak

    return ak.stock_lhb_stock_detail_em(symbol=symbol, date=day, flag=flag)


# --------------------------------------------------------------------------- #
# 数据清洗与入库
# --------------------------------------------------------------------------- #
def _parse_fund_flow_rows(df: Any, code: str) -> list[dict[str, Any]]:
    df = df.where(df.notna(), None)
    rows: list[dict[str, Any]] = []
    for record in df.to_dict("records"):
        trade_date = _to_date(record.get("日期"))
        if trade_date is None:
            continue
        row: dict[str, Any] = {"code": code, "trade_date": trade_date}
        for column, target in FUND_FLOW_COL_MAP.items():
            if target in ("code", "trade_date"):
                continue
            row[target] = _to_float(record.get(column))
        rows.append(row)
    return rows


def _parse_lhb_rows(df: Any) -> list[dict[str, Any]]:
    df = df.where(df.notna(), None)
    rows: list[dict[str, Any]] = []
    for record in df.to_dict("records"):
        code = _normalize_code(record.get("代码"))
        trade_date = _to_date(record.get("上榜日"))
        reason = _clean_scalar(record.get("上榜原因"))
        if not code or trade_date is None or not reason:
            continue
        row: dict[str, Any] = {"code": code, "trade_date": trade_date, "reason": str(reason)}
        for column, target in LHB_COL_MAP.items():
            if target in ("code", "trade_date", "reason"):
                continue
            row[target] = _to_float(record.get(column)) if target != "stock_name" else _clean_scalar(record.get(column))
        rows.append(row)
    return rows


def _parse_seat_rows(df: Any) -> list[dict[str, Any]]:
    """席位明细 -> [{seat, buy, sell, net, tag}]。"""
    df = df.where(df.notna(), None)
    seats: list[dict[str, Any]] = []
    for record in df.to_dict("records"):
        name = _clean_scalar(record.get("交易营业部名称"))
        if not name:
            continue
        seats.append(
            {
                "seat": str(name),
                "buy": _to_float(record.get("买入金额")),
                "sell": _to_float(record.get("卖出金额")),
                "net": _to_float(record.get("净额")),
                "tag": _tag_seat(str(name)),
            }
        )
    return seats


async def _upsert_fund_flow(session: AsyncSession, rows: list[dict[str, Any]]) -> int:
    if not rows:
        return 0
    table = StockFundFlowDaily.__table__
    statement = pg_insert(table).values(rows)
    set_map = {
        "close_price": statement.excluded.close_price,
        "change_pct": statement.excluded.change_pct,
        "main_net_inflow": statement.excluded.main_net_inflow,
        "main_net_ratio": statement.excluded.main_net_ratio,
        "super_net_inflow": statement.excluded.super_net_inflow,
        "large_net_inflow": statement.excluded.large_net_inflow,
        "medium_net_inflow": statement.excluded.medium_net_inflow,
        "small_net_inflow": statement.excluded.small_net_inflow,
        "updated_at": func.now(),
    }
    await session.execute(
        statement.on_conflict_do_update(index_elements=["code", "trade_date"], set_=set_map)
    )
    return len(rows)


async def _upsert_lhb(session: AsyncSession, rows: list[dict[str, Any]]) -> int:
    if not rows:
        return 0
    table = StockLhbDaily.__table__
    statement = pg_insert(table).values(rows)
    set_map = {
        "stock_name": statement.excluded.stock_name,
        "lhb_net_buy": statement.excluded.lhb_net_buy,
        "lhb_buy_amount": statement.excluded.lhb_buy_amount,
        "lhb_sell_amount": statement.excluded.lhb_sell_amount,
        "turnover_ratio": statement.excluded.turnover_ratio,
        "updated_at": func.now(),
    }
    await session.execute(
        statement.on_conflict_do_update(
            index_elements=["code", "trade_date", "reason"], set_=set_map
        )
    )
    return len(rows)


async def fetch_and_store_fund_flow(session: AsyncSession, code: str) -> int:
    """拉取单股资金流序列并幂等入库，返回写入行数。"""
    market = _market_of(code)
    try:
        df = await asyncio.to_thread(_fetch_individual_fund_flow, code, market)
    except Exception as exc:  # noqa: BLE001
        logger.warning("资金流拉取失败 %s: %s", code, exc)
        return 0
    rows = _parse_fund_flow_rows(df, code)
    return await _upsert_fund_flow(session, rows)


# --------------------------------------------------------------------------- #
# 持仓 + 自选资金流同步（后台单例）
# --------------------------------------------------------------------------- #
fund_flow_job = JobState()


def launch_fund_flow_sync() -> JobState:
    fund_flow_job.running = True
    fund_flow_job.phase = "syncing"
    fund_flow_job.label = "同步持仓/自选资金流"
    fund_flow_job.done = 0
    fund_flow_job.total = 0
    fund_flow_job.message = ""
    fund_flow_job.started_at = _now_iso()
    fund_flow_job.finished_at = None
    fund_flow_job.error = ""
    asyncio.create_task(run_fund_flow_sync())
    return fund_flow_job


async def _collect_portfolio_codes(session: AsyncSession) -> list[str]:
    codes: set[str] = set()
    for code in (await session.scalars(select(Position.code))).all():
        normalized = _normalize_code(code)
        if normalized:
            codes.add(normalized)
    for code in (await session.scalars(select(WatchlistItem.stock_code))).all():
        normalized = _normalize_code(code)
        if normalized:
            codes.add(normalized)
    return sorted(codes)


async def run_fund_flow_sync() -> None:
    fund_flow_job.running = True
    fund_flow_job.phase = "syncing"
    fund_flow_job.label = "同步持仓/自选资金流"
    fund_flow_job.done = 0
    fund_flow_job.total = 0
    fund_flow_job.message = ""
    fund_flow_job.started_at = _now_iso()
    fund_flow_job.finished_at = None
    fund_flow_job.error = ""

    try:
        async with AsyncSessionLocal() as session:
            codes = await _collect_portfolio_codes(session)
        if not codes:
            raise RuntimeError("当前没有持仓或自选股票")

        fund_flow_job.total = len(codes)
        fund_flow_job.message = f"共 {len(codes)} 只，逐只拉取近 120 日资金流…"

        semaphore = asyncio.Semaphore(FETCH_CONCURRENCY)
        total_written = 0

        async def one(code: str) -> None:
            nonlocal total_written
            async with semaphore:
                async with AsyncSessionLocal() as session:
                    written = await fetch_and_store_fund_flow(session, code)
                    await session.commit()
                total_written += written
                fund_flow_job.done += 1
                fund_flow_job.message = f"已同步 {fund_flow_job.done}/{fund_flow_job.total}（{code}）"
                await asyncio.sleep(FETCH_INTERVAL)

        # 串行 + 信号量：这里并发度由信号量控制
        await asyncio.gather(*(one(code) for code in codes))

        fund_flow_job.phase = "done"
        fund_flow_job.label = "完成"
        fund_flow_job.done = len(codes)
        fund_flow_job.message = f"已同步 {len(codes)} 只，写入 {total_written} 条资金流记录"
    except Exception as exc:  # noqa: BLE001
        logger.exception("fund-flow 同步失败")
        fund_flow_job.phase = "error"
        fund_flow_job.label = "失败"
        fund_flow_job.error = str(exc) or "同步失败"
        fund_flow_job.message = fund_flow_job.error
    finally:
        fund_flow_job.running = False
        fund_flow_job.finished_at = _now_iso()


# --------------------------------------------------------------------------- #
# 单股资金流 / 龙虎榜查询（按需补拉）
# --------------------------------------------------------------------------- #
async def get_stock_fund_flow_data(code: str, *, refresh: bool = False) -> dict[str, Any]:
    async with AsyncSessionLocal() as session:
        latest = await session.scalar(
            select(func.max(StockFundFlowDaily.trade_date)).where(
                StockFundFlowDaily.code == code
            )
        )
        stale = latest is None or (date.today() - latest).days > 5
        if refresh or stale:
            written = await fetch_and_store_fund_flow(session, code)
            await session.commit()
            if written == 0 and latest is None:
                # 仍无数据（可能已退市/北交所无此接口）
                pass

        flow_rows = (
            await session.scalars(
                select(StockFundFlowDaily)
                .where(StockFundFlowDaily.code == code)
                .order_by(StockFundFlowDaily.trade_date.desc())
                .limit(60)
            )
        ).all()
        lhb_rows = (
            await session.scalars(
                select(StockLhbDaily)
                .where(StockLhbDaily.code == code)
                .order_by(StockLhbDaily.trade_date.desc())
                .limit(30)
            )
        ).all()

        # 懒加载席位明细：同步阶段只抓了持仓/自选上榜个股，此处按需补拉
        need_seats = [
            r
            for r in lhb_rows
            if not (r.seat_details or {}).get("buy") and not (r.seat_details or {}).get("sell")
        ][:3]
        if need_seats:
            for r in need_seats:
                await _fetch_and_store_seat_details(session, r.code, r.trade_date, r.reason)
            await session.commit()
            lhb_rows = (
                await session.scalars(
                    select(StockLhbDaily)
                    .where(StockLhbDaily.code == code)
                    .order_by(StockLhbDaily.trade_date.desc())
                    .limit(30)
                )
            ).all()

        return {
            "code": code,
            "fundFlow": [_fund_flow_to_dict(r) for r in reversed(flow_rows)],
            "lhb": [_lhb_to_dict(r) for r in lhb_rows],
        }


def _fund_flow_to_dict(r: StockFundFlowDaily) -> dict[str, Any]:
    return {
        "tradeDate": r.trade_date.isoformat(),
        "closePrice": float(r.close_price) if r.close_price is not None else None,
        "changePct": float(r.change_pct) if r.change_pct is not None else None,
        "mainNetInflow": float(r.main_net_inflow) if r.main_net_inflow is not None else None,
        "mainNetRatio": float(r.main_net_ratio) if r.main_net_ratio is not None else None,
        "superNetInflow": float(r.super_net_inflow) if r.super_net_inflow is not None else None,
        "largeNetInflow": float(r.large_net_inflow) if r.large_net_inflow is not None else None,
        "mediumNetInflow": float(r.medium_net_inflow) if r.medium_net_inflow is not None else None,
        "smallNetInflow": float(r.small_net_inflow) if r.small_net_inflow is not None else None,
    }


def _lhb_to_dict(r: StockLhbDaily) -> dict[str, Any]:
    return {
        "code": r.code,
        "tradeDate": r.trade_date.isoformat(),
        "reason": r.reason,
        "stockName": r.stock_name,
        "lhbNetBuy": float(r.lhb_net_buy) if r.lhb_net_buy is not None else None,
        "lhbBuyAmount": float(r.lhb_buy_amount) if r.lhb_buy_amount is not None else None,
        "lhbSellAmount": float(r.lhb_sell_amount) if r.lhb_sell_amount is not None else None,
        "turnoverRatio": float(r.turnover_ratio) if r.turnover_ratio is not None else None,
        "instNetBuy": float(r.inst_net_buy) if r.inst_net_buy is not None else None,
        "seatDetails": r.seat_details or {},
    }


# --------------------------------------------------------------------------- #
# 近 5 日主力摘要（供持仓/自选表格内嵌渲染）
# --------------------------------------------------------------------------- #
async def mini_summary(codes: list[str]) -> dict[str, Any]:
    normalized = [c for c in (_normalize_code(x) for x in codes) if c]
    result: dict[str, Any] = {"items": {}}
    if not normalized:
        return result

    async with AsyncSessionLocal() as session:
        flow_rows = (
            await session.scalars(
                select(StockFundFlowDaily)
                .where(StockFundFlowDaily.code.in_(normalized))
                .order_by(StockFundFlowDaily.code, StockFundFlowDaily.trade_date.desc())
            )
        ).all()
        lhb_codes = set(
            (
                await session.scalars(
                    select(StockLhbDaily.code)
                    .where(StockLhbDaily.code.in_(normalized))
                    .distinct()
                )
            ).all()
        )

    by_code: dict[str, list[StockFundFlowDaily]] = {}
    for row in flow_rows:
        by_code.setdefault(row.code, []).append(row)

    for code in normalized:
        rows = by_code.get(code, [])[:5]
        arr = [float(r.main_net_inflow) if r.main_net_inflow is not None else 0.0 for r in rows]
        arr = arr[::-1]  # 按时间升序
        result["items"][code] = {
            "mainNetInflow5": arr,
            "sum5": round(sum(arr), 2),
            "lhb": code in lhb_codes,
        }
    return result


# --------------------------------------------------------------------------- #
# 全市场主力资金排行
# --------------------------------------------------------------------------- #
async def market_rank(indicator: str) -> list[dict[str, Any]]:
    indicator = indicator if indicator in {"今日", "5日", "10日"} else "今日"
    df = await asyncio.to_thread(_fetch_fund_flow_rank, indicator)
    df = df.where(df.notna(), None)
    prefix = indicator
    out: list[dict[str, Any]] = []
    for record in df.to_dict("records"):
        code = _normalize_code(record.get("代码"))
        if not code:
            continue
        out.append(
            {
                "code": code,
                "name": _clean_scalar(record.get("名称")),
                "price": _to_float(record.get("最新价")),
                "changePct": _to_float(record.get(f"{prefix}涨跌幅")),
                "mainNetInflow": _to_float(record.get(f"{prefix}主力净流入-净额")),
                "mainNetRatio": _to_float(record.get(f"{prefix}主力净流入-净占比")),
                "superNetInflow": _to_float(record.get(f"{prefix}超大单净流入-净额")),
                "largeNetInflow": _to_float(record.get(f"{prefix}大单净流入-净额")),
                "mediumNetInflow": _to_float(record.get(f"{prefix}中单净流入-净额")),
                "smallNetInflow": _to_float(record.get(f"{prefix}小单净流入-净额")),
            }
        )
    return out


# --------------------------------------------------------------------------- #
# 龙虎榜同步（后台单例）
# --------------------------------------------------------------------------- #
lhb_job = JobState()


def launch_lhb_sync() -> JobState:
    lhb_job.running = True
    lhb_job.phase = "detail"
    lhb_job.label = "拉取龙虎榜汇总"
    lhb_job.done = 0
    lhb_job.total = 0
    lhb_job.message = ""
    lhb_job.started_at = _now_iso()
    lhb_job.finished_at = None
    lhb_job.error = ""
    asyncio.create_task(run_lhb_sync())
    return lhb_job


async def _fetch_and_store_seat_details(
    session: AsyncSession, code: str, trade_date: date, reason: str
) -> None:
    """拉取个股买卖前五席位明细，识别机构/北向，回写 seat_details 与 inst_net_buy。"""
    day_str = trade_date.strftime("%Y%m%d")
    buy_rows: list[dict[str, Any]] = []
    sell_rows: list[dict[str, Any]] = []
    for flag in ("买入", "卖出"):
        try:
            df = await asyncio.to_thread(_fetch_lhb_stock_detail, code, day_str, flag)
        except Exception as exc:  # noqa: BLE001
            logger.warning("席位明细拉取失败 %s %s %s: %s", code, day_str, flag, exc)
            continue
        seats = _parse_seat_rows(df)
        if flag == "买入":
            buy_rows = seats
        else:
            sell_rows = seats

    if not buy_rows and not sell_rows:
        return

    # 机构净买额 = 所有「机构专用」席位净额之和。
    # 同一席位可能在买卖两榜重复出现（名称+买卖金额完全相同），按三元组去重；
    # 「机构专用」为交易所匿名标签，不同机构同名，须按金额区分而非仅按名称去重。
    inst_sum = 0.0
    seen_keys: set[tuple] = set()
    for seat in buy_rows + sell_rows:
        if seat["tag"] != "机构专用":
            continue
        key = (seat["seat"], seat["buy"], seat["sell"])
        if key in seen_keys:
            continue
        seen_keys.add(key)
        if seat["net"] is not None:
            inst_sum += seat["net"]

    seat_details = {"buy": buy_rows, "sell": sell_rows}
    table = StockLhbDaily.__table__
    statement = pg_insert(table).values(
        {
            "code": code,
            "trade_date": trade_date,
            "reason": reason,
            "seat_details": seat_details,
            "inst_net_buy": round(inst_sum, 2) if inst_sum else None,
        }
    )
    set_map = {
        "seat_details": statement.excluded.seat_details,
        "inst_net_buy": statement.excluded.inst_net_buy,
        "updated_at": func.now(),
    }
    await session.execute(
        statement.on_conflict_do_update(
            index_elements=["code", "trade_date", "reason"], set_=set_map
        )
    )


async def run_lhb_sync() -> None:
    lhb_job.running = True
    lhb_job.phase = "detail"
    lhb_job.label = "拉取龙虎榜汇总"
    lhb_job.done = 0
    lhb_job.total = 0
    lhb_job.message = ""
    lhb_job.started_at = _now_iso()
    lhb_job.finished_at = None
    lhb_job.error = ""

    try:
        today = date.today()
        end = today.strftime("%Y%m%d")
        start = (today - timedelta(days=10)).strftime("%Y%m%d")

        async with AsyncSessionLocal() as session:
            portfolio_codes = set(await _collect_portfolio_codes(session))

        df = await asyncio.to_thread(_fetch_lhb_detail, start, end)
        lhb_rows = _parse_lhb_rows(df)
        if not lhb_rows:
            raise RuntimeError("近 10 日无龙虎榜数据")

        async with AsyncSessionLocal() as session:
            await _upsert_lhb(session, lhb_rows)
            await session.commit()

        # 席位明细：仅对持仓/自选上榜个股 + 全市场前若干（控制调用量）
        target_rows = [r for r in lhb_rows if r["code"] in portfolio_codes]
        lhb_job.phase = "seats"
        lhb_job.label = "拉取席位明细"
        lhb_job.total = len(target_rows)
        lhb_job.message = f"上榜 {len(lhb_rows)} 条，其中持仓/自选 {len(target_rows)} 条需拉席位"

        semaphore = asyncio.Semaphore(FETCH_CONCURRENCY)

        async def one_seat(row: dict[str, Any]) -> None:
            async with semaphore:
                async with AsyncSessionLocal() as session:
                    await _fetch_and_store_seat_details(
                        session, row["code"], row["trade_date"], row["reason"]
                    )
                    await session.commit()
                lhb_job.done += 1
                await asyncio.sleep(FETCH_INTERVAL)

        await asyncio.gather(*(one_seat(row) for row in target_rows))

        lhb_job.phase = "done"
        lhb_job.label = "完成"
        lhb_job.message = f"已同步 {len(lhb_rows)} 条龙虎榜，{len(target_rows)} 条席位明细"
    except Exception as exc:  # noqa: BLE001
        logger.exception("lhb 同步失败")
        lhb_job.phase = "error"
        lhb_job.label = "失败"
        lhb_job.error = str(exc) or "同步失败"
        lhb_job.message = lhb_job.error
    finally:
        lhb_job.running = False
        lhb_job.finished_at = _now_iso()


async def lhb_daily(inst_only: bool = False) -> dict[str, Any]:
    """查询龙虎榜每日总览（含机构净买筛选）。"""
    async with AsyncSessionLocal() as session:
        stmt = select(StockLhbDaily).order_by(
            StockLhbDaily.trade_date.desc(), StockLhbDaily.lhb_net_buy.desc()
        )
        if inst_only:
            stmt = stmt.where(StockLhbDaily.inst_net_buy.is_not(None))
        rows = (await session.scalars(stmt.limit(500))).all()
    return {"rows": [_lhb_to_dict(r) for r in rows]}
