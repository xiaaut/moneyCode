"""实际控制人查询接口。

GET /api/controller?codes=SH600519,SZ000725&refresh=1
优先命中 30 天内存缓存，否则同花顺 F10 穿透 + 东财 F10 兜底，
并把结果 ON CONFLICT 同步回 stocks 表的 controller / controller_category / state_owned。
"""

from __future__ import annotations

import re

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.models import Stock
from app.services import ths as ths_mod

router = APIRouter(tags=["controller"])

CODE_RE = re.compile(r"^(SH|SZ|BJ)\d{6}$")


async def _sync_stock(db: AsyncSession, item: dict) -> None:
    """把穿透结果回写 stocks（controller + state_owned + controller_category）。"""
    pure = re.sub(r"^(SH|SZ|BJ)", "", item["code"])
    name = (
        item.get("finalController")
        or item.get("actualController")
        or item.get("eastmoneyController")
        or ""
    )
    category = item.get("category") or "unknown"
    unified = ths_mod.map_ths_category(category) or category
    state_owned = unified in ths_mod.STATE_CATEGORIES

    table = Stock.__table__
    statement = pg_insert(table).values(
        [
            {
                "code": pure,
                "name": item.get("name") or "",
                "controller": name,
                "controller_category": unified,
                "state_owned": state_owned,
                "extra": {"controller_detail": item},
            }
        ]
    )
    statement = statement.on_conflict_do_update(
        index_elements=["code"],
        set_={
            "name": func.coalesce(statement.excluded.name, table.c.name),
            "controller": statement.excluded.controller,
            "controller_category": statement.excluded.controller_category,
            "state_owned": statement.excluded.state_owned,
            "extra": func.coalesce(table.c.extra, text("'{}'::jsonb")).op("||")(statement.excluded.extra),
            "updated_at": func.now(),
        },
    )
    await db.execute(statement)
    await db.commit()


@router.get("/controller", summary="查询实际控制人（同花顺为主，东财兜底）")
async def get_controller(
    db: AsyncSession = Depends(get_db),
    codes: str = Query("", description="SH600519,SZ000725（支持 SH/SZ/BJ 前缀）"),
    refresh: str = Query("", description="传 1 时跳过 30 天缓存强制重抓"),
) -> dict:
    code_list = [c.strip().upper() for c in codes.split(",") if c.strip()]
    if not code_list or any(not CODE_RE.match(c) for c in code_list):
        return {"items": {}, "message": "codes 参数格式错误，应为 SH600519,SZ000725"}

    force = refresh == "1"
    items: dict[str, dict] = {}
    for code in dict.fromkeys(code_list):
        try:
            item = await ths_mod.resolve_controller(code, force)
            await _sync_stock(db, item)
        except Exception as exc:  # noqa: BLE001
            item = {
                "code": code,
                "category": "unknown",
                "categoryLabel": ths_mod.CATEGORY_LABELS["unknown"],
                "note": str(exc) or "抓取失败",
                "fetchedAt": ths_mod._now_iso(),
            }
        items[code] = {k: v for k, v in item.items() if k != "fromCache"}
        items[code]["fromCache"] = item.get("fromCache", False)

    return {"items": items, "message": ""}
