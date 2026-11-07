"""AKShare 全市场同步引擎。

    * stock_zh_a_spot_em()   -> 5300+ 只 A 股实时行情与估值（现价/涨跌幅/PE/PB/市值/换手…）
    * stock_yjbb_em(date=…)  -> 最新季度业绩报表（ROE / EPS / 营收与净利同比…）

    关键约束：
    1. akshare 是同步阻塞库，所有抓取都经 asyncio.to_thread 放入线程池执行，
       严禁在主事件循环中直接调用，避免阻塞 FastAPI。
    2. 数据入库前彻底清洗 NaN / Inf / '-' / None，规避 asyncpg / JSON 序列化报错。
    3. 分批 upsert（每批 <= 400 条），规避 asyncpg 32767 参数上限；ON CONFLICT (code)
       只更新行情与财报字段，controller / state_owned / in_msci 等库内既有字段
       不在 set 子句中，天然保留。
"""

from __future__ import annotations

import asyncio
import logging
import math
import re
from datetime import date, datetime, timezone
from typing import Any

from sqlalchemy import func
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import AsyncSessionLocal
from app.models import Stock
from app.services.jobs import JobState

logger = logging.getLogger(__name__)

# 分批 upsert 每批条数（严格 <= 400）
CHUNK_SIZE = 400

# 最近抓取几个财报期
REPORT_PERIODS_COUNT = 4

# 财报期 -> 季度标签
_QUARTER_END = {"0331": "Q1", "0630": "Q2", "0930": "Q3", "1231": "Q4"}

# stock_zh_a_spot_em 中文列 -> 目标字段
SPOT_COLUMN_MAP: dict[str, str] = {
    "代码": "code",
    "名称": "name",
    "最新价": "price",
    "涨跌幅": "change_pct",
    "成交量": "volume",
    "成交额": "turnover_amount",
    "换手率": "turnover_rate",
    "市盈率-动态": "pe_ttm",
    "市净率": "pb",
    "总市值": "total_mv",      # 元，入库时折算成 market_cap_yi（亿）
    "流通市值": "circ_mv",     # 元
    "60日涨跌幅": "change_60d",
    "年初至今涨跌幅": "change_ytd",
}

# stock_yjbb_em 中文列 -> 目标字段
FIN_COLUMN_MAP: dict[str, str] = {
    "股票代码": "code",
    "股票简称": "name",
    "每股收益": "eps_current",
    "营业总收入-营业总收入": "revenue",
    "营业总收入-同比增长": "revenue_yoy",
    "净利润-净利润": "net_profit",
    "净利润-同比增长": "net_profit_yoy",
    "净资产收益率": "roe",
    "销售毛利率": "gross_margin",
    "所处行业": "industry",
}

_NULL_TOKENS = {"", "-", "--", "—", "nan", "none", "null", "n/a", "无", "无数据"}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _clean_scalar(value: Any) -> Any:
    """把 numpy/pandas/Decimal/NaN/Inf/'-' 统一收敛为 None 或基础类型。"""
    if value is None:
        return None
    # numpy/pandas 标量 -> python 原生
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
    # Decimal 等其它类型，尝试转 float
    try:
        number = float(value)
    except (TypeError, ValueError):
        return str(value)
    if math.isnan(number) or math.isinf(number):
        return None
    return number


def _to_float(value: Any) -> float | None:
    """数值字段转换；非数值返回 None。"""
    value = _clean_scalar(value)
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        text = value.replace(",", "").replace("%", "").strip()
        try:
            return float(text)
        except ValueError:
            return None
    return None


def _to_str(value: Any) -> str | None:
    value = _clean_scalar(value)
    if value is None:
        return None
    return str(value).strip() or None


def _normalize_code(value: Any) -> str | None:
    """股票代码统一成 6 位数字（对齐库内 code 格式）。"""
    text = _to_str(value)
    if not text:
        return None
    digits = "".join(ch for ch in text if ch.isdigit())
    if len(digits) < 6:
        # 补齐前导 0（如 "1" -> "000001"）
        return digits.zfill(6) if digits else None
    return digits[-6:]


def _recent_report_dates(count: int = REPORT_PERIODS_COUNT) -> list[str]:
    """返回最近 count 个已结束的财报期（YYYYMMDD），最近的在前。"""
    today = date.today()
    candidates: set[date] = set()
    for year in range(today.year - 2, today.year + 1):
        for month, day in ((3, 31), (6, 30), (9, 30), (12, 31)):
            candidates.add(date(year, month, day))
    past = sorted(d for d in candidates if d <= today)
    recent = past[-count:] if len(past) >= count else past
    return [d.strftime("%Y%m%d") for d in reversed(recent)]


