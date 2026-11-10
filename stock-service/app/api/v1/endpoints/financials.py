"""财报透视与排雷工作台接口。"""

from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters import data_router
from app.db.session import get_db
from app.models import Stock, StockAnalysisNote, StockFinancialStatement
from app.models.financial import ALL_MONEY_FIELDS
from app.services import financial_analysis

router = APIRouter(tags=["financials"])


def _to_row(item: dict) -> dict:
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
    return row


@router.get("/financials/report/{code}", summary="财报透视与排雷报告")
async def get_financial_report(
    code: str,
    refresh: bool = Query(False, description="true 时强制重拉三表"),
    db: AsyncSession = Depends(get_db),
) -> dict:
    normalized = "".join(ch for ch in code if ch.isdigit())[-6:]
    if len(normalized) != 6:
        raise HTTPException(status_code=400, detail="无效的股票代码")

    # 读库
    existing = (
        await db.scalars(
            select(StockFinancialStatement)
            .where(StockFinancialStatement.code == normalized)
            .order_by(StockFinancialStatement.report_date.desc())
        )
    ).all()

    if existing and not refresh:
        items = [_row_from_model(r) for r in existing]
        # 读库分支：用库内原始三表字段重算全部衍生指标（含驱动类型）+ 雷达/健康分/透视，
        # 确保 compute_derived/_driver_type 逻辑升级后旧库数据也能即时反映新定性
        financial_analysis.analyze(items)
    else:
        # 拉取三表 + 计算
        items = await data_router.get_financial_statements(normalized, "annual", 5)
        if not items:
            raise HTTPException(status_code=404, detail=f"未获取到 {normalized} 的三表数据")
        financial_analysis.analyze(items)

        # 落库
        table = StockFinancialStatement.__table__
        for item in items:
            row = _to_row(item)
            statement = pg_insert(table).values(row)
            updatable = [c.name for c in table.columns if c.name not in {"code", "report_date"}]
            statement = statement.on_conflict_do_update(
                index_elements=["code", "report_date"],
                set_={name: statement.excluded[name] for name in updatable},
            )
            await db.execute(statement)
        await db.commit()

    stock = await db.scalar(select(Stock).where(Stock.code == normalized))
    name = stock.name if stock else normalized

    latest = items[0] if items else {}
    latest_dm = latest.get("derived_metrics") or {}
    net_bp = latest_dm.get("net_bargaining_power")
    bargaining_label = "产业链两头吃" if (net_bp is not None and net_bp > 0) else "产业链话语权偏弱"
    note = await db.get(StockAnalysisNote, normalized)

    return {
        "code": normalized,
        "name": name,
        "items": items,
        "radar_checks": latest.get("radar_checks", []),
        "health_score": latest.get("health_score"),
        "executive_summary": latest.get("executive_summary"),
        "driver_type": latest_dm.get("driver_type") or "多因素均衡驱动型",
        "bargaining_label": bargaining_label,
        "source": latest.get("source"),
        "note": {
            "notes": note.notes if note else "",
            "tags": note.tags if note else "",
            "rating": note.rating if note else "",
            "updated_at": note.updated_at.isoformat() if note and note.updated_at else None,
        },
    }


def _row_from_model(row: StockFinancialStatement) -> dict:
    item = {"code": row.code, "report_date": str(row.report_date), "report_label": row.report_label, "report_type": row.report_type, "source": row.source}
    for field in ALL_MONEY_FIELDS:
        value = getattr(row, field, None)
        item[field] = float(value) if value is not None else None
    item["derived_metrics"] = row.derived_metrics or {}
    return item


class NoteIn(BaseModel):
    notes: str = ""
    tags: str = ""
    rating: str = ""


@router.post("/financials/notes/{code}", summary="保存个人复盘笔记与评级")
async def save_note(code: str, payload: NoteIn, db: AsyncSession = Depends(get_db)) -> dict:
    normalized = "".join(ch for ch in code if ch.isdigit())[-6:]
    if len(normalized) != 6:
        raise HTTPException(status_code=400, detail="无效的股票代码")

    table = StockAnalysisNote.__table__
    statement = pg_insert(table).values(
        {
            "code": normalized,
            "notes": payload.notes,
            "tags": payload.tags,
            "rating": payload.rating,
        }
    )
    statement = statement.on_conflict_do_update(
        index_elements=["code"],
        set_={
            "notes": statement.excluded.notes,
            "tags": statement.excluded.tags,
            "rating": statement.excluded.rating,
            "updated_at": __import__("sqlalchemy").func.now(),
        },
    )
    await db.execute(statement)
    await db.commit()
    return {"ok": True, "code": normalized}
