"""四维联动调仓雷达与补仓沙盘引擎。

聚合 4 大底层数据源（positions / watchlist / stock_kline_daily / stock_financial_statements
+ stocks 的 ROE），为每只持仓/自选标的生成：
- 5 年前复权价格水位（最高/最低/分位/MA20/MA250/乖离率）
- 张新民健康分 + 六大雷区红黄绿灯（health_score 不落库，读库后 finalize 重算）
- 4 类调仓信号（击球加仓 / 止盈落袋 / 财报排雷 / 仓位与深套）
"""

from __future__ import annotations

import asyncio
import logging
from datetime import date, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.adapters.ths_client import is_fund_ticker
from app.db.session import AsyncSessionLocal
from app.models import Position, RebalancePlan, Stock, WatchlistItem
from app.models.financial import ALL_MONEY_FIELDS
from app.services import financial_analysis

logger = logging.getLogger(__name__)

SIGNAL_META = {
    "BUY_ZONE": {"label": "🎯 击球加仓", "badge": "击球加仓区", "color": "#10b981"},
    "PROFIT_ZONE": {"label": "💰 止盈落袋", "badge": "止盈落袋区", "color": "#3b82f6"},
    "RISK_ZONE": {"label": "🚨 财报排雷", "badge": "财报排雷区", "color": "#ef4444"},
    "REBALANCE_ZONE": {"label": "⚖️ 仓位与深套", "badge": "仓位与深套区", "color": "#f59e0b"},
}
# 主信号优先级（越小越优先展示）
_SIGNAL_PRIORITY = {"RISK_ZONE": 0, "REBALANCE_ZONE": 1, "BUY_ZONE": 2, "PROFIT_ZONE": 3}


def _f(value: Any) -> float | None:
    if value is None:
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return None if result != result else result


# --------------------------------------------------------------------------- #
# 数据读取
# --------------------------------------------------------------------------- #
async def _load_financial_health(code: str) -> dict[str, Any]:
    """读财报多期并 finalize 重算健康分/雷达。返回健康分 + 驱动类型 + 红黄绿灯。"""
    from app.models import StockFinancialStatement

    async with AsyncSessionLocal() as session:
        rows = (
            await session.scalars(
                select(StockFinancialStatement)
                .where(StockFinancialStatement.code == code)
                .order_by(StockFinancialStatement.report_date.desc())
                .limit(5)
            )
        ).all()

    if not rows:
        return {"checked": False, "health_score": None, "driver_type": None, "danger_count": 0, "warn_count": 0, "report_label": None}

    items: list[dict[str, Any]] = []
    for row in rows:
        item = {
            "code": row.code,
            "report_date": str(row.report_date),
            "report_label": row.report_label,
            "report_type": row.report_type,
            "source": row.source,
        }
        for field in ALL_MONEY_FIELDS:
            value = getattr(row, field, None)
            item[field] = float(value) if value is not None else None
        item["derived_metrics"] = row.derived_metrics or {}
        items.append(item)

    financial_analysis.analyze(items)
    latest = items[0]
    radars = latest.get("radar_checks") or []
    danger = sum(1 for r in radars if r.get("status") == "danger")
    warn = sum(1 for r in radars if r.get("status") == "warn")
    return {
        "checked": True,
        "health_score": latest.get("health_score"),
        "driver_type": (latest.get("derived_metrics") or {}).get("driver_type"),
        "danger_count": danger,
        "warn_count": warn,
        "report_label": latest.get("report_label"),
    }


