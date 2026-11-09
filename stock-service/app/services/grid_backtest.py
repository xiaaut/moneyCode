"""网格交易历史回测引擎。

严格「防未来函数」逐日撮合状态机：
- 从 stock_kline_daily 读标的日 K（按 trade_date 正序）；
- 以 base_price 为中心，向下/向上各 max_grids 档等比网格（step_pct 步长）；
- 每天用 low 判定「跌破买入线 → 买入」，用 high 判定「涨破卖出线 → 卖出」；
- 买入档 i 与卖出档 i 对称配对，一轮买卖赚 step_pct 差价；
- 记录交易流水、每日账户资产序列，并对比买入并持有基准。
"""

from __future__ import annotations

from datetime import date
from typing import Any

from sqlalchemy import select

from app.db.session import AsyncSessionLocal
from app.models import StockKlineDaily


def _f(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _round(value: float, digits: int = 4) -> float:
    return round(value, digits)


async def load_klines(code: str, start: date, end: date) -> list[dict[str, Any]]:
    """读取标的日 K（正序），返回 [{date, open, high, low, close}, ...]。"""
    async with AsyncSessionLocal() as session:
        rows = (
            await session.scalars(
                select(StockKlineDaily)
                .where(
                    StockKlineDaily.code == code,
                    StockKlineDaily.trade_date >= start,
                    StockKlineDaily.trade_date <= end,
                )
                .order_by(StockKlineDaily.trade_date)
            )
        ).all()
    return [
        {
            "date": str(row.trade_date),
            "open": _f(row.open),
            "high": _f(row.high),
            "low": _f(row.low),
            "close": _f(row.close),
        }
        for row in rows
        if _f(row.open) and _f(row.high) and _f(row.low) and _f(row.close)
    ]


def run_backtest(klines: list[dict[str, Any]], params: dict[str, Any]) -> dict[str, Any]:
    """执行网格回测，返回绩效指标 + 每日净值 + 交易流水。"""
    initial_cash = float(params.get("initial_cash", 100000))
    base_price = float(params.get("base_price", 0))
    step_pct = float(params.get("step_pct", 1.0))
    grid_amount = float(params.get("grid_amount", 10000))
    max_grids = int(params.get("max_grids", 10))
    commission_rate = float(params.get("commission_rate", 0.0001))

    step = step_pct / 100.0
    if base_price <= 0 or step <= 0 or max_grids <= 0:
        return _empty_result()

    # 网格档位：买入线（向下）/ 卖出线（向上），对称配对
    levels: list[dict[str, Any]] = []
    for i in range(1, max_grids + 1):
        buy_price = base_price * (1 - step) ** i
        sell_price = base_price * (1 + step) ** i
        levels.append({"level": i, "buy": buy_price, "sell": sell_price, "filled": False, "shares": 0})

    cash = initial_cash
    shares = 0
    trades: list[dict[str, Any]] = []
    equity: list[dict[str, Any]] = []
    realized_total = 0.0
    buy_count = 0
    sell_count = 0

    for k in klines:
        low = k["low"]
        high = k["high"]
        close = k["close"]

        # 1) 买入：当天最低价跌破空档买入线
        for lv in levels:
            if lv["filled"]:
                continue
            if low <= lv["buy"]:
                n_shares = int(grid_amount / lv["buy"] / 100) * 100
                if n_shares < 100:
                    n_shares = 100
                cost = n_shares * lv["buy"]
                fee = cost * commission_rate
                if cash >= cost + fee:
                    cash -= cost + fee
                    shares += n_shares
                    lv["filled"] = True
                    lv["shares"] = n_shares
                    lv["fee"] = fee
                    buy_count += 1
                    trades.append(
                        {
                            "date": k["date"],
                            "side": "BUY",
                            "price": _round(lv["buy"]),
                            "shares": n_shares,
                            "amount": _round(cost, 2),
                            "fee": _round(fee, 2),
                            "level": lv["level"],
                        }
                    )

        # 2) 卖出：当天最高价涨破持仓档卖出线
        for lv in levels:
            if not lv["filled"]:
                continue
            if high >= lv["sell"]:
                n_shares = lv["shares"]
                proceeds = n_shares * lv["sell"]
                fee = proceeds * commission_rate
                realized = (lv["sell"] - lv["buy"]) * n_shares - float(lv.get("fee", 0)) - fee
                cash += proceeds - fee
                shares -= n_shares
                realized_total += realized
                lv["filled"] = False
                lv["shares"] = 0
                sell_count += 1
                trades.append(
                    {
                        "date": k["date"],
                        "side": "SELL",
                        "price": _round(lv["sell"]),
                        "shares": n_shares,
                        "amount": _round(proceeds, 2),
                        "fee": _round(fee, 2),
                        "level": lv["level"],
                        "profit": _round(realized, 2),
                    }
                )

        market_value = shares * close
        equity.append(
            {
                "date": k["date"],
                "cash": _round(cash, 2),
                "shares": shares,
                "market_value": _round(market_value, 2),
                "total_equity": _round(cash + market_value, 2),
            }
        )

    # 基准：买入并持有（起始日全仓买入）
    benchmark_equity: list[dict[str, Any]] = []
    if klines:
        first_close = klines[0]["close"]
        bench_shares = int(initial_cash / first_close / 100) * 100
        bench_fee = bench_shares * first_close * commission_rate
        bench_cash = initial_cash - bench_shares * first_close - bench_fee
        for k in klines:
            mv = bench_shares * k["close"]
            benchmark_equity.append(
                {
                    "date": k["date"],
                    "total_equity": _round(bench_cash + mv, 2),
                }
            )

    metrics = _compute_metrics(
        initial_cash, equity, benchmark_equity, realized_total, buy_count, sell_count
    )

    return {
        "code": params.get("code"),
        "start_date": params.get("start_date"),
        "end_date": params.get("end_date"),
        "metrics": metrics,
        "equity": equity,
        "benchmark_equity": benchmark_equity,
        "trades": trades,
    }


def _compute_metrics(
    initial_cash: float,
    equity: list[dict[str, Any]],
    benchmark_equity: list[dict[str, Any]],
    realized_total: float,
    buy_count: int,
    sell_count: int,
) -> dict[str, Any]:
    if not equity:
        return _empty_metrics()

    final_equity = equity[-1]["total_equity"]
    total_return = (final_equity - initial_cash) / initial_cash * 100

    final_benchmark = benchmark_equity[-1]["total_equity"] if benchmark_equity else initial_cash
    benchmark_return = (final_benchmark - initial_cash) / initial_cash * 100

    # CAGR
    days = len(equity)
    years = days / 244.0  # 每年约 244 个交易日
    annualized = 0.0
    if years > 0 and final_equity > 0:
        annualized = ((final_equity / initial_cash) ** (1 / years) - 1) * 100

    grid_dd = _max_drawdown([e["total_equity"] for e in equity])
    bench_dd = _max_drawdown([e["total_equity"] for e in benchmark_equity]) if benchmark_equity else 0.0

    return {
        "total_return_pct": _round(total_return, 2),
        "benchmark_return_pct": _round(benchmark_return, 2),
        "excess_return_pct": _round(total_return - benchmark_return, 2),
        "annualized_return_pct": _round(annualized, 2),
        "max_drawdown_pct": _round(grid_dd, 2),
        "benchmark_max_drawdown_pct": _round(bench_dd, 2),
        "arbitrage_count": sell_count,
        "buy_count": buy_count,
        "sell_count": sell_count,
        "total_profit": _round(realized_total, 2),
        "final_equity": _round(final_equity, 2),
    }


def _max_drawdown(values: list[float]) -> float:
    if not values:
        return 0.0
    peak = values[0]
    max_dd = 0.0
    for value in values:
        if value > peak:
            peak = value
        if peak > 0:
            dd = (peak - value) / peak * 100
            if dd > max_dd:
                max_dd = dd
    return max_dd


def _empty_metrics() -> dict[str, Any]:
    return {
        "total_return_pct": 0.0,
        "benchmark_return_pct": 0.0,
        "excess_return_pct": 0.0,
        "annualized_return_pct": 0.0,
        "max_drawdown_pct": 0.0,
        "benchmark_max_drawdown_pct": 0.0,
        "arbitrage_count": 0,
        "buy_count": 0,
        "sell_count": 0,
        "total_profit": 0.0,
        "final_equity": 0.0,
    }


def _empty_result() -> dict[str, Any]:
    return {
        "metrics": _empty_metrics(),
        "equity": [],
        "benchmark_equity": [],
        "trades": [],
    }
