"""MSCI 成份股接口。

    GET  /api/msci         从 stocks(in_msci=True) + msci_meta 组装回显
    POST /api/msci         保存导入结果：重置 in_msci 后把新成份股标记为 True
    POST /api/msci/parse   上传 Base64（TXT/CSV/Excel）内存解析
    POST /api/msci/analyze 后台补全行业/PE/ROE
    GET  /api/msci/progress 分析进度
    POST /api/msci/refresh 刷新行情
"""

from __future__ import annotations

import base64
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, Response, status
from sqlalchemy import func, select, text, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.models import MsciMeta, Stock
from app.services import jobs as jobs_mod
from app.services.msci_parse import normalize_msci_matrix, read_sheet_matrix
from app.services.quotes import quote_service

router = APIRouter(tags=["msci"])


def _parse_dt(value: Any) -> datetime | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


async def _assemble(db: AsyncSession) -> dict[str, Any]:
    meta = await db.get(MsciMeta, 1)
    rows = (
        await db.scalars(select(Stock).where(Stock.in_msci.is_(True)).order_by(Stock.code))
    ).all()

    out_rows: list[dict[str, Any]] = []
    for stock in rows:
        extra = stock.extra or {}
        industry = stock.industry or ""
        parts = [p for p in industry.split("-") if p]
        out_rows.append(
            {
                "code": stock.code,
                "name": stock.name,
                "extra": stock.msci_extra or {},
                "quote": None,
                "industry": industry,
                "industryGroup": extra.get("industry_group") or (parts[-1] if parts else "未分类"),
                "industryParent": extra.get("industry_parent")
                or ("-".join(parts[:-1]) if len(parts) >= 2 else ""),
                "price": float(stock.price) if stock.price is not None else None,
                "changePct": float(stock.change_pct) if stock.change_pct is not None else None,
                "peTtm": float(stock.pe_ttm) if stock.pe_ttm is not None else None,
                "pb": float(stock.pb) if stock.pb is not None else None,
                "marketCapYi": float(stock.market_cap_yi) if stock.market_cap_yi is not None else None,
                "turnover": extra.get("turnover"),
                "roe": float(stock.roe) if stock.roe is not None else None,
                "roeReport": extra.get("roe_report") or "",
                "analyzedAt": meta.analyzed_at.isoformat() if meta and meta.analyzed_at else None,
            }
        )

    return {
        "fileName": meta.file_name if meta and meta.file_name else "",
        "importedAt": meta.imported_at.isoformat() if meta and meta.imported_at else None,
        "updatedAt": meta.updated_at.isoformat() if meta and meta.updated_at else None,
        "analyzedAt": meta.analyzed_at.isoformat() if meta and meta.analyzed_at else None,
        "columns": meta.columns if meta and meta.columns else [],
        "rows": out_rows,
    }


