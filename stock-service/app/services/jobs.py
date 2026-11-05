"""后台抓取任务（单例 + 进度状态机）。

    * StateOwnedJob —— 全市场国企扫描（公司概况 -> 行情校验 -> 央企名录 -> 同花顺穿透）
    * MsciAnalyzeJob —— MSCI 成份股补全行业 / PE / ROE

两者都是模块级单例，进程内唯一；接口用 asyncio.create_task 触发，前端轮询 /progress。
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from datetime import datetime, timezone
from typing import Any

import httpx
from pydantic import BaseModel, ConfigDict
from pydantic.alias_generators import to_camel
from sqlalchemy import func, select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import AsyncSessionLocal
from app.models import MsciMeta, Stock
from app.services import ths as ths_mod
from app.services.quotes import quote_service

logger = logging.getLogger(__name__)

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)
HEADERS = {"User-Agent": UA, "Accept-Language": "zh-CN,zh;q=0.9"}
ORG_INFO_EM2016 = (
    "https://datacenter-web.eastmoney.com/api/data/v1/get"
    "?reportName=RPT_F10_BASIC_ORGINFO&columns=SECURITY_CODE,SECURITY_NAME_ABBR,EM2016&pageNumber="
)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class JobState(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)

    running: bool = False
    phase: str = "idle"
    label: str = "空闲"
    done: int = 0
    total: int = 0
    message: str = ""
    started_at: str | None = None
    finished_at: str | None = None
    error: str = ""


def _new_job(phase: str, label: str) -> JobState:
    return JobState(running=True, phase=phase, label=label, started_at=_now_iso())


state_owned_job = JobState()
state_owned_meta: dict[str, Any] = {"updatedAt": None, "sourceStats": {}}
msci_job = JobState()


def launch_state_owned(force: bool, limit: int | None) -> JobState:
    """同步置 running=true 后派发后台任务，保证 /start 立即返回可见的 running 状态。"""
    state_owned_job.running = True
    state_owned_job.phase = "universe"
    state_owned_job.label = "拉取全市场公司概况"
    state_owned_job.done = 0
    state_owned_job.total = 0
    state_owned_job.message = ""
    state_owned_job.started_at = _now_iso()
    state_owned_job.finished_at = None
    state_owned_job.error = ""
    asyncio.create_task(run_state_owned_job(force, limit))
    return state_owned_job


def launch_msci_analyze() -> JobState:
    msci_job.running = True
    msci_job.phase = "industry"
    msci_job.label = "拉取行业分类"
    msci_job.done = 0
    msci_job.total = 0
    msci_job.message = ""
    msci_job.started_at = _now_iso()
    msci_job.finished_at = None
    msci_job.error = ""
    asyncio.create_task(run_msci_analyze_job())
    return msci_job


# --------------------------------------------------------------------------- #
# DB 写回：stocks 表 upsert，extra 用 jsonb || 合并，避免覆盖其它来源的字段
# --------------------------------------------------------------------------- #
async def _upsert_stocks_chunked(
    session: AsyncSession,
    rows: list[dict[str, Any]],
    set_builder,
    chunk_size: int = 500,
) -> None:
    """分批 upsert，避免一次性 INSERT 超过 asyncpg 的 32767 参数上限。"""
    table = Stock.__table__
    for start in range(0, len(rows), chunk_size):
        chunk = rows[start : start + chunk_size]
        keys = {key for row in chunk for key in row}
        normalized = [{key: row.get(key) for key in keys} for row in chunk]
        statement = pg_insert(table).values(normalized)
        set_map = set_builder(statement, table)
        set_map.setdefault("updated_at", func.now())
        await session.execute(
            statement.on_conflict_do_update(index_elements=["code"], set_=set_map)
        )


async def _get_msci_codes(session: AsyncSession) -> list[str]:
    codes = (await session.scalars(select(Stock.code).where(Stock.in_msci.is_(True)))).all()
    return [str(c) for c in codes]


# --------------------------------------------------------------------------- #
# 全市场国企扫描
# --------------------------------------------------------------------------- #
async def _fetch_org_rows(client: httpx.AsyncClient, job: JobState) -> list[dict[str, Any]]:
    org_rows: list[dict[str, Any]] = []
    page, pages = 1, 1
    while True:
        url = f"{ths_mod.ORG_INFO_URL}{page}&pageSize=500"
        resp = await client.get(url, headers={"Referer": "https://data.eastmoney.com/"})
        resp.raise_for_status()
        payload = resp.json()
        data = (payload or {}).get("result") or {}
        rows = data.get("data") if isinstance(data, dict) else None
        rows = rows if isinstance(rows, list) else []
        if not rows:
            break
        org_rows.extend(rows)
        pages = int(data.get("pages") or pages)
        job.done = page
        job.total = pages
        job.message = f"已获取 {len(org_rows)} 条公司概况"
        if page >= pages:
            break
        page += 1
    return org_rows


async def run_state_owned_job(force: bool, limit: int | None) -> None:
    global state_owned_meta
    job = _new_job("universe", "拉取全市场公司概况")
    state_owned_job.running = job.running
    state_owned_job.phase = job.phase
    state_owned_job.label = job.label
    state_owned_job.started_at = job.started_at
    state_owned_job.done = state_owned_job.total = 0
    state_owned_job.message = ""
    state_owned_job.finished_at = None
    state_owned_job.error = ""

    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(20.0, connect=6.0), headers=HEADERS) as client:
            org_rows = await _fetch_org_rows(client, state_owned_job)

            # 过滤：沪深主板 + 北交所，排除 ST / 退
            seen: set[str] = set()
            candidates: list[dict[str, Any]] = []
            for row in org_rows:
                sec_type = str(row.get("SECURITY_TYPE") or "")
                if sec_type not in ths_mod.A_SHARE_TYPES:
                    continue
                code = str(row.get("SECURITY_CODE") or "").strip()
                if not re.fullmatch(r"\d{6}", code) or code in seen:
                    continue
                name = str(row.get("SECURITY_NAME_ABBR") or "").strip()
                if re.search(r"ST|退", name):
                    continue
                seen.add(code)
                candidates.append(
                    {
                        "code": code,
                        "name": name,
                        "board": ths_mod.board_of(sec_type),
                        "actualHolder": str(row.get("ACTUAL_HOLDER") or "").strip(),
                    }
                )

            after_filter = len(candidates)
            if limit:
                candidates = candidates[:limit]

            # 阶段 2：行情校验上市状态
            state_owned_job.phase = "quotes"
            state_owned_job.label = "校验上市状态并拉取行情"
            state_owned_job.done = 0
            state_owned_job.total = len(candidates)
            state_owned_job.message = "正在批量拉取行情…"
            tencent_quotes = await quote_service.fetch_tencent([c["code"] for c in candidates])
            live_rows = [c for c in candidates if c["code"] in tencent_quotes]

            # 阶段 3：央企名录映射
            central_names = await ths_mod.load_central_soe_names()
            central_matched: dict[str, str] = {}
            for row in live_rows:
                base = ths_mod.classify_bulk_holder(row["actualHolder"])
                if base["category"] != "state-group":
                    continue
                matched = ths_mod.match_central_soe(row["actualHolder"], central_names)
                if matched:
                    central_matched[row["code"]] = matched

            refine_list = [
                row
                for row in live_rows
                if ths_mod.classify_bulk_holder(row["actualHolder"])["category"] in {"state-group", "other"}
                and row["code"] not in central_matched
            ]
            state_owned_job.phase = "controllers"
            state_owned_job.label = "穿透国有法人的最终控制人"
            state_owned_job.done = 0
            state_owned_job.total = len(refine_list)
            state_owned_job.message = (
                f"需要穿透 {len(refine_list)} 只（首次较慢，之后命中 30 天缓存）" if refine_list else "无需穿透"
            )

            # 阶段 4：并发穿透（限流 2 并发，同花顺本身还有最小间隔限流）
            refined_map: dict[str, dict[str, Any]] = {}
            semaphore = asyncio.Semaphore(2)

            async def refine(row: dict[str, Any]) -> None:
                sec_code = f"{ths_mod.market_prefix_of(row['code'])}{row['code']}"
                async with semaphore:
                    try:
                        item = await ths_mod.resolve_controller(sec_code, force)
                        refined_map[row["code"]] = item
                    except Exception as exc:  # noqa: BLE001
                        refined_map[row["code"]] = {"category": "unknown", "note": str(exc) or "穿透失败"}
                    state_owned_job.done += 1
                    state_owned_job.message = f"已穿透 {state_owned_job.done}/{state_owned_job.total} 只国有法人"

            await asyncio.gather(*(refine(row) for row in refine_list))

            # 阶段 5：汇总 + 入库
            state_owned_job.phase = "save"
            state_owned_job.label = "汇总并保存"
            state_owned_job.done = 0
            state_owned_job.total = len(live_rows)

            db_rows: list[dict[str, Any]] = []
            for row in live_rows:
                base = ths_mod.classify_bulk_holder(row["actualHolder"])
                refined = refined_map.get(row["code"])
                quote = tencent_quotes.get(row["code"]) or {}
                source_url_base = (
                    "https://emweb.securities.eastmoney.com/PC_HSF10/CompanySurvey/Index?type=web&code="
                    f"{ths_mod.market_prefix_of(row['code'])}{row['code']}"
                )

                category = base["category"]
                label = base["label"]
                final_controller = ""
                controlling_holder = ""
                source = "东方财富数据中心·公司概况"
                source_url = source_url_base
                note = ""

                matched_central = central_matched.get(row["code"])
                if matched_central:
                    category = "sasac-central"
                    label = ths_mod.STATE_CATEGORY_LABELS["sasac-central"]
                    source = "东方财富数据中心 + 国资委央企名录"
                    source_url = ths_mod.CENTRAL_SOE_URL
                    note = f"央企名录匹配：{matched_central}"

                if refined:
                    final_controller = str(refined.get("finalController") or "")
                    controlling_holder = str(refined.get("controllingHolder") or "")
                    mapped = ths_mod.map_ths_category(str(refined.get("category") or ""))
                    if mapped:
                        category = mapped
                        label = ths_mod.STATE_CATEGORY_LABELS.get(mapped, label)
                    source = "同花顺 F10·公司资料（穿透）"
                    source_url = str(refined.get("sourceUrl") or f"https://basic.10jqka.com.cn/{row['code']}/company.html")
                    note = str(refined.get("note") or "")
                    if category == "state-group" and not note:
                        fallback = ths_mod.match_central_soe(
                            "、".join(x for x in (refined.get("actualController"), refined.get("controllingHolder"), refined.get("finalController")) if x),
                            central_names,
                        )
                        if fallback:
                            category = "sasac-central"
                            label = ths_mod.STATE_CATEGORY_LABELS["sasac-central"]
                            note = f"央企名录匹配：{fallback}"
                        else:
                            note = "仅披露到国有集团层面，建议人工核对"

                db_rows.append(
                    {
                        "code": row["code"],
                        "name": row["name"],
                        "board": row["board"],
                        "controller": final_controller or row["actualHolder"],
                        "controller_category": category,
                        "state_owned": category in ths_mod.STATE_CATEGORIES,
                        "price": quote.get("price"),
                        "change_pct": quote.get("changePct"),
                        "extra": {
                            "actual_holder": row["actualHolder"],
                            "final_controller": final_controller,
                            "controlling_holder": controlling_holder,
                            "source": source,
                            "source_url": source_url,
                            "note": note,
                            "category_label": label,
                        },
                    }
                )

            db_rows.sort(key=lambda r: r["code"])

            async with AsyncSessionLocal() as session:
                def set_builder(statement, table):
                    return {
                        "name": func.coalesce(statement.excluded.name, table.c.name),
                        "board": statement.excluded.board,
                        "controller": statement.excluded.controller,
                        "controller_category": statement.excluded.controller_category,
                        "state_owned": statement.excluded.state_owned,
                        "price": statement.excluded.price,
                        "change_pct": statement.excluded.change_pct,
                        "extra": func.coalesce(table.c.extra, text("'{}'::jsonb")).op("||")(statement.excluded.extra),
                    }

                await _upsert_stocks_chunked(session, db_rows, set_builder)
                await session.commit()

            counts: dict[str, int] = {}
            for row in db_rows:
                counts[row["controller_category"]] = counts.get(row["controller_category"], 0) + 1
            state_owned_meta = {
                "updatedAt": _now_iso(),
                "sourceStats": {
                    "orgInfoRows": len(org_rows),
                    "afterBoardAndFilter": after_filter,
                    "liveRows": len(live_rows),
                    "refinedRows": len(refined_map),
                    "centralSoeNames": len(central_names),
                    "centralMatched": len(central_matched),
                },
            }

            state_owned_job.phase = "done"
            state_owned_job.label = "完成"
            state_owned_job.done = len(db_rows)
            state_owned_job.total = len(db_rows)
            state_owned_job.message = (
                f"已保存 {len(db_rows)} 只，其中国资/政府控股 "
                f"{sum(1 for r in db_rows if r['controller_category'] in ths_mod.STATE_CATEGORIES)} 只"
            )
    except Exception as exc:  # noqa: BLE001
        logger.exception("state-owned job 失败")
        state_owned_job.phase = "error"
        state_owned_job.label = "失败"
        state_owned_job.error = str(exc) or "扫描失败"
        state_owned_job.message = state_owned_job.error
    finally:
        state_owned_job.running = False
        state_owned_job.finished_at = _now_iso()


# --------------------------------------------------------------------------- #
# MSCI 分析（行业 / PE / ROE）
# --------------------------------------------------------------------------- #
_org_info_map: dict[str, dict[str, str]] | None = None
_org_info_fetched_at = 0.0


async def _load_org_info_map(client: httpx.AsyncClient) -> dict[str, dict[str, str]]:
    global _org_info_map, _org_info_fetched_at
    if _org_info_map and time.time() - _org_info_fetched_at < 2 * 24 * 3600:
        return _org_info_map

    mapping: dict[str, dict[str, str]] = {}
    page, pages = 1, 1
    while True:
        url = f"{ORG_INFO_EM2016}{page}&pageSize=500"
        resp = await client.get(url, headers={"Referer": "https://data.eastmoney.com/"})
        resp.raise_for_status()
        payload = resp.json()
        data = (payload or {}).get("result") or {}
        rows = data.get("data") if isinstance(data, dict) else None
        rows = rows if isinstance(rows, list) else []
        if not rows:
            break
        for row in rows:
            code = str(row.get("SECURITY_CODE") or "").strip()
            if not re.fullmatch(r"\d{6}", code):
                continue
            mapping[code] = {
                "name": str(row.get("SECURITY_NAME_ABBR") or "").strip(),
                "industry": str(row.get("EM2016") or "").strip(),
            }
        pages = int(data.get("pages") or pages)
        if page >= pages:
            break
        page += 1

    _org_info_map = mapping
    _org_info_fetched_at = time.time()
    return mapping


async def run_msci_analyze_job() -> None:
    job = _new_job("industry", "拉取行业分类")
    msci_job.running = True
    msci_job.phase = job.phase
    msci_job.label = job.label
    msci_job.started_at = job.started_at
    msci_job.done = msci_job.total = 0
    msci_job.message = ""
    msci_job.finished_at = None
    msci_job.error = ""

    try:
        async with AsyncSessionLocal() as session:
            codes = await _get_msci_codes(session)
            if not codes:
                raise RuntimeError("还没有导入 MSCI 数据")

            async with httpx.AsyncClient(timeout=httpx.Timeout(20.0, connect=6.0), headers=HEADERS) as client:
                org_info = await _load_org_info_map(client)
                matched_industry = sum(1 for c in codes if org_info.get(c, {}).get("industry"))

                msci_job.phase = "quotes"
                msci_job.label = "拉取 PE(TTM) 与现价"
                msci_job.total = len(codes)
                msci_job.message = f"行业匹配 {matched_industry}/{len(codes)}"
                quotes = await quote_service.fetch_tencent(codes)

                msci_job.phase = "roe"
                msci_job.label = "拉取 ROE"
                msci_job.done = 0
                msci_job.total = len(codes)
                msci_job.message = "正在批量查询 ROE…"

                prefixed = [f"{ths_mod.market_prefix_of(c)}{c}" for c in codes]
                roe_items, _ = await quote_service.resolve_roe(prefixed)
                bulk_roe: dict[str, float] = {}
                for code, pref in zip(codes, prefixed):
                    info = roe_items.get(pref)
                    if info and isinstance(info.get("roe"), (int, float)):
                        bulk_roe[code] = float(info["roe"])

            msci_job.phase = "save"
            msci_job.label = "汇总并保存"
            msci_job.total = len(codes)

            analyzed_at = _now_iso()
            db_rows: list[dict[str, Any]] = []
            for code in codes:
                info = org_info.get(code, {})
                quote = quotes.get(code) or {}
                industry = info.get("industry", "")
                parts = [p for p in industry.split("-") if p]
                roe = bulk_roe.get(code)
                db_rows.append(
                    {
                        "code": code,
                        "name": quote.get("name") or info.get("name") or "",
                        "industry": industry,
                        "pe_ttm": quote.get("peTtm"),
                        "pb": quote.get("pb"),
                        "price": quote.get("price"),
                        "change_pct": quote.get("changePct"),
                        "market_cap_yi": quote.get("marketCapYi"),
                        "roe": roe,
                        "extra": {
                            "industry_group": parts[-1] if parts else "未分类",
                            "industry_parent": "-".join(parts[:-1]) if len(parts) >= 2 else "",
                            "turnover": quote.get("turnover"),
                            "roe_report": "最新财报" if roe is not None else "",
                        },
                    }
                )

            async with AsyncSessionLocal() as session:
                def set_builder(statement, table):
                    return {
                        "name": func.coalesce(statement.excluded.name, table.c.name),
                        "industry": func.coalesce(statement.excluded.industry, table.c.industry),
                        "pe_ttm": statement.excluded.pe_ttm,
                        "pb": statement.excluded.pb,
                        "price": statement.excluded.price,
                        "change_pct": statement.excluded.change_pct,
                        "market_cap_yi": statement.excluded.market_cap_yi,
                        "roe": statement.excluded.roe,
                        "extra": func.coalesce(table.c.extra, text("'{}'::jsonb")).op("||")(statement.excluded.extra),
                    }

                await _upsert_stocks_chunked(session, db_rows, set_builder)
                meta = await session.get(MsciMeta, 1)
                if meta is None:
                    meta = MsciMeta(id=1)
                    session.add(meta)
                meta.analyzed_at = datetime.now(timezone.utc)
                await session.commit()

            msci_job.phase = "done"
            msci_job.label = "完成"
            msci_job.done = len(db_rows)
            msci_job.total = len(db_rows)
            msci_job.message = f"已分析 {len(db_rows)} 只，行业匹配 {matched_industry} 只"
    except Exception as exc:  # noqa: BLE001
        logger.exception("msci analyze job 失败")
        msci_job.phase = "error"
        msci_job.label = "失败"
        msci_job.error = str(exc) or "分析失败"
        msci_job.message = msci_job.error
    finally:
        msci_job.running = False
        msci_job.finished_at = _now_iso()