async def _load_kline_stats(code: str) -> dict[str, Any]:
    """5 年前复权价格水位 + MA20/MA250 + 最新价。"""
    from app.models import StockKlineDaily

    five_years_ago = date.today() - timedelta(days=365 * 5)
    async with AsyncSessionLocal() as session:
        high = await session.scalar(
            select(func.max(StockKlineDaily.qfq_close)).where(
                StockKlineDaily.code == code, StockKlineDaily.trade_date >= five_years_ago
            )
        )
        low = await session.scalar(
            select(func.min(StockKlineDaily.qfq_close)).where(
                StockKlineDaily.code == code, StockKlineDaily.trade_date >= five_years_ago
            )
        )
        closes = (
            await session.scalars(
                select(StockKlineDaily.qfq_close)
                .where(StockKlineDaily.code == code)
                .order_by(StockKlineDaily.trade_date.desc())
                .limit(250)
            )
        ).all()

    high = _f(high)
    low = _f(low)
    if not closes:
        return {"high_5y": high, "low_5y": low, "percentile_5y": None, "ma20": None, "ma250": None, "bias_ma20": None, "qfq_price": None}

    qfq_price = _f(closes[0])
    ma20 = round(sum(_f(c) for c in closes[:20]) / min(len(closes), 20), 3) if closes[:20] else None
    ma250 = round(sum(_f(c) for c in closes[:250]) / len(closes[:250]), 3) if closes[:250] else None

    percentile = None
    if high is not None and low is not None and qfq_price is not None and high > low:
        percentile = round((qfq_price - low) / (high - low) * 100, 2)
    bias = None
    if ma20 and qfq_price:
        bias = round((qfq_price - ma20) / ma20 * 100, 2)

    return {
        "high_5y": high,
        "low_5y": low,
        "percentile_5y": percentile,
        "ma20": ma20,
        "ma250": ma250,
        "bias_ma20": bias,
        "qfq_price": qfq_price,
    }


async def _load_roe_map(codes: list[str]) -> dict[str, float | None]:
    if not codes:
        return {}
    async with AsyncSessionLocal() as session:
        rows = (
            await session.execute(
                select(Stock.code, Stock.roe).where(Stock.code.in_(codes))
            )
        ).all()
    return {r[0]: _f(r[1]) for r in rows}


async def _load_target_price() -> dict[str, float | None]:
    """选股策略目标价（单只，若有配置）。目标价 = target_eps * mean_pe。"""
    from app.models import StrategyConfig

    async with AsyncSessionLocal() as session:
        cfg = await session.get(StrategyConfig, 1)
    if not cfg or not cfg.stock_code or not cfg.target_eps or not cfg.mean_pe:
        return {}
    target = float(cfg.target_eps) * float(cfg.mean_pe)
    return {cfg.stock_code.strip(): round(target, 2)}


# --------------------------------------------------------------------------- #
# 信号扫描
# --------------------------------------------------------------------------- #
def _scan_signals(item: dict[str, Any]) -> list[str]:
    signals: list[str] = []
    k = item.get("kline") or {}
    percentile = k.get("percentile_5y")
    ma250 = k.get("ma250")
    bias = k.get("bias_ma20")
    qfq_price = k.get("qfq_price")
    health = item.get("health_score")
    roe = item.get("roe")
    profit = item.get("profit_ratio")
    weight = item.get("position_weight_pct")
    danger = item.get("danger_count") or 0
    warn = item.get("warn_count") or 0

    # 击球加仓区：低分位/低于年线 且 优质（健康分高或 ROE 高）
    low_position = (percentile is not None and percentile <= 30) or (ma250 and qfq_price and qfq_price <= ma250)
    quality = (health is not None and health >= 75) or (roe is not None and roe >= 12)
    if low_position and quality:
        signals.append("BUY_ZONE")

    # 止盈落袋区
    if (profit is not None and profit >= 20) or (percentile is not None and percentile >= 75) or (bias is not None and bias >= 12):
        signals.append("PROFIT_ZONE")

    # 财报排雷区
    if (health is not None and health < 65) or danger > 0 or warn > 2:
        signals.append("RISK_ZONE")

    # 仓位再平衡/深套自救
    if (weight is not None and weight > 20) or (profit is not None and profit <= -18):
        signals.append("REBALANCE_ZONE")

    return signals


def _build_advice(item: dict[str, Any], signals: list[str]) -> str:
    name = item.get("name") or item.get("code")
    k = item.get("kline") or {}
    percentile = k.get("percentile_5y")
    if "RISK_ZONE" in signals:
        return f"{name}基本面存疑（健康分 {item.get('health_score') if item.get('health_score') is not None else '待体检'}，{item.get('danger_count') or 0} 项红灯 / {item.get('warn_count') or 0} 项黄灯），建议逢高减仓或换股。"
    if "BUY_ZONE" in signals:
        return f"{name}处于历史低估值击球区（5 年分位 {percentile}%），优质白马可分批逢低加仓。"
    if "PROFIT_ZONE" in signals:
        return f"{name}浮盈较厚（{item.get('profit_ratio')}%），建议分批止盈锁定利润。"
    if "REBALANCE_ZONE" in signals:
        return f"{name}单票占比 {item.get('position_weight_pct')}% 或浮亏 {item.get('profit_ratio')}%，建议再平衡或利用网格做 T 摊薄成本。"
    return f"{name}当前四维水位健康，暂无需调仓，持续观察。"