@router.get("/msci", summary="MSCI 成份股数据")
async def get_msci(db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    data = await _assemble(db)
    data["job"] = jobs_mod.msci_job.model_dump(by_alias=True)
    return data


@router.post("/msci", summary="保存 MSCI 导入结果")
async def save_msci(payload: dict[str, Any], db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    rows = payload.get("rows")
    rows = rows if isinstance(rows, list) else []
    columns = payload.get("columns")
    columns = [str(c) for c in columns] if isinstance(columns, list) else []

    normalized = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        code = str(row.get("code") or "").strip()
        if len(code) != 6 or not code.isdigit():
            continue
        normalized.append(
            {
                "code": code,
                "name": str(row.get("name") or "").strip(),
                "msci_extra": row.get("extra") if isinstance(row.get("extra"), dict) else {},
            }
        )

    # 先重置旧标记，再把新成份股设为 True
    await db.execute(update(Stock).values(in_msci=False))
    if normalized:
        table = Stock.__table__
        values = [
            {"code": r["code"], "name": r["name"], "in_msci": True, "msci_extra": r["msci_extra"]}
            for r in normalized
        ]
        statement = pg_insert(table).values(values)
        statement = statement.on_conflict_do_update(
            index_elements=["code"],
            set_={
                "name": func.coalesce(statement.excluded.name, table.c.name),
                "in_msci": statement.excluded.in_msci,
                "msci_extra": statement.excluded.msci_extra,
                "updated_at": func.now(),
            },
        )
        await db.execute(statement)

    meta = await db.get(MsciMeta, 1)
    if meta is None:
        meta = MsciMeta(id=1)
        db.add(meta)
    meta.file_name = str(payload.get("fileName") or "") or None
    meta.imported_at = _parse_dt(payload.get("importedAt")) or datetime.now(timezone.utc)
    meta.updated_at = _parse_dt(payload.get("updatedAt"))
    meta.analyzed_at = _parse_dt(payload.get("analyzedAt"))
    meta.columns = columns
    await db.commit()

    return {"ok": True, "count": len(normalized)}


@router.post("/msci/parse", summary="解析上传的 TXT / Excel")
async def parse_msci(response: Response, payload: dict[str, Any]) -> dict[str, Any]:
    file_name = str(payload.get("fileName") or "import.txt")
    data_base64 = str(payload.get("dataBase64") or "")

    def fail(message: str) -> dict[str, Any]:
        response.status_code = status.HTTP_400_BAD_REQUEST
        return {"message": message, "warnings": [], "rows": [], "columns": []}

    if not data_base64:
        return fail("没有收到文件内容")

    try:
        data = base64.b64decode(data_base64)
    except (ValueError, TypeError):
        return fail("文件内容不是合法的 Base64")
    if not data:
        return fail("文件内容为空")

    try:
        matrix = read_sheet_matrix(file_name, data)
        parsed = normalize_msci_matrix(matrix)
    except Exception as exc:  # noqa: BLE001
        return fail(f"文件解析失败：{exc}")

    if not parsed["rows"]:
        return fail(parsed["warnings"][0] if parsed["warnings"] else "解析失败")

    return {
        "ok": True,
        "fileName": file_name,
        "columns": parsed["columns"],
        "rows": parsed["rows"],
        "warnings": parsed["warnings"],
        "total": len(parsed["rows"]),
    }


@router.post("/msci/analyze", summary="启动 MSCI 分析（行业/PE/ROE）")
async def analyze_msci(response: Response) -> dict[str, Any]:
    if jobs_mod.msci_job.running:
        response.status_code = status.HTTP_409_CONFLICT
        return {"message": "分析任务正在进行中", "job": jobs_mod.msci_job.model_dump(by_alias=True)}
    jobs_mod.launch_msci_analyze()
    return {"started": True, "job": jobs_mod.msci_job.model_dump(by_alias=True)}


@router.get("/msci/progress", summary="MSCI 分析进度")
async def msci_progress() -> dict[str, Any]:
    return {"job": jobs_mod.msci_job.model_dump(by_alias=True)}


@router.post("/msci/refresh", summary="刷新 MSCI 行情")
async def refresh_msci(db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    codes = (
        await db.scalars(select(Stock.code).where(Stock.in_msci.is_(True)))
    ).all()
    codes = [str(c) for c in codes]
    if not codes:
        return {"message": "还没有导入 MSCI 数据", "ok": False}

    quotes = await quote_service.fetch_tencent(codes)
    values = []
    for code in codes:
        quote = quotes.get(code)
        if not quote:
            continue
        values.append(
            {
                "code": code,
                "name": quote.get("name") or "",
                "price": quote.get("price"),
                "change_pct": quote.get("changePct"),
                "pe_ttm": quote.get("peTtm"),
                "pb": quote.get("pb"),
                "market_cap_yi": quote.get("marketCapYi"),
                "extra": {"turnover": quote.get("turnover")},
            }
        )
    if values:
        table = Stock.__table__
        statement = pg_insert(table).values(values)
        statement = statement.on_conflict_do_update(
            index_elements=["code"],
            set_={
                "name": func.coalesce(statement.excluded.name, table.c.name),
                "price": statement.excluded.price,
                "change_pct": statement.excluded.change_pct,
                "pe_ttm": statement.excluded.pe_ttm,
                "pb": statement.excluded.pb,
                "market_cap_yi": statement.excluded.market_cap_yi,
                "extra": func.coalesce(table.c.extra, text("'{}'::jsonb")).op("||")(statement.excluded.extra),
                "updated_at": func.now(),
            },
        )
        await db.execute(statement)

    now = datetime.now(timezone.utc)
    meta = await db.get(MsciMeta, 1)
    if meta is None:
        meta = MsciMeta(id=1)
        db.add(meta)
    meta.updated_at = now
    await db.commit()

    return {"ok": True, "updatedAt": now.isoformat(), "total": len(values), "message": ""}