def _report_period_label(yyyymmdd: str) -> str:
    """'20260930' -> '2026Q3'。"""
    year = yyyymmdd[:4]
    mmdd = yyyymmdd[4:]
    quarter = _QUARTER_END.get(mmdd)
    if quarter:
        return f"{year}{quarter}"
    month = int(mmdd[:2]) if mmdd[:2].isdigit() else 1
    return f"{year}Q{(month - 1) // 3 + 1}"


# --------------------------------------------------------------------------- #
# AKShare 阻塞抓取（放进线程池）
# --------------------------------------------------------------------------- #
def _fetch_spot() -> Any:
    import akshare as ak

    return ak.stock_zh_a_spot_em()


def _fetch_yjbb(period: str) -> Any:
    import akshare as ak

    return ak.stock_yjbb_em(date=period)


async def fetch_single_stock_financials(code: str) -> dict[str, Any]:
    """拉取单股最新财报指标（eps/roe/营收净利同比/行业），跨最近 3 个财报期查找。

    返回 _build_fin_rows 的 row（不含 code 键），找不到时返回 {}。
    """
    for period in _recent_report_dates(3):
        try:
            df = await asyncio.to_thread(_fetch_yjbb, period)
        except Exception as exc:  # noqa: BLE001
            logger.warning("单股财报拉取失败 %s(%s): %s", code, period, exc)
            continue
        rows = _build_fin_rows(df, _report_period_label(period))
        if code in rows:
            return rows[code]
    return {}


# --------------------------------------------------------------------------- #
# 同步任务单例
# --------------------------------------------------------------------------- #
akshare_job = JobState()
akshare_meta: dict[str, Any] = {"updatedAt": None, "spotRows": 0, "finRows": 0}


def launch_akshare_sync() -> JobState:
    """同步置 running=true 后派发后台任务，保证 /sync-akshare 立即返回可见状态。"""
    akshare_job.running = True
    akshare_job.phase = "spot"
    akshare_job.label = "拉取全市场实时行情"
    akshare_job.done = 0
    akshare_job.total = 0
    akshare_job.message = ""
    akshare_job.started_at = _now_iso()
    akshare_job.finished_at = None
    akshare_job.error = ""
    asyncio.create_task(run_akshare_sync())
    return akshare_job


def _reset_job(phase: str, label: str) -> None:
    akshare_job.running = True
    akshare_job.phase = phase
    akshare_job.label = label
    akshare_job.done = 0
    akshare_job.total = 0
    akshare_job.message = ""
    akshare_job.started_at = _now_iso()
    akshare_job.finished_at = None
    akshare_job.error = ""


def _build_spot_rows(df: Any) -> list[dict[str, Any]]:
    df = df.where(df.notna(), None)  # NaN / pd.NA / NaT 统一置 None
    rows: list[dict[str, Any]] = []
    for record in df.to_dict("records"):
        code = _normalize_code(record.get("代码"))
        if not code:
            continue
        row: dict[str, Any] = {"code": code}
        for column, target in SPOT_COLUMN_MAP.items():
            if target in ("code",):
                continue
            row[target] = _to_float(record.get(column)) if target != "name" else _to_str(record.get(column))
        # 总市值（元）-> market_cap_yi（亿）
        total_mv = row.pop("total_mv", None)
        row["market_cap_yi"] = round(total_mv / 1e8, 4) if total_mv is not None else None
        if row.get("name"):
            rows.append(row)
    return rows


def _build_fin_rows(df: Any, period_label: str) -> dict[str, dict[str, Any]]:
    """返回 {code: {财报字段}}，按最新报告期优先由调用方决定覆盖。"""
    df = df.where(df.notna(), None)  # NaN / pd.NA / NaT 统一置 None
    result: dict[str, dict[str, Any]] = {}
    for record in df.to_dict("records"):
        code = _normalize_code(record.get("股票代码"))
        if not code:
            continue
        row: dict[str, Any] = {"report_period": period_label}
        for column, target in FIN_COLUMN_MAP.items():
            if target in ("code", "name"):
                continue
            if target == "industry":
                row[target] = _to_str(record.get(column))
            else:
                row[target] = _to_float(record.get(column))
        result[code] = row
    return result


