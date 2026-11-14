"""网格配对回合（Round-Trip Cycle）引擎。

核心思想：把「单边成交流水」折叠成「一开一平的回合」，用回合状态表达网格真实持仓：

- LONG_FIRST  + HOLDING_LONG  ：下方低吸区先买后卖（买入开仓，等价格涨到档位卖出价平仓）
- SHORT_FIRST + WAITING_REBUY ：上方底仓先卖后买（卖出底仓开仓，等回落接回平仓）
- CLOSED                       ：一买一卖闭环，只有此时才产生真实净利

为什么必须这样：底仓高抛后价格若继续上涨，用「卖出价 - 底仓成本」会算出虚假亏损；
回合制只认「同一回合内的买价与卖价」，未接回前不计任何已实现盈亏。

配对顺序采用 LIFO（最近开仓的回合优先被平掉），符合网格「逐格滚动」的直觉。
"""

from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import Any

MAX_LEVELS = 200

LONG_FIRST = "LONG_FIRST"
SHORT_FIRST = "SHORT_FIRST"

HOLDING_LONG = "HOLDING_LONG"
WAITING_REBUY = "WAITING_REBUY"
CLOSED = "CLOSED"

STATUS_LABELS = {
    HOLDING_LONG: "持仓待卖",
    WAITING_REBUY: "高抛待接回",
    CLOSED: "已闭环结清",
}
TYPE_LABELS = {LONG_FIRST: "低吸先买后卖", SHORT_FIRST: "底仓高抛倒差价"}


def round3(value: Any) -> float:
    """与前端 Math.round(x*1000)/1000 对齐（正数四舍五入）。"""
    number = float(value or 0)
    return math.floor(number * 1000 + 0.5) / 1000