# --------------------------------------------------------------------------- #
# Dashboard 聚合
# --------------------------------------------------------------------------- #
async def get_dashboard() -> dict[str, Any]:
    async with AsyncSessionLocal() as session:
        positions = (await session.scalars(select(Position))).all()
        watch_rows = (await session.execute(select(WatchlistItem.stock_code, WatchlistItem.stock_name))).all()

    pos_codes = [p.code for p in positions]
    watch_codes = [w[0] for w in watch_rows]
    watch_name_map = {w[0]: w[1] for w in watch_rows}
    all_codes = [c for c in dict.fromkeys([*pos_codes, *watch_codes]) if c and len(c) == 6]

    roe_map = await _load_roe_map(all_codes)
    target_map = await _load_target_price()

    # 并发聚合每只标的的四维数据
    async def _aggregate_one(code: str) -> dict[str, Any]:
        health_task = _load_financial_health(code)
        kline_task = _load_kline_stats(code)
        health, kline = await asyncio.gather(health_task, kline_task)
        return {"code": code, "health": health, "kline": kline}

    aggregated = await asyncio.gather(*(_aggregate_one(c) for c in all_codes))

    agg_map = {a["code"]: a for a in aggregated}

    # 总市值
    total_mv = sum(_f(p.market_value) or 0 for p in positions)
    pos_by_code = {p.code: p for p in positions}

    items: list[dict[str, Any]] = []
    for code in all_codes:
        agg = agg_map[code]
        health = agg["health"]
        kline = agg["kline"]
        p = pos_by_code.get(code)
        wname = watch_name_map.get(code)

        name = (p.name if p else None) or wname or code
        shares = int(p.shares or 0) if p else 0
        cost_price = _f(p.cost_price) if p else None
        current_price = _f(p.current_price) if p else kline.get("qfq_price")
        market_value = _f(p.market_value) if p else 0
        profit_ratio = _f(p.profit_ratio) if p else None
        weight = round(market_value / total_mv * 100, 2) if total_mv else 0

        item: dict[str, Any] = {
            "code": code,
            "name": name,
            "account_tag": None,  # positions 表当前单账户，多账户能力预留
            "is_position": p is not None,
            "shares": shares,
            "cost_price": cost_price,
            "current_price": current_price,
            "market_value": market_value,
            "profit_ratio": profit_ratio,
            "position_weight_pct": weight,
            "is_etf": is_fund_ticker(code),
            "health_score": health["health_score"],
            "driver_type": health["driver_type"],
            "checked": health["checked"],
            "danger_count": health["danger_count"],
            "warn_count": health["warn_count"],
            "report_label": health["report_label"],
            "roe": roe_map.get(code),
            "target_price": target_map.get(code),
            "kline": kline,
        }
        signals = _scan_signals(item)
        item["signals"] = signals
        item["signal_type"] = min(signals, key=lambda s: _SIGNAL_PRIORITY[s]) if signals else None
        item["signal_label"] = SIGNAL_META[item["signal_type"]]["label"] if item["signal_type"] else None
        item["advice"] = _build_advice(item, signals)
        items.append(item)

    # 排序：有信号的在前（按主信号优先级），无信号在后（按权重）
    items.sort(key=lambda x: (_SIGNAL_PRIORITY.get(x["signal_type"], 99), -(x.get("position_weight_pct") or 0)))

    # 仓位集中度（前 5 大重仓 + 其他）
    holding = [it for it in items if it["is_position"]]
    holding.sort(key=lambda x: -(x.get("market_value") or 0))
    top5 = holding[:5]
    others_mv = sum(x.get("market_value") or 0 for x in holding[5:])
    concentration = [{"code": x["code"], "name": x["name"], "market_value": x["market_value"], "weight_pct": x["position_weight_pct"]} for x in top5]
    if others_mv > 0 and total_mv:
        concentration.append({"code": "", "name": "其他", "market_value": others_mv, "weight_pct": round(others_mv / total_mv * 100, 2)})

    # 优质资产占比（健康分 >= 80 的持仓市值占比）
    quality_mv = sum((x.get("market_value") or 0) for x in holding if x.get("health_score") is not None and x["health_score"] >= 80)
    quality_ratio = round(quality_mv / total_mv * 100, 2) if total_mv else 0

    # 待处理信号数 + 已到价工单数（先做到价触发判定）
    price_map = {x["code"]: x["current_price"] for x in items}
    await _refresh_triggered_status([], price_map)
    plans = await _list_plan_rows()
    signal_count = sum(1 for x in items if x["signal_type"])
    triggered_count = sum(1 for pl in plans if pl["status"] == "TRIGGERED")

    summary = {
        "total_market_value": round(total_mv, 2),
        "total_codes": len(all_codes),
        "position_count": len(positions),
        "watchlist_count": len(watch_rows),
        "quality_ratio_pct": quality_ratio,
        "signal_count": signal_count,
        "triggered_plan_count": triggered_count,
        "concentration": concentration,
    }

    return {"summary": summary, "signals": items, "plans": plans}


