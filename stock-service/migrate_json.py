#!/usr/bin/env python3
"""历史 JSON 数据清洗并导入 NAS PostgreSQL。

数据源（../stock-web/data/）：
    state-owned.json      -> stocks（代码/名称/板块/实际控制人）
    msci.json             -> stocks（行业/PE/PB/ROE/市值）
    portfolio.json        -> positions（同一代码多条记录按加权成本合并）+ stocks 补全
    watchlist.json        -> stocks（自选标记 / EPS 预测）
    closed-positions.json -> trade_history
    grid-strategies.json  -> grid_strategies + grid_trade_records

特性：
    * 全量 upsert（ON CONFLICT DO UPDATE），可重复执行
    * 任一文件缺失/损坏不影响其它文件，逐文件容错
    * 逐表进度日志 + 结束时打印各表记录数
    * --dry-run 只做清洗统计，不连接数据库

用法：
    python migrate_json.py              # 正式导入
    python migrate_json.py --dry-run    # 只看清洗结果
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import sys
import time
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

import app.models  # noqa: F401  注册全部模型
from app.core.config import BASE_DIR, settings
from app.db.session import AsyncSessionLocal, engine
from app.utils.industry import clean_industry_or_none
from app.models import (
    GridStrategy,
    GridTradeRecord,
    MsciMeta,
    Position,
    Stock,
    TradeHistory,
    WatchlistItem,
)

DEFAULT_DATA_DIR = BASE_DIR.parent / "stock-web" / "data"
DEFAULT_CHUNK_SIZE = 500

# JSON 里的各类空值表示
NULL_TOKENS = {"", "-", "--", "—", "nan", "null", "none", "n/a", "无", "无数据"}

# 计入「国企/政府控股」的类别（与前端 STATE_CATEGORIES 一致）
STATE_OWNED_CATEGORIES = {"sasac-central", "sasac-local", "government", "state-finance"}

CENT = Decimal("0.01")
RATIO = Decimal("0.0001")


# --------------------------------------------------------------------------- #
# 基础清洗工具
# --------------------------------------------------------------------------- #
def log(message: str) -> None:
    print(f"[{datetime.now():%H:%M:%S}] {message}", flush=True)


def to_decimal(value: Any) -> Decimal | None:
    """字符串 '5.97%' / '-' / 12.3 统一转 Decimal，非法值返回 None。"""
    if value is None or isinstance(value, bool):
        return None
    try:
        if isinstance(value, Decimal):
            return value if value.is_finite() else None
        if isinstance(value, (int, float)):
            result = Decimal(str(value))
            return result if result.is_finite() else None
        text = str(value).strip().replace(",", "")
        if text.lower() in NULL_TOKENS:
            return None
        text = text.rstrip("%").strip()
        result = Decimal(text)
        return result if result.is_finite() else None
    except (InvalidOperation, ValueError):
        return None


def to_int(value: Any) -> int | None:
    result = to_decimal(value)
    return int(result) if result is not None else None


def to_text(value: Any, limit: int | None = None) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if text.lower() in NULL_TOKENS:
        return None
    return text[:limit] if limit else text


def to_date(value: Any) -> date | None:
    if not value:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value).strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")[:19]).date()
    except ValueError:
        pass
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y%m%d", "%Y.%m.%d"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def to_datetime(value: Any) -> datetime | None:
    if not value:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    try:
        parsed = datetime.fromisoformat(str(value).strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def json_safe(value: Any) -> Any:
    """把 Decimal/date 递归转成 JSONB 可序列化的类型，并丢弃空值。"""
    if isinstance(value, dict):
        return {k: json_safe(v) for k, v in value.items() if v not in (None, "", [], {})}
    if isinstance(value, (list, tuple)):
        return [json_safe(item) for item in value]
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    return value


def load_json(path: Path, expected: type) -> Any:
    """读取 JSON；文件缺失或损坏时返回空容器并记录告警（不中断整体迁移）。"""
    if not path.exists():
        log(f"⚠️  文件不存在，跳过：{path.name}")
        return expected()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        log(f"⚠️  解析失败，跳过 {path.name}：{exc}")
        return expected()
    if not isinstance(data, expected):
        log(f"⚠️  {path.name} 顶层结构为 {type(data).__name__}，期望 {expected.__name__}，跳过")
        return expected()
    return data


def merge_patch(target: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
    """把补丁合并进目标行；None/空串不覆盖已有值，extra 做字典合并。"""
    for key, value in patch.items():
        if value is None or value == "":
            continue
        if key == "extra" and isinstance(value, dict):
            target.setdefault("extra", {}).update(json_safe(value))
        else:
            target[key] = value
    return target


# --------------------------------------------------------------------------- #
# 各数据源 -> 行数据
# --------------------------------------------------------------------------- #
def collect_position_rows(data_dir: Path) -> tuple[list[dict], list[dict], dict[str, int]]:
    """portfolio.json -> (持仓行, 股票补丁, 统计)。

    同一代码出现多条时按股数加权合并成本，避免唯一键冲突丢数据。
    """
    items = load_json(data_dir / "portfolio.json", list)
    grouped: dict[str, dict[str, Any]] = {}
    stats = {"raw": len(items), "skipped": 0, "duplicates": 0}

    for record in items:
        if not isinstance(record, dict):
            stats["skipped"] += 1
            continue
        code = to_text(record.get("code"), 8)
        if not code:
            stats["skipped"] += 1
            continue
        if code in grouped:
            stats["duplicates"] += 1

        shares = to_int(record.get("quantity")) or 0
        cost = to_decimal(record.get("costPrice")) or Decimal("0")
        price = to_decimal(record.get("currentPrice"))

        bucket = grouped.setdefault(
            code,
            {
                "code": code,
                "name": to_text(record.get("name"), 64) or code,
                "shares": 0,
                "total_cost": Decimal("0"),
                "price": None,
                "sector": None,
                "position_ratio": None,
                "extra": {},
            },
        )
        bucket["shares"] += shares
        bucket["total_cost"] += cost * shares
        if price is not None:
            bucket["price"] = price
        bucket["sector"] = bucket["sector"] or to_text(record.get("sector"), 128)
        ratio = to_decimal(record.get("positionRatio"))
        if ratio is not None:
            bucket["position_ratio"] = ratio
        bucket["extra"].update(
            json_safe(
                {
                    "pb": to_decimal(record.get("pb")),
                    "pe": to_decimal(record.get("pe")),
                    "roe": to_decimal(record.get("roe")),
                    "current_pe": to_decimal(record.get("currentPe")),
                    "avg_pe": to_decimal(record.get("avgPe")),
                    "g": to_decimal(record.get("g")),
                    "peg": to_decimal(record.get("peg")),
                    "expected_return": to_decimal(record.get("expectedReturn")),
                    "eps": to_decimal(record.get("eps")),
                    "eps_3y": to_decimal(record.get("eps3y")),
                    "legacy_id": record.get("id"),
                }
            )
        )

    position_rows: list[dict[str, Any]] = []
    stock_patches: list[dict[str, Any]] = []
    for bucket in grouped.values():
        shares = bucket["shares"]
        total_cost = bucket["total_cost"]
        cost_price = (total_cost / shares).quantize(RATIO) if shares else None
        price = bucket["price"]
        market_value = (price * shares).quantize(CENT) if price is not None and shares else None
        profit = (
            (market_value - total_cost).quantize(CENT)
            if market_value is not None
            else None
        )
        profit_ratio = (
            (profit / total_cost * 100).quantize(RATIO)
            if profit is not None and total_cost > 0
            else None
        )

        position_rows.append(
            {
                "code": bucket["code"],
                "name": bucket["name"],
                "shares": shares,
                "cost_price": cost_price,
                "current_price": price,
                "market_value": market_value,
                "profit_amount": profit,
                "profit_ratio": profit_ratio,
                "sector": clean_industry_or_none(bucket["sector"]),
                "position_ratio": bucket["position_ratio"],
                "extra": json_safe(bucket["extra"]),
            }
        )

        metrics = bucket["extra"]
        stock_patches.append(
            {
                "code": bucket["code"],
                "name": bucket["name"],
                "industry": clean_industry_or_none(bucket["sector"]),
                "pb": to_decimal(metrics.get("pb")),
                "pe_ttm": to_decimal(metrics.get("pe")),
                "roe": to_decimal(metrics.get("roe")),
                "eps_current": to_decimal(metrics.get("eps")),
                "eps_forecast": to_decimal(metrics.get("eps_3y")),
                "price": price,
            }
        )

    stats["positions"] = len(position_rows)
    return position_rows, stock_patches, stats


def collect_stock_rows(data_dir: Path, portfolio_patches: list[dict]) -> tuple[list[dict], dict[str, int]]:
    """合并 4 个来源生成 stocks 行，后写字段覆盖先写字段（空值不覆盖）。"""
    merged: dict[str, dict[str, Any]] = {}
    stats = {"state_owned": 0, "msci": 0, "portfolio": 0, "watchlist": 0, "no_name": 0}

    def put(code: str | None, patch: dict[str, Any]) -> None:
        if not code:
            return
        merge_patch(merged.setdefault(code, {"code": code}), patch)

    # 1) 国有控股：代码 / 名称 / 板块 / 实际控制人
    payload = load_json(data_dir / "state-owned.json", dict)
    for record in payload.get("rows", []):
        if not isinstance(record, dict):
            continue
        controller = (
            to_text(record.get("finalController"), 128)
            or to_text(record.get("controllingHolder"), 128)
            or to_text(record.get("actualHolder"), 128)
        )
        category = (str(record.get("category") or "").strip() or None)
        put(
            to_text(record.get("code"), 8),
            {
                "name": to_text(record.get("name"), 64),
                "board": to_text(record.get("board"), 16),
                "controller": controller,
                "controller_category": category,
                "state_owned": category in STATE_OWNED_CATEGORIES,
                "price": to_decimal(record.get("price")),
                "change_pct": to_decimal(record.get("changePct")),
                "market_cap_yi": to_decimal(record.get("totalMarketValue")),
                "extra": {
                    "category_label": to_text(record.get("categoryLabel")),
                    "actual_holder": to_text(record.get("actualHolder")),
                    "final_controller": to_text(record.get("finalController")),
                    "controlling_holder": to_text(record.get("controllingHolder")),
                    "source": to_text(record.get("source")),
                    "source_url": to_text(record.get("sourceUrl")),
                    "note": to_text(record.get("note")),
                },
            },
        )
        stats["state_owned"] += 1

    # 2) MSCI：行业与估值
    payload = load_json(data_dir / "msci.json", dict)
    for record in payload.get("rows", []):
        if not isinstance(record, dict):
            continue
        put(
            to_text(record.get("code"), 8),
            {
                "name": to_text(record.get("name"), 64),
                "industry": clean_industry_or_none(
                    to_text(record.get("industry") or record.get("industryGroup"), 128)
                ),
                "pe_ttm": to_decimal(record.get("peTtm")),
                "pb": to_decimal(record.get("pb")),
                "roe": to_decimal(record.get("roe")),
                "price": to_decimal(record.get("price")),
                "change_pct": to_decimal(record.get("changePct")),
                "market_cap_yi": to_decimal(record.get("marketCapYi")),
                "in_msci": True,
                "msci_extra": json_safe(record.get("extra")),
                "extra": {
                    "industry_group": to_text(record.get("industryGroup")),
                    "industry_parent": to_text(record.get("industryParent")),
                    "roe_report": to_text(record.get("roeReport")),
                    "turnover": to_decimal(record.get("turnover")),
                },
            },
        )
        stats["msci"] += 1

    # 3) 持仓标的：更贴近手工维护的行业口径
    for patch in portfolio_patches:
        put(patch.get("code"), patch)
        stats["portfolio"] += 1

    # 4) 自选：EPS 预测与自选标记
    items = load_json(data_dir / "watchlist.json", list)
    for record in items:
        if not isinstance(record, dict):
            continue
        put(
            to_text(record.get("stockCode"), 8),
            {
                "name": to_text(record.get("stockName"), 64),
                "pe_ttm": to_decimal(record.get("currentPe")),
                "eps_current": to_decimal(record.get("currentEps")),
                "eps_forecast": to_decimal(record.get("targetEps")),
                "is_watchlist": True,
                "extra": {
                    "historical_avg_pe": to_decimal(record.get("historicalAvgPe")),
                    "mean_pe": to_decimal(record.get("meanPe")),
                    "hold_years": to_int(record.get("holdYears")),
                    "g_percent": to_decimal(record.get("gPercent")),
                    "peg": to_decimal(record.get("peg")),
                    "annualized_return": to_decimal(record.get("annualizedReturnPercent")),
                    "pass_count": to_int(record.get("passCount")),
                    "total_conditions": to_int(record.get("totalConditions")),
                    "all_passed": record.get("allPassed"),
                    "details": record.get("details") if isinstance(record.get("details"), list) else None,
                    "watchlist_added_at": to_text(record.get("createdAt")),
                },
            },
        )
        stats["watchlist"] += 1

    rows: list[dict[str, Any]] = []
    for row in merged.values():
        if not row.get("name"):
            stats["no_name"] += 1
            continue
        row.setdefault("extra", {})
        row.setdefault("is_watchlist", False)
        # 非 MSCI 来源的行不会带 in_msci，必须显式给默认值，否则撞 NOT NULL
        row.setdefault("in_msci", False)
        row.setdefault("state_owned", False)
        row.setdefault("msci_extra", {})
        rows.append(row)
    return rows, stats


def collect_trade_rows(data_dir: Path) -> tuple[list[dict], dict[str, int]]:
    """closed-positions.json -> trade_history。"""
    items = load_json(data_dir / "closed-positions.json", list)
    rows: list[dict[str, Any]] = []
    stats = {"raw": len(items), "skipped": 0, "derived": 0}

    for record in items:
        if not isinstance(record, dict):
            stats["skipped"] += 1
            continue
        code = to_text(record.get("code"), 8)
        if not code:
            stats["skipped"] += 1
            continue

        quantity = to_int(record.get("quantity"))
        buy_price = to_decimal(record.get("buyPrice"))
        sell_price = to_decimal(record.get("sellPrice"))
        fee = to_decimal(record.get("fee")) or Decimal("0")
        buy_date = to_date(record.get("buyDate"))
        sell_date = to_date(record.get("sellDate"))

        profit = to_decimal(record.get("profitAmount"))
        if profit is None and buy_price is not None and sell_price is not None and quantity:
            profit = ((sell_price - buy_price) * quantity - fee).quantize(CENT)
            stats["derived"] += 1

        hold_days = to_int(record.get("holdDays"))
        if hold_days is None and buy_date and sell_date:
            hold_days = (sell_date - buy_date).days

        profit_ratio = to_decimal(record.get("profitRatio"))
        if profit_ratio is None and profit is not None and buy_price and quantity:
            cost_basis = buy_price * quantity
            if cost_basis:
                profit_ratio = (profit / cost_basis * 100).quantize(RATIO)

        key_source = "|".join(
            str(part)
            for part in (
                code,
                buy_date,
                sell_date,
                quantity,
                buy_price,
                sell_price,
                fee,
            )
        )
        rows.append(
            {
                "code": code,
                "name": to_text(record.get("name"), 64) or code,
                "quantity": quantity,
                "buy_price": buy_price,
                "sell_price": sell_price,
                "fee": fee,
                "buy_date": buy_date,
                "sell_date": sell_date,
                "hold_days": hold_days,
                "profit_amount": profit,
                "profit_ratio": profit_ratio,
                "note": to_text(record.get("note")),
                "source_key": hashlib.sha1(key_source.encode("utf-8")).hexdigest(),
            }
        )

    return rows, stats


def collect_watchlist_rows(data_dir: Path) -> tuple[list[dict], dict[str, int]]:
    """watchlist.json -> watchlist_items（前端 WatchList.vue 用的完整指标快照）。"""
    items = load_json(data_dir / "watchlist.json", list)
    rows: list[dict[str, Any]] = []
    stats = {"raw": len(items), "skipped": 0}
    now_ms = int(time.time() * 1000)

    for index, record in enumerate(items):
        if not isinstance(record, dict):
            stats["skipped"] += 1
            continue
        code = to_text(record.get("stockCode"), 8)
        if not code:
            stats["skipped"] += 1
            continue

        rows.append(
            {
                "id": to_int(record.get("id")) or now_ms + index,
                "stock_code": code,
                "stock_name": to_text(record.get("stockName"), 64),
                "current_eps": to_decimal(record.get("currentEps")),
                "target_eps": to_decimal(record.get("targetEps")),
                "current_pe": to_decimal(record.get("currentPe")),
                "historical_avg_pe": to_decimal(record.get("historicalAvgPe")),
                "mean_pe": to_decimal(record.get("meanPe")),
                "hold_years": to_int(record.get("holdYears")),
                "g_percent": to_decimal(record.get("gPercent")),
                "peg": to_decimal(record.get("peg")),
                "annualized_return_percent": to_decimal(
                    record.get("annualizedReturnPercent")
                ),
                "pass_count": to_int(record.get("passCount")),
                "total_conditions": to_int(record.get("totalConditions")),
                "all_passed": bool(record.get("allPassed")),
                "details": record.get("details") if isinstance(record.get("details"), list) else [],
                "created_at": to_datetime(record.get("createdAt")) or datetime.now(timezone.utc),
            }
        )

    return rows, stats


def collect_msci_meta(data_dir: Path) -> list[dict]:
    """msci.json 顶层元信息 -> msci_meta（单例 id=1）。"""
    payload = load_json(data_dir / "msci.json", dict)
    if not payload:
        return []
    return [
        {
            "id": 1,
            "file_name": to_text(payload.get("fileName"), 255),
            "imported_at": to_datetime(payload.get("importedAt")),
            "updated_at": to_datetime(payload.get("updatedAt")),
            "analyzed_at": to_datetime(payload.get("analyzedAt")),
            "columns": payload.get("columns") if isinstance(payload.get("columns"), list) else [],
        }
    ]


def collect_grid_rows(data_dir: Path) -> tuple[list[dict], list[dict], dict[str, int]]:
    """grid-strategies.json -> (策略行, 成交行)。

    原 JSON 把 trades 内嵌在策略里，这里拆表并顺带推导
    每档步长 / 单格资金 / 当前持仓 / 动态摊薄成本 / 累计套利。
    """
    items = load_json(data_dir / "grid-strategies.json", list)
    strategies: list[dict[str, Any]] = []
    trade_rows: list[dict[str, Any]] = []
    stats = {"strategies": 0, "trades": 0, "skipped": 0}

    for index, record in enumerate(items):
        if not isinstance(record, dict):
            stats["skipped"] += 1
            continue
        code = to_text(record.get("symbol") or record.get("code"), 8)
        if not code:
            stats["skipped"] += 1
            continue

        strategy_id = to_text(record.get("id"), 40) or f"grid_{code}_{index}"
        base_price = to_decimal(record.get("basePrice"))
        upper = to_decimal(record.get("upperPrice"))
        lower = to_decimal(record.get("lowerPrice"))
        mode = to_text(record.get("mode"), 16) or "arithmetic"
        grid_count = to_int(record.get("gridCount")) or 10
        shares_per_grid = to_int(record.get("sharesPerGrid")) or 100
        initial_shares = to_int(record.get("initialShares")) or 0
        initial_cost = to_decimal(record.get("initialCost")) or base_price

        # 步长：等差取价差，等比取每档比例
        step_value: Decimal | None = None
        step_pct: Decimal | None = None
        if upper is not None and lower is not None and grid_count > 0 and upper > lower:
            if mode == "geometric" and lower > 0:
                ratio = float(upper / lower) ** (1 / grid_count)
                step_pct = ((Decimal(str(ratio)) - 1) * 100).quantize(RATIO)
            else:
                step_value = ((upper - lower) / grid_count).quantize(RATIO)

        grid_amount = (
            (base_price * shares_per_grid).quantize(CENT) if base_price is not None else None
        )

        # 逐笔成交
        initial_amount = (initial_cost or Decimal("0")) * initial_shares
        buy_cash = Decimal("0")
        sell_cash = Decimal("0")
        buy_shares = 0
        sell_shares = 0
        realized_total = Decimal("0")

        for t_index, trade in enumerate(record.get("trades") or []):
            if not isinstance(trade, dict):
                continue
            side = to_text(trade.get("type") or trade.get("side"), 8)
            if side not in {"buy", "sell"}:
                stats["skipped"] += 1
                continue
            price = to_decimal(trade.get("price"))
            shares = to_int(trade.get("shares")) or 0
            fee = to_decimal(trade.get("fee")) or Decimal("0")
            profit = to_decimal(trade.get("realizedProfit")) or Decimal("0")
            executed_at = to_datetime(trade.get("timestamp") or trade.get("executedAt"))

            if price is not None:
                if side == "buy":
                    buy_cash += price * shares + fee
                    buy_shares += shares
                else:
                    sell_cash += price * shares - fee
                    sell_shares += shares
            if side == "sell":
                realized_total += profit

            trade_rows.append(
                {
                    "id": to_text(trade.get("id"), 40) or f"{strategy_id[:32]}_t{t_index}",
                    "strategy_id": strategy_id,
                    "code": code,
                    "executed_at": executed_at,
                    "side": side,
                    "price": price,
                    "shares": shares,
                    "fee": fee,
                    "realized_profit": profit,
                    "level_index": None,
                }
            )
            stats["trades"] += 1

        current_shares = initial_shares + buy_shares - sell_shares
        net_invested = initial_amount + buy_cash - sell_cash
        diluted_cost = (
            max(Decimal("0"), net_invested / current_shares).quantize(RATIO)
            if current_shares > 0
            else None
        )

        strategies.append(
            {
                "id": strategy_id,
                "code": code,
                "name": to_text(record.get("name"), 64),
                "base_price": base_price,
                "upper_price": upper,
                "lower_price": lower,
                "mode": mode,
                "grid_count": grid_count,
                "step_value": step_value,
                "step_pct": step_pct,
                "grid_amount": grid_amount,
                "shares_per_grid": shares_per_grid,
                "initial_shares": initial_shares,
                "initial_cost": initial_cost,
                "commission_rate": to_decimal(record.get("commissionRate")) or Decimal("0.0001"),
                "stamp_duty_rate": to_decimal(record.get("stampDutyRate")) or Decimal("0"),
                "free_five": bool(record.get("freeFive")),
                "last_price": to_decimal(record.get("lastPrice")) or base_price,
                "status": to_text(record.get("status"), 16) or "active",
                "current_shares": current_shares,
                "diluted_cost": diluted_cost,
                "realized_profit": realized_total.quantize(CENT),
                "note": to_text(record.get("note")),
            }
        )
        stats["strategies"] += 1

    return strategies, trade_rows, stats


# --------------------------------------------------------------------------- #
# 批量 upsert
# --------------------------------------------------------------------------- #
def update_fields_for(rows: list[dict[str, Any]], exclude: set[str]) -> list[str]:
    """取所有行字段的并集（不能只看首行，否则跨来源字段在冲突更新时会被漏掉）。"""
    fields: set[str] = set()
    for row in rows:
        fields.update(row)
    return sorted(fields - exclude)


async def upsert(
    session,
    model,
    rows: list[dict[str, Any]],
    conflict_columns: list[str],
    update_fields: list[str],
    chunk_size: int = DEFAULT_CHUNK_SIZE,
    extra_set: dict[str, Any] | None = None,
) -> int:
    """分批 upsert；缺失字段以 NULL 参与 SQL，但用 COALESCE 保留库中已有值。"""
    if not rows:
        return 0

    table = model.__table__
    allowed = set(table.columns.keys())
    sanitized: list[dict[str, Any]] = []
    for row in rows:
        unknown = set(row) - allowed
        if unknown:
            log(f"⚠️  {table.name} 丢弃未知字段：{sorted(unknown)}")
        sanitized.append({k: v for k, v in row.items() if k in allowed})

    written = 0
    for start in range(0, len(sanitized), chunk_size):
        chunk = sanitized[start : start + chunk_size]
        keys = {key for row in chunk for key in row}
        normalized = [{key: row.get(key) for key in keys} for row in chunk]

        statement = pg_insert(table).values(normalized)
        set_map: dict[str, Any] = {
            field: func.coalesce(statement.excluded[field], table.c[field])
            for field in update_fields
        }
        if extra_set:
            set_map.update(extra_set)

        statement = statement.on_conflict_do_update(
            index_elements=conflict_columns, set_=set_map
        )
        await session.execute(statement)
        written += len(chunk)
    return written


# --------------------------------------------------------------------------- #
# 主流程
# --------------------------------------------------------------------------- #
async def migrate(data_dir: Path, dry_run: bool, chunk_size: int) -> int:
    log(f"数据目录：{data_dir}")
    if not data_dir.exists():
        log(f"❌ 数据目录不存在：{data_dir}")
        return 1

    position_rows, portfolio_patches, position_stats = collect_position_rows(data_dir)
    stock_rows, stock_stats = collect_stock_rows(data_dir, portfolio_patches)
    trade_rows, trade_stats = collect_trade_rows(data_dir)
    watchlist_rows, watchlist_stats = collect_watchlist_rows(data_dir)
    msci_meta_rows = collect_msci_meta(data_dir)
    strategy_rows, grid_trade_rows, grid_stats = collect_grid_rows(data_dir)

    log(
        f"清洗完成：stocks {len(stock_rows)} 行（state-owned {stock_stats['state_owned']} / "
        f"msci {stock_stats['msci']} / 持仓 {stock_stats['portfolio']} / 自选 {stock_stats['watchlist']}）"
    )
    if stock_stats["no_name"]:
        log(f"⚠️  {stock_stats['no_name']} 行缺少名称，已跳过（stocks.name 非空）")
    log(
        f"positions {len(position_rows)} 行（原始 {position_stats['raw']} 条，"
        f"同代码合并 {position_stats['duplicates']} 条）"
    )
    log(
        f"trade_history {len(trade_rows)} 行（原始 {trade_stats['raw']} 条，"
        f"派生盈亏 {trade_stats['derived']} 条）"
    )
    log(f"watchlist_items {len(watchlist_rows)} 行（原始 {watchlist_stats['raw']} 条）")
    log(f"msci_meta {len(msci_meta_rows)} 行")
    log(
        f"grid_strategies {len(strategy_rows)} 行 / grid_trade_records {len(grid_trade_rows)} 行"
    )

    if dry_run:
        log("--dry-run 模式：仅清洗，不写数据库")
        print("-" * 46)
        print(f"{'表名':<22}{'待写入行数':>12}")
        print("-" * 46)
        for name, count in (
            ("stocks", len(stock_rows)),
            ("positions", len(position_rows)),
            ("trade_history", len(trade_rows)),
            ("watchlist_items", len(watchlist_rows)),
            ("msci_meta", len(msci_meta_rows)),
            ("grid_strategies", len(strategy_rows)),
            ("grid_trade_records", len(grid_trade_rows)),
        ):
            print(f"{name:<22}{count:>12}")
        return 0

    failures: list[str] = []
    async with AsyncSessionLocal() as session:
        steps = [
            ("stocks", Stock, stock_rows, ["code"], {"code"}),
            ("positions", Position, position_rows, ["code"], {"code"}),
            ("trade_history", TradeHistory, trade_rows, ["source_key"], {"source_key"}),
            ("watchlist_items", WatchlistItem, watchlist_rows, ["id"], {"id"}),
            ("msci_meta", MsciMeta, msci_meta_rows, ["id"], {"id"}),
            ("grid_strategies", GridStrategy, strategy_rows, ["id"], {"id"}),
            ("grid_trade_records", GridTradeRecord, grid_trade_rows, ["id"], {"id"}),
        ]

        for name, model, rows, conflict, exclude in steps:
            if not rows:
                log(f"跳过 {name}：无数据")
                continue
            try:
                extra_set = (
                    {"updated_at": func.now()}
                    if "updated_at" in model.__table__.columns
                    else None
                )
                count = await upsert(
                    session,
                    model,
                    rows,
                    conflict,
                    update_fields_for(rows, exclude),
                    chunk_size,
                    extra_set,
                )
                await session.commit()
                log(f"✅ {name:<20} 写入 {count} 条")
            except Exception as exc:  # noqa: BLE001 - 单表失败不影响其它表
                await session.rollback()
                failures.append(name)
                log(f"❌ {name} 写入失败：{type(exc).__name__}: {exc}")

        print("-" * 46)
        print(f"{'表名':<22}{'记录数':>12}")
        print("-" * 46)
        for model in (Stock, Position, TradeHistory, WatchlistItem, MsciMeta, GridStrategy, GridTradeRecord):
            try:
                total = await session.scalar(
                    select(func.count()).select_from(model.__table__)
                )
                print(f"{model.__tablename__:<22}{total:>12}")
            except Exception as exc:  # noqa: BLE001
                print(f"{model.__tablename__:<22}{'查询失败':>12}  {exc}")

    await engine.dispose()

    if failures:
        log(f"迁移结束，但有 {len(failures)} 张表失败：{', '.join(failures)}")
        return 1
    log("迁移完成 ✅")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="历史 JSON 清洗并导入 NAS PostgreSQL")
    parser.add_argument("--data-dir", type=Path, default=DEFAULT_DATA_DIR, help="JSON 数据目录")
    parser.add_argument("--dry-run", action="store_true", help="只清洗不写库")
    parser.add_argument("--chunk-size", type=int, default=DEFAULT_CHUNK_SIZE, help="批量写入每批行数")
    args = parser.parse_args()

    if not args.dry_run:
        log(
            f"目标库：{settings.POSTGRES_HOST}:{settings.POSTGRES_PORT}/{settings.POSTGRES_DB}"
        )

    try:
        return asyncio.run(migrate(args.data_dir, args.dry_run, args.chunk_size))
    except Exception as exc:  # noqa: BLE001
        print(f"\n迁移中断 ❌ {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