async def _upsert_chunked(session: AsyncSession, rows: list[dict[str, Any]]) -> int:
    """分批 upsert，仅更新行情与财报字段，保留 controller/state_owned/in_msci。"""
    table = Stock.__table__
    # 会写入 / 更新的字段集合（显式列出，避免误覆盖主数据）
    writable = [
        "name", "price", "change_pct", "volume", "turnover_amount", "turnover_rate",
        "pe_ttm", "pb", "market_cap_yi", "circ_mv", "change_60d", "change_ytd",
        "roe", "eps_current", "revenue", "revenue_yoy", "net_profit",
        "net_profit_yoy", "gross_margin", "report_period", "industry",
    ]
    written = 0
    for start in range(0, len(rows), CHUNK_SIZE):
        chunk = rows[start : start + CHUNK_SIZE]
        keys = {key for row in chunk for key in row if key in writable or key == "code"}
        normalized = [{key: row.get(key) for key in keys} for row in chunk]
        statement = pg_insert(table).values(normalized)
        set_map = {
            # 名称与行业：新值优先，为空时保留库内旧值
            "name": func.coalesce(statement.excluded.name, table.c.name),
            "industry": func.coalesce(statement.excluded.industry, table.c.industry),
        }
        for column in writable:
            if column in ("name", "industry"):
                continue
            if column not in keys:
                continue
            # 行情 / 财务字段：新值优先，为空时保留旧值（避免把既有数据冲成 NULL）
            set_map[column] = func.coalesce(statement.excluded[column], table.c[column])
        set_map["updated_at"] = func.now()
        await session.execute(
            statement.on_conflict_do_update(index_elements=["code"], set_=set_map)
        )
        written += len(chunk)
    return written


async def run_akshare_sync() -> None:
    global akshare_meta
    _reset_job("spot", "拉取全市场实时行情")

    try:
        # 阶段 1：全市场实时行情
        spot_df = await asyncio.to_thread(_fetch_spot)
        spot_rows = _build_spot_rows(spot_df)
        akshare_job.message = f"已获取 {len(spot_rows)} 只 A 股实时行情"
        logger.info("AKShare spot 行情拉取完成：%d 只", len(spot_rows))
        if not spot_rows:
            raise RuntimeError("AKShare 实时行情为空，请检查网络或接口可用性")

        # 阶段 2：最近几个财报期业绩报表（最新报告期优先）
        akshare_job.phase = "financial"
        akshare_job.label = "拉取最新财报业绩"
        akshare_job.done = 0
        periods = _recent_report_dates(REPORT_PERIODS_COUNT)
        akshare_job.total = len(periods)
        akshare_job.message = f"待抓取财报期：{', '.join(periods)}"

        fin_by_code: dict[str, dict[str, Any]] = {}
        for index, period in enumerate(periods):
            try:
                fin_df = await asyncio.to_thread(_fetch_yjbb, period)
            except Exception as exc:  # noqa: BLE001 - 单个财报期失败不阻断整体
                logger.warning("财报期 %s 抓取失败：%s", period, exc)
                akshare_job.message = f"财报期 {period} 抓取失败（{type(exc).__name__}），继续下一个"
                continue
            period_rows = _build_fin_rows(fin_df, _report_period_label(period))
            for code, row in period_rows.items():
                # 最新报告期优先：已有数据则不再覆盖
                fin_by_code.setdefault(code, row)
            akshare_job.done = index + 1
            akshare_job.message = f"财报期 {period} 完成，累计 {len(fin_by_code)} 只有业绩数据"

        # 阶段 3：合并 + 清洗 + 分批入库
        akshare_job.phase = "save"
        akshare_job.label = "写入数据库"
        akshare_job.done = 0
        akshare_job.total = len(spot_rows)
        akshare_job.message = "正在合并并分批写入数据库…"

        merged: list[dict[str, Any]] = []
        fin_matched = 0
        for row in spot_rows:
            fin = fin_by_code.get(row["code"])
            if fin:
                merged.append({**row, **fin})
                fin_matched += 1
            else:
                merged.append(row)

        async with AsyncSessionLocal() as session:
            written = await _upsert_chunked(session, merged)
            await session.commit()

        akshare_meta = {
            "updatedAt": _now_iso(),
            "spotRows": len(spot_rows),
            "finRows": fin_matched,
            "written": written,
        }

        akshare_job.phase = "done"
        akshare_job.label = "完成"
        akshare_job.done = written
        akshare_job.total = written
        akshare_job.message = (
            f"已同步 {written} 只（行情 {len(spot_rows)} 只，含财报业绩 {fin_matched} 只）"
        )
        logger.info("AKShare 全市场同步完成：%d 只入库（含财报 %d 只）", written, fin_matched)
    except Exception as exc:  # noqa: BLE001
        logger.exception("akshare 同步任务失败")
        akshare_job.phase = "error"
        akshare_job.label = "失败"
        akshare_job.error = str(exc) or "同步失败"
        akshare_job.message = akshare_job.error
    finally:
        akshare_job.running = False
        akshare_job.finished_at = _now_iso()
