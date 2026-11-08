"""统一回写服务：把行情/估值/财务指标回写 stocks 表，含防零值保护。

核心目标：无论数据来自同花顺主通道还是免费备用通道，只要拿到有效数据，
统一更新 stocks 表，消除持仓页/全市场页/选股策略页之间的数据不一致。
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import func
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Stock

logger = logging.getLogger(__name__)


def _get(data: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in data and data[key] is not None:
            return data[key]
    return None


def _clean(value: Any) -> Any:
    """过滤 NaN / None。"""
    if value is None:
        return None
    if isinstance(value, float) and value != value:  # NaN
        return None
    return value


def extract_valid_fields(data: dict[str, Any]) -> dict[str, Any]:
    """从标准指标字典提取有效字段，返回 {stocks列名: 值}（过滤零值/无效值）。"""
    fields: dict[str, Any] = {}

    # 最新价：必须 > 0（过滤非交易时段的 0 值）
    price = _clean(_get(data, "price"))
    if price is not None and price > 0:
        fields["price"] = float(price)
        change = _clean(_get(data, "changePct", "change_pct"))
        if change is not None:
            fields["change_pct"] = float(change)

    # 估值 / 财务：非 None 且非 0（保留负 PE 等有语义的值）
    mapping = {
        "pe_ttm": ("pe_ttm", "pe"),
        "pb": ("pb",),
        "roe": ("roe",),
        "eps_current": ("eps_current", "eps"),
        "revenue": ("revenue",),
        "revenue_yoy": ("revenue_yoy",),
        "net_profit": ("net_profit",),
        "net_profit_yoy": ("net_profit_yoy",),
        "gross_margin": ("gross_margin",),
        "report_period": ("report_period",),
        "market_cap_yi": ("marketCapYi", "market_cap_yi"),
        "volume": ("volume",),
        "turnover_amount": ("turnover", "turnover_amount"),
        "industry": ("industry",),
    }
    for column, keys in mapping.items():
        value = _clean(_get(data, *keys))
        if value is not None and value != 0:
            fields[column] = float(value) if isinstance(value, (int, float)) else value

    # 名称：非空字符串才更新
    name = _get(data, "name")
    if name and str(name).strip():
        fields["name"] = str(name).strip()

    return fields


async def writeback_stock(db: AsyncSession, code: str, data: dict[str, Any]) -> bool:
    """单股回写有效字段（用传入的 session，调用方负责 commit）。

    name 兜底策略：INSERT 新代码时用 code 兜底满足 NOT NULL；
    UPDATE 已有代码时，仅在拿到真实 name 才覆盖（空 name 保留库内已有值）。
    """
    fields = extract_valid_fields(data)
    table = Stock.__table__

    # 名称：真实名称优先，否则 INSERT 时用 code 兜底
    name_for_insert = fields.pop("name", None) or code

    statement = pg_insert(table).values({"code": code, "name": name_for_insert, **fields})
    set_map = {key: statement.excluded[key] for key in fields}
    # UPDATE 时：若本次 name 是 code 兜底（未拿到真实名称），用 nullif 归空后 coalesce 保留库内值
    set_map["name"] = func.coalesce(func.nullif(statement.excluded.name, code), table.c.name)
    set_map["updated_at"] = func.now()
    await db.execute(
        statement.on_conflict_do_update(index_elements=["code"], set_=set_map)
    )
    return True


async def writeback_bulk(db: AsyncSession, items: dict[str, dict[str, Any]]) -> int:
    """批量回写 {code: data}，返回实际更新的条数。"""
    count = 0
    for code, data in items.items():
        if await writeback_stock(db, code, data):
            count += 1
    return count