# --------------------------------------------------------------------------- #
# 工单 CRUD
# --------------------------------------------------------------------------- #
def _plan_to_dict(row: RebalancePlan) -> dict[str, Any]:
    return {
        "id": row.id,
        "code": row.code,
        "name": row.name,
        "account_tag": row.account_tag,
        "action_type": row.action_type,
        "trigger_condition": row.trigger_condition,
        "target_price": _f(row.target_price),
        "plan_shares": row.plan_shares,
        "current_cost": _f(row.current_cost),
        "reason_tag": row.reason_tag,
        "notes": row.notes,
        "status": row.status,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


async def _list_plan_rows() -> list[dict[str, Any]]:
    async with AsyncSessionLocal() as session:
        rows = (
            await session.scalars(select(RebalancePlan).order_by(RebalancePlan.created_at.desc()))
        ).all()
    return [_plan_to_dict(r) for r in rows]


async def _refresh_triggered_status(plans: list[dict[str, Any]], price_map: dict[str, float | None]) -> None:
    """PENDING 工单若现价已越过触发价，自动置为 TRIGGERED（到价待执行）。"""
    async with AsyncSessionLocal() as session:
        rows = (
            await session.scalars(
                select(RebalancePlan).where(RebalancePlan.status == "PENDING")
            )
        ).all()
        changed = False
        for row in rows:
            price = price_map.get(row.code)
            if price is None or row.target_price is None:
                continue
            target = float(row.target_price)
            cond = row.trigger_condition or ""
            buy_side = row.action_type in ("BUY_ADD", "GRID_T")
            hit = (price <= target) if buy_side else (price >= target)
            if hit:
                row.status = "TRIGGERED"
                changed = True
        if changed:
            await session.commit()


def _normalize_plan(payload: dict[str, Any]) -> dict[str, Any]:
    """清洗工单输入，只保留有效字段。"""
    fields = {
        "code", "name", "account_tag", "action_type", "trigger_condition",
        "target_price", "plan_shares", "current_cost", "reason_tag", "notes",
    }
    clean: dict[str, Any] = {}
    for key in fields:
        if key in payload and payload[key] is not None:
            clean[key] = payload[key]
    if "action_type" not in clean or not clean["action_type"]:
        raise ValueError("action_type 不能为空")
    return clean


async def create_plan(payload: dict[str, Any]) -> dict[str, Any]:
    clean = _normalize_plan(payload)
    table = RebalancePlan.__table__
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            pg_insert(table).values(clean).returning(RebalancePlan.id)
        )
        await session.commit()
        plan_id = result.scalar_one()
    async with AsyncSessionLocal() as session:
        row = await session.get(RebalancePlan, plan_id)
        return _plan_to_dict(row)


async def update_plan(plan_id: int, payload: dict[str, Any]) -> dict[str, Any]:
    clean = _normalize_plan(payload)
    table = RebalancePlan.__table__
    # 支持更新 status
    if "status" in payload and payload["status"] is not None:
        clean["status"] = payload["status"]
    async with AsyncSessionLocal() as session:
        await session.execute(
            table.update().where(RebalancePlan.id == plan_id).values(clean)
        )
        await session.commit()
        row = await session.get(RebalancePlan, plan_id)
        return _plan_to_dict(row)


async def delete_plan(plan_id: int) -> None:
    async with AsyncSessionLocal() as session:
        row = await session.get(RebalancePlan, plan_id)
        if row:
            await session.delete(row)
            await session.commit()


async def mark_done(plan_id: int) -> dict[str, Any]:
    async with AsyncSessionLocal() as session:
        row = await session.get(RebalancePlan, plan_id)
        if row:
            row.status = "DONE"
            await session.commit()
            return _plan_to_dict(row)
        return {}
