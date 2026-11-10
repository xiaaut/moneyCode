"""同花顺金融数据 API 主通道适配器。

契约约定：
- 内部数据库统一使用 6 位代码；进出同花顺时用 ``to_ths_code`` 转换标准后缀。
- 场内基金（ETF/LOF）走 ``/api/fund/market/snapshot``，A 股走 ``/api/a-share/*``。
- 统一解包 ``ApiResponse`` 信封，返回 (code, data, message)。
- 需降级到免费通道的错误码见 ``FALLBACK_CODES``，需指数退避重试的见 ``RETRY_CODES``。

参考文档：https://fuyao.aicubes.cn/llms-full.txt
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from typing import Any

import httpx

from app.core.config import settings

logger = logging.getLogger(__name__)

# 触发降级到免费通道的错误码
FALLBACK_CODES = {2001, 2003, 3001, 3004, 4001, 5001, 5002, 5003}
# 触发指数退避重试的错误码（配合 HTTP 429）
RETRY_CODES = {4001, 5002, 5003}

TIMEOUT = httpx.Timeout(15.0, connect=6.0)
UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

# 批量接口每批代码上限
BATCH_LIMIT = 10
BATCH_INTERVAL = 0.2


def to_ths_code(code: str) -> str:
    """6 位代码 -> 同花顺 thscode（带交易所后缀）。"""
    pure = "".join(ch for ch in str(code) if ch.isdigit())[-6:]
    if not pure:
        return ""
    if pure[:1] in ("6", "5"):
        return f"{pure}.SH"
    if pure[:1] in ("4", "8") or pure[:2] == "92":
        return f"{pure}.BJ"
    return f"{pure}.SZ"


def from_ths_code(ths_code: str) -> str:
    """thscode -> 6 位代码。"""
    return "".join(ch for ch in str(ths_code) if ch.isdigit())[-6:]


def is_fund_ticker(code: str) -> bool:
    """判断 6 位代码是否为场内基金（ETF/LOF），需走 fund-market 接口。

    沪市基金：50/51/52/53/56/58 开头；深市基金：15/16 开头。
    """
    return code[:2] in {"50", "51", "52", "53", "56", "58", "15", "16"}


def latest_report_labels(now: datetime | None = None) -> list[str]:
    """返回最近 4 个报告期（新→旧），格式 ``yyyy-n``（1 一季报/2 中报/3 三季报/4 年报）。"""
    now = now or datetime.now()
    year, month = now.year, now.month
    if month >= 11:
        quarter = 3
    elif month >= 8:
        quarter = 2
    elif month >= 5:
        quarter = 1
    else:
        quarter = 4
        year -= 1

    labels: list[str] = []
    for _ in range(4):
        labels.append(f"{year}-{quarter}")
        quarter -= 1
        if quarter == 0:
            quarter = 4
            year -= 1
    return labels


def _num(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result


class ThsUnavailableError(Exception):
    """同花顺通道不可用（认证/权限/限流/上游异常），应降级到免费通道。"""


class ThsClient:
    """同花顺主通道客户端。"""

    def __init__(self) -> None:
        self.api_key = settings.THS_API_KEY
        self.base_url = settings.THS_BASE_URL.rstrip("/")

    # ------------------------------------------------------------------ #
    # 底层请求：统一解包信封 + 指数退避重试
    # ------------------------------------------------------------------ #
    async def _request(self, path: str, params: dict[str, Any]) -> tuple[int, dict | None, str]:
        """返回 (code, data, message)。重试耗尽后仍返回最后一次结果。"""
        url = f"{self.base_url}{path}"
        headers = {"X-api-key": self.api_key, "User-Agent": UA}
        last_code = -1
        last_message = ""

        for attempt in range(3):
            try:
                async with httpx.AsyncClient(timeout=TIMEOUT) as client:
                    resp = await client.get(url, params=params, headers=headers)
            except httpx.HTTPError as exc:
                last_code, last_message = -1, f"请求异常：{type(exc).__name__}"
                await asyncio.sleep(0.3 * (2**attempt))
                continue

            if resp.status_code == 429:
                last_code, last_message = 429, "HTTP 429 限流"
                await asyncio.sleep(0.5 * (2**attempt))
                continue

            try:
                payload = resp.json()
            except ValueError as exc:
                last_code, last_message = -1, f"响应非 JSON：{exc}"
                await asyncio.sleep(0.3 * (2**attempt))
                continue

            code = payload.get("code", -1)
            message = payload.get("message", "")
            if code in RETRY_CODES:
                last_code, last_message = code, message
                await asyncio.sleep(0.5 * (2**attempt))
                continue
            return int(code), payload.get("data"), str(message)

        return last_code, None, last_message

    async def _request_or_fail(self, path: str, params: dict[str, Any]) -> dict | None:
        """请求并把「应降级」的错误码转成异常，便于上层统一 fallback。"""
        code, data, message = await self._request(path, params)
        if code in FALLBACK_CODES or code == 429:
            raise ThsUnavailableError(f"同花顺 code={code} {message}")
        return data

    # ------------------------------------------------------------------ #
    # 行情 + 估值（A 股 + ETF 分流）
    # ------------------------------------------------------------------ #
    async def get_quotes(self, codes: list[str]) -> dict[str, dict[str, Any]]:
        """批量行情快照。返回 {6位代码: {name, price, changePct, pe, pb, volume, turnover}}。"""
        if not codes:
            return {}
        stocks = [c for c in codes if not is_fund_ticker(c)]
        funds = [c for c in codes if is_fund_ticker(c)]

        result: dict[str, dict[str, Any]] = {}
        if stocks:
            result.update(await self._stock_quotes(stocks))
        if funds:
            result.update(await self._fund_quotes(funds))
        return result

    async def _stock_quotes(self, codes: list[str]) -> dict[str, dict[str, Any]]:
        price_map = await self._batch_prices(codes)
        valuation_map = await self._batch_valuations(codes)
        result: dict[str, dict[str, Any]] = {}
        for code in codes:
            price = price_map.get(code) or {}
            valuation = valuation_map.get(code) or {}
            result[code] = {
                "name": valuation.get("name"),
                "price": price.get("price"),
                "changePct": price.get("changePct"),
                "volume": price.get("volume"),
                "turnover": price.get("turnover"),
                "pe": valuation.get("pe"),
                "pb": valuation.get("pb"),
            }
        return result

    async def _fund_quotes(self, codes: list[str]) -> dict[str, dict[str, Any]]:
        result: dict[str, dict[str, Any]] = {}
        ths_codes = [to_ths_code(c) for c in codes]
        for chunk in _chunks(ths_codes, 100):
            data = await self._request_or_fail(
                "/api/fund/market/snapshot", {"thscode": ",".join(chunk)}
            )
            for item in (data or {}).get("item") or []:
                code = from_ths_code(item.get("thscode") or "")
                result[code] = {
                    "name": None,  # 基金快照不含名称，由 stocks 表已有 name 兜底
                    "price": _num(item.get("last_price")),
                    "changePct": _num(item.get("price_change_ratio_pct")),
                    "volume": _num(item.get("volume")),
                    "turnover": _num(item.get("turnover")),
                    "pe": None,
                    "pb": None,
                }
        return result

    async def _batch_prices(self, codes: list[str]) -> dict[str, dict[str, Any]]:
        result: dict[str, dict[str, Any]] = {}
        ths_codes = [to_ths_code(c) for c in codes]
        for chunk in _chunks(ths_codes, 100):
            data = await self._request_or_fail(
                "/api/a-share/prices/snapshot", {"thscodes": ",".join(chunk)}
            )
            for item in (data or {}).get("item") or []:
                code = from_ths_code(item.get("thscode") or "")
                result[code] = {
                    "price": _num(item.get("last_price")),
                    "changePct": _num(item.get("price_change_ratio_pct")),
                    "volume": _num(item.get("volume")),
                    "turnover": _num(item.get("turnover")),
                }
        return result

    async def _batch_valuations(self, codes: list[str]) -> dict[str, dict[str, Any]]:
        result: dict[str, dict[str, Any]] = {}
        ths_codes = [to_ths_code(c) for c in codes]
        for chunk in _chunks(ths_codes, 100):
            data = await self._request_or_fail(
                "/api/a-share/valuations/snapshot", {"thscodes": ",".join(chunk)}
            )
            for item in (data or {}).get("item") or []:
                code = from_ths_code(item.get("thscode") or "")
                result[code] = {
                    "name": item.get("name"),
                    "pe": _num(item.get("pe_ttm")),
                    "pb": _num(item.get("pb_mrq")),
                }
        return result

    # ------------------------------------------------------------------ #
    # 名称
    # ------------------------------------------------------------------ #
    async def get_stock_name(self, code: str) -> str | None:
        """单只名称（ETF 走标的检索，A 股走估值快照的 name）。"""
        ths_code = to_ths_code(code)
        if is_fund_ticker(code):
            data = await self._request_or_fail(
                "/api/meta/tickers/search", {"q": ths_code, "limit": 1}
            )
            for item in (data or {}).get("item") or []:
                if from_ths_code(item.get("thscode") or "") == code:
                    return item.get("name")
            return None
        data = await self._request_or_fail(
            "/api/a-share/valuations/snapshot", {"thscodes": ths_code}
        )
        for item in (data or {}).get("item") or []:
            if from_ths_code(item.get("thscode") or "") == code:
                return item.get("name")
        return None

    # ------------------------------------------------------------------ #
    # 单股完整指标（行情 + 估值 + 财务）
    # ------------------------------------------------------------------ #
    async def get_single_financial_indicators(self, code: str) -> dict[str, Any]:
        """返回单股标准指标字典（含 source 标注）。ETF 无财务指标，仅行情。"""
        quotes = await self.get_quotes([code])
        q = quotes.get(code) or {}

        result: dict[str, Any] = {
            "code": code,
            "name": q.get("name"),
            "price": q.get("price"),
            "changePct": q.get("changePct"),
            "pe_ttm": q.get("pe"),
            "pb": q.get("pb"),
            "volume": q.get("volume"),
            "turnover": q.get("turnover"),
            "marketCapYi": None,
            "roe": None,
            "eps_current": None,
            "revenue": None,
            "revenue_yoy": None,
            "net_profit": None,
            "net_profit_yoy": None,
            "gross_margin": None,
            "industry": None,
            "report_period": None,
        }

        if is_fund_ticker(code):
            result["source"] = "ths"
            return result

        # 财务指标（ROE / 营收同比 / 净利同比 / 毛利率），按报告期从新到旧探测
        ths_code = to_ths_code(code)
        for label in latest_report_labels():
            data = await self._request(
                "/api/a-share/financials/indicators",
                {"thscode": ths_code, "report": label},
            )
            code_, payload, _ = data
            if code_ in FALLBACK_CODES or code_ == 429:
                raise ThsUnavailableError(f"同花顺 code={code_}")
            indicators = self._flatten_indicators(payload)
            if indicators.get("roe") is not None or indicators.get("gross_margin") is not None:
                result.update(indicators)
                result["report_period"] = _report_label_to_q(label)
                break

        # EPS（利润表最新年报）
        eps = await self._latest_eps(ths_code)
        if eps is not None:
            result["eps_current"] = eps

        result["source"] = "ths"
        return result

    def _flatten_indicators(self, data: dict | None) -> dict[str, Any]:
        """把财务指标接口的五类能力块展平为 {roe, revenue_yoy, net_profit_yoy, gross_margin}。

        用包含匹配（而非精确 index_id），兼容文档与实际返回的字段名差异：
        实际返回 ``calculate_operating_income_yoy_growth_ratio``、
        ``calculate_parent_holder_net_profit_yoy_growth_ratio`` 等带前缀变体。
        """
        mapping = [
            ("index_weighted_avg_roe", "roe"),
            ("operating_income_yoy", "revenue_yoy"),
            ("net_profit_yoy", "net_profit_yoy"),
            ("sale_gross_margin", "gross_margin"),
        ]
        flat: dict[str, Any] = {}
        for ability in (data or {}).get("abilities") or []:
            for indicator in ability.get("indicators") or []:
                index_id = indicator.get("index_id") or ""
                for needle, field in mapping:
                    if needle in index_id:
                        flat[field] = _num(indicator.get("value"))
                        break
        return flat

    async def _latest_eps(self, ths_code: str) -> float | None:
        # 用 quarterly 拿最新报告期累计 EPS，与 report_period 口径一致
        data = await self._request_or_fail(
            "/api/a-share/financials/income-statements",
            {"thscode": ths_code, "period": "quarterly", "limit": 1},
        )
        for item in (data or {}).get("item") or []:
            eps = _num(item.get("basic_eps"))
            if eps is not None:
                return eps
        return None

    # ------------------------------------------------------------------ #
    # 三表（同花顺简化字段）
    # ------------------------------------------------------------------ #
    async def get_financial_statements(
        self, code: str, mode: str = "annual", limit: int = 5
    ) -> list[dict[str, Any]]:
        """同花顺三表多期序列，映射为标准字段字典（字段为简化版，缺项由路由器补全）。"""
        ths_code = to_ths_code(code)

        annual_income, annual_balance, annual_cash, quarterly_income = await asyncio.gather(
            self._fetch_ths_table("/api/a-share/financials/income-statements", ths_code, "annual", limit),
            self._fetch_ths_table("/api/a-share/financials/balance-sheets", ths_code, "annual", limit),
            self._fetch_ths_table("/api/a-share/financials/cash-flow-statements", ths_code, "annual", limit),
            self._fetch_ths_table("/api/a-share/financials/income-statements", ths_code, "quarterly", 2),
        )

        merged: dict[str, dict[str, Any]] = {}
        for row in annual_income:
            rd = _ths_report_date(row)
            if rd:
                merged.setdefault(rd, {"code": code, "report_date": rd, "source": "ths"})
                _map_ths(merged[rd], row, THS_INCOME_MAP)
                merged[rd]["report_label"] = _ths_label(row)
        for row in annual_balance:
            rd = _ths_report_date(row)
            if rd and rd in merged:
                _map_ths(merged[rd], row, THS_BALANCE_MAP)
        for row in annual_cash:
            rd = _ths_report_date(row)
            if rd and rd in merged:
                _map_ths(merged[rd], row, THS_CASHFLOW_MAP)
        # 季报/中报拐点（仅利润表补营业收入等关键项）
        for row in quarterly_income:
            rd = _ths_report_date(row)
            if rd and rd not in merged:
                merged.setdefault(rd, {"code": code, "report_date": rd, "source": "ths"})
                _map_ths(merged[rd], row, THS_INCOME_MAP)
                merged[rd]["report_label"] = _ths_label(row)

        items = sorted(merged.values(), key=lambda x: x["report_date"], reverse=True)
        for item in items:
            item["report_type"] = "annual" if item["report_date"].month == 12 else "periodic"
        return items

    async def _fetch_ths_table(self, path: str, ths_code: str, period: str, limit: int) -> list[dict[str, Any]]:
        data = await self._request_or_fail(
            path, {"thscode": ths_code, "period": period, "limit": limit}
        )
        return (data or {}).get("item") or []


def _ths_report_date(row: dict[str, Any]) -> Any:
    from datetime import date, timezone, timedelta

    ms = row.get("period_end_ms")
    if not ms:
        return None
    try:
        return datetime.fromtimestamp(int(ms) / 1000, tz=timezone(timedelta(hours=8))).date()
    except (TypeError, ValueError, OSError):
        return None


def _ths_label(row: dict[str, Any]) -> str:
    year = row.get("fiscal_year") or ""
    period = row.get("fiscal_period") or ""
    qmap = {"FY": "年报", "Q1": "一季报", "Q2": "中报", "Q3": "三季报", "Q4": "年报"}
    return f"{year}{qmap.get(period, period)}"


def _map_ths(target: dict[str, Any], row: dict[str, Any], mapping: dict[str, str]) -> None:
    for field, ths_key in mapping.items():
        value = _num(row.get(ths_key))
        if value is not None:
            target[field] = value


THS_INCOME_MAP = {
    "total_revenue": "operating_income",
    "revenue": "operating_income",
    "operating_cost": "operating_costs",
    "selling_expense": "sales_fee",
    "admin_expense": "manage_fee",
    "rd_expense": "research_and_development_expenses",
    "interest_expense": "interest_expenses",
    "operating_profit": "operating_profit",
    "total_profit": "profit_total",
    "net_profit": "net_profit",
    "parent_net_profit": "parent_holder_net_profit",
}

THS_BALANCE_MAP = {
    "monetary_funds": "cash",
    "accounts_receivable": "accounts_receivable",
    "total_current_assets": "total_current_assets",
    "total_assets": "assets_total",
    "total_liabilities": "total_debt",
    "total_equity": "holder_equity_total",
}

THS_CASHFLOW_MAP = {
    "net_operating_cash_flow": "act_cash_flow_net",
    "net_investing_cash_flow": "invest_cash_flow_net",
    "net_financing_cash_flow": "financing_cash_flow_net",
    "capex_cash_outflow": "pay_fixed_assets_etc_cash",
    "dividend_interest_cash_outflow": "pay_dividends_profits_interest_cash",
    "net_cash_increase": "cash_equivalents_net_addition",
}


def _chunks(items: list[str], size: int) -> list[list[str]]:
    return [items[i : i + size] for i in range(0, len(items), size)]


def _report_label_to_q(label: str) -> str:
    """'2026-3' -> '2026Q3'。"""
    year, quarter = label.split("-")
    return f"{year}Q{quarter}"


# 进程内单例
ths_client = ThsClient()