def _f(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def build_level_prices(params: dict[str, Any]) -> list[dict[str, Any]]:
    """按策略参数重建档位价格（与前端 gridCalculator 保持同一口径）。

    返回升序 [{index, buy_price, sell_price}]，index 从 1 开始。
    """
    gen_mode = str(params.get("gen_mode") or "range")
    base = _f(params.get("base_price"))
    upper = _f(params.get("upper_price"))
    lower = _f(params.get("lower_price"))
    grid_count = int(params.get("grid_count") or 0)
    mode = str(params.get("mode") or "arithmetic")

    prices: list[float] = []
    if gen_mode == "step":
        # 正交组合：等比→复利百分比；等差→单利百分比（固定价差）或固定元数
        raw_type = str(params.get("step_type") or "percent").lower()
        step_type = "amount" if raw_type in ("amount", "fixed") else "percent"
        step_value = _f(params.get("step_input"))
        up = max(0, int(params.get("grids_up") or 0))
        down = max(0, int(params.get("grids_down") or 0))
        geometric = str(params.get("mode") or mode) != "arithmetic"
        if base <= 0 or step_value <= 0 or up + down < 1:
            return []

        delta = 0.0
        if not geometric:
            delta = round3(step_value) if step_type == "amount" else round3(base * step_value / 100)

        def offset_price(offset: int) -> float:
            if geometric:
                return round3(base * (1 + step_value / 100) ** offset)
            return round3(base + offset * delta)

        for i in range(down, 0, -1):
            prices.append(offset_price(-i))
        prices.append(round3(base))
        for i in range(1, up + 1):
            prices.append(offset_price(i))
    else:
        if base <= 0 or upper <= lower or grid_count <= 0:
            return []
        grid_count = min(grid_count, MAX_LEVELS)
        if mode == "geometric" and lower > 0:
            ratio = (upper / lower) ** (1 / grid_count)
            for i in range(grid_count + 1):
                prices.append(round3(upper if i == grid_count else lower * ratio**i))
        else:
            step = (upper - lower) / grid_count
            for i in range(grid_count + 1):
                prices.append(round3(upper if i == grid_count else lower + step * i))

    # 去重（与前端一致：高价股四舍五入后可能重叠）
    deduped: list[float] = []
    for price in prices:
        if price <= 0:
            continue
        if not deduped or price > deduped[-1]:
            deduped.append(price)

    return [
        {"index": i + 1, "buy_price": deduped[i], "sell_price": deduped[i + 1]}
        for i in range(len(deduped) - 1)
    ]


def _sort_key(trade: dict[str, Any]) -> tuple:
    executed = trade.get("executed_at")
    if isinstance(executed, datetime):
        if executed.tzinfo is None:
            executed = executed.replace(tzinfo=timezone.utc)
        stamp = executed.timestamp()
    else:
        stamp = 0.0
    return (stamp, str(trade.get("id") or ""))


def rebuild_cycles(trades: list[dict[str, Any]], levels: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """按时间顺序重放成交流水，生成回合列表。

    trades 需包含：id / side('buy'|'sell') / price / shares / fee / executed_at /
    level_index / pair_cycle_id（可为 None）。
    """
    level_map = {level["index"]: level for level in levels}
    open_cycles: list[dict[str, Any]] = []
    cycles: list[dict[str, Any]] = []

    for trade in sorted(trades, key=_sort_key):
        side = "buy" if str(trade.get("side")).lower() == "buy" else "sell"
        shares = int(trade.get("shares") or 0)
        price = _f(trade.get("price"))
        fee = _f(trade.get("fee"))
        level_index = trade.get("level_index")
        level_index = int(level_index) if level_index is not None else None

        if shares <= 0 or price <= 0:
            continue

        # 1) 优先按手工指定的回合平仓
        candidate = None
        explicit_id = (trade.get("pair_cycle_id") or "").strip()
        if explicit_id:
            for cycle in open_cycles:
                if cycle["id"] == explicit_id and str(cycle["open_direction"]).lower() != side:
                    candidate = cycle
                    break

        # 2) 未指定（或指定失效）时，同档位 LIFO 自动配对
        if candidate is None:
            same_level = [
                cycle
                for cycle in open_cycles
                if str(cycle["open_direction"]).lower() != side
                and (level_index is None or cycle["grid_level"] == level_index)
            ]
            if same_level:
                candidate = max(same_level, key=lambda c: _sort_key({"executed_at": c["open_time"], "id": c["id"]}))

        if candidate is not None:
            matched = min(int(candidate["open_shares"] or 0), shares)
            open_shares = int(candidate["open_shares"] or 0)
            open_fee = _f(candidate["open_fee"])
            open_price = _f(candidate["open_price"])
            fee_ratio = matched / open_shares if open_shares else 1.0
            open_fee_matched = open_fee * fee_ratio
            close_fee_matched = fee * (matched / shares) if shares else 0.0

            if side == "sell":
                buy_price, sell_price = open_price, price
                buy_fee, sell_fee = open_fee_matched, close_fee_matched
            else:
                buy_price, sell_price = price, open_price
                buy_fee, sell_fee = close_fee_matched, open_fee_matched

            net_profit = round(
                (sell_price - buy_price) * matched - buy_fee - sell_fee, 2
            )

            candidate.update(
                {
                    "status": CLOSED,
                    "close_trade_id": trade.get("id"),
                    "close_price": price,
                    "close_shares": matched,
                    "close_fee": round(close_fee_matched, 2),
                    "close_time": trade.get("executed_at"),
                    "net_profit": net_profit,
                }
            )
            open_cycles.remove(candidate)
            continue

        # 3) 无配对 → 新开一笔回合
        level = level_map.get(level_index or 0)
        if side == "buy":
            cycle_type, status = LONG_FIRST, HOLDING_LONG
            target = level["sell_price"] if level else None
        else:
            cycle_type, status = SHORT_FIRST, WAITING_REBUY
            target = level["buy_price"] if level else None

        cycle = {
            "id": f"cyc_{trade.get('id')}"[:40],
            "grid_level": level_index,
            "cycle_type": cycle_type,
            "status": status,
            "open_trade_id": trade.get("id"),
            "open_direction": side.upper(),
            "open_price": price,
            "open_shares": shares,
            "open_fee": round(fee, 2),
            "open_time": trade.get("executed_at"),
            "target_close_price": target,
            "close_trade_id": None,
            "close_price": None,
            "close_shares": None,
            "close_fee": None,
            "close_time": None,
            "net_profit": None,
        }
        cycles.append(cycle)
        open_cycles.append(cycle)

    return cycles


def summarize(cycles: list[dict[str, Any]]) -> dict[str, Any]:
    """回合口径的汇总指标。"""
    closed = [c for c in cycles if c["status"] == CLOSED]
    open_cycles = [c for c in cycles if c["status"] != CLOSED]
    realized = round(sum(_f(c.get("net_profit")) for c in closed), 2)
    return {
        "realized_profit": realized,
        "closed_count": len(closed),
        "open_count": len(open_cycles),
        "holding_count": len([c for c in open_cycles if c["status"] == HOLDING_LONG]),
        "waiting_rebuy_count": len([c for c in open_cycles if c["status"] == WAITING_REBUY]),
    }
