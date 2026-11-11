"""免费备用通道适配器：封装东财 push2 / 腾讯 qt.gtimg / 东财 datacenter 财务。

优化点：单股财务指标优先用东财 datacenter 单股财务接口（``RPT_F10_FINANCE_MAINFINADATA``，
一次 HTTP 返回 EPS/ROE/营收/净利/同比/毛利率），替代原先「为 1 只股票拉取
``ak.stock_yjbb_em`` 全市场 5500 行」的慢逻辑。

返回结构与 ``ths_client`` 完全对齐，保证上层路由器可无缝切换。
"""

from __future__ import annotations

import asyncio
import logging
import re
from datetime import date
from typing import Any

import httpx

from app.services.quotes import quote_service

logger = logging.getLogger(__name__)

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)
BASE_HEADERS = {
    "User-Agent": UA,
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "zh-CN,zh;q=0.9",
}
TIMEOUT = httpx.Timeout(15.0, connect=6.0)

# 东财 datacenter 单股财务接口
FINANCE_URL = "https://datacenter-web.eastmoney.com/api/data/v1/get"
FINANCE_COLUMNS = (
    "SECURITY_CODE,REPORT_DATE,REPORT_DATE_NAME,EPSJB,ROEJQ,"
    "TOTALOPERATEREVE,TOTALOPERATEREVETZ,PARENTNETPROFIT,PARENTNETPROFITTZ,XSMLL"
)


def _num(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result


def _report_name_to_q(name: str) -> str:
    """'2026三季报' -> '2026Q3'。"""
    match = re.match(r"(\d{4})(一季报|中报|三季报|年报)", str(name or ""))
    if not match:
        return str(name or "")
    quarter = {"一季报": "Q1", "中报": "Q2", "三季报": "Q3", "年报": "Q4"}[match.group(2)]
    return f"{match.group(1)}{quarter}"


def _report_label_of(report_date: str) -> str:
    """'2025-12-31' -> '2025年报'; '2025-06-30' -> '2025中报'; '2025-09-30' -> '2025三季报'。"""
    value = str(report_date or "")[:10]
    try:
        year, month, _ = value.split("-")
    except ValueError:
        return str(report_date or "")
    labels = {"03": "一季报", "06": "中报", "09": "三季报", "12": "年报"}
    return f"{year}{labels.get(month, '')}"


def _date_of(value: str) -> date | None:
    """'2026-06-15 00:00:00' -> date(2026, 6, 15)。"""
    try:
        return date.fromisoformat(str(value or "")[:10])
    except ValueError:
        return None


class FreeClient:
    """免费通道客户端，方法契约与 ths_client 对齐。"""

    # ------------------------------------------------------------------ #
    # 行情（腾讯为主，含名称/PE/PB/市值）
    # ------------------------------------------------------------------ #
    async def get_quotes(self, codes: list[str]) -> dict[str, dict[str, Any]]:
        if not codes:
            return {}
        tencent = await quote_service.fetch_tencent(codes)
        result: dict[str, dict[str, Any]] = {}
        for code in codes:
            row = tencent.get(code) or {}
            result[code] = {
                "name": row.get("name"),
                "price": row.get("price"),
                "changePct": row.get("changePct"),
                "volume": None,
                "turnover": row.get("turnover"),
                "pe": row.get("peTtm"),
                "pb": row.get("pb"),
                "marketCapYi": row.get("marketCapYi"),
            }
        return result

    async def get_stock_name(self, code: str) -> str | None:
        data = await quote_service.fetch_tencent([code])
        return (data.get(code) or {}).get("name")

    # ------------------------------------------------------------------ #
    # 单股完整指标（行情 + 财务，东财单股接口，避免拉全市场）
    # ------------------------------------------------------------------ #
    async def get_single_financial_indicators(self, code: str) -> dict[str, Any]:
        quotes = await self.get_quotes([code])
        q = quotes.get(code) or {}

        result: dict[str, Any] = {
            "code": code,
            "name": q.get("name"),
            "price": q.get("price"),
            "changePct": q.get("changePct"),
            "pe_ttm": q.get("pe"),
            "pb": q.get("pb"),
            "volume": None,
            "turnover": q.get("turnover"),
            "marketCapYi": q.get("marketCapYi"),
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

        finance = await self._single_finance(code)
        result.update(finance)
        result["source"] = "free_fallback"
        return result

    async def _single_finance(self, code: str) -> dict[str, Any]:
        """东财 datacenter 单股财务（最新一期），失败返回空字典。"""
        params = {
            "reportName": "RPT_F10_FINANCE_MAINFINADATA",
            "columns": FINANCE_COLUMNS,
            "filter": f'(SECURITY_CODE="{code}")',
            "sortColumns": "REPORT_DATE",
            "sortTypes": "-1",
            "pageNumber": "1",
            "pageSize": "1",
            "source": "HSF10",
            "client": "PC",
        }
        try:
            async with httpx.AsyncClient(timeout=TIMEOUT, headers=BASE_HEADERS) as client:
                resp = await client.get(
                    FINANCE_URL, params=params, headers={"Referer": "https://data.eastmoney.com/"}
                )
                resp.raise_for_status()
                payload = resp.json()
        except (httpx.HTTPError, ValueError) as exc:
            logger.warning("东财单股财务请求失败 %s: %s", code, exc)
            return {}

        rows = ((payload or {}).get("result") or {}).get("data") or []
        if not rows:
            return {}
        row = rows[0]
        return {
            "roe": _num(row.get("ROEJQ")),
            "eps_current": _num(row.get("EPSJB")),
            "revenue": _num(row.get("TOTALOPERATEREVE")),
            "revenue_yoy": _num(row.get("TOTALOPERATEREVETZ")),
            "net_profit": _num(row.get("PARENTNETPROFIT")),
            "net_profit_yoy": _num(row.get("PARENTNETPROFITTZ")),
            "gross_margin": _num(row.get("XSMLL")),
            "report_period": _report_name_to_q(row.get("REPORT_DATE_NAME")),
        }

    # ------------------------------------------------------------------ #
    # 分红配送（东财，作为除权试算备用通道）
    # ------------------------------------------------------------------ #
    async def get_dividend_preview(self, code: str) -> dict[str, Any] | None:
        """东财分红送配最新一期，返回 {report_label, ex_date, cash_div_per_share, bonus_share_ratio, progress}。

        报告期字段（REPORT_DATE）是同花顺除复权接口没有的，故东财通道信息更完整。
        """
        params = {
            "reportName": "RPT_SHAREBONUS_DET",
            "columns": "ALL",
            "filter": f'(SECURITY_CODE="{code}")',
            "sortColumns": "EX_DIVIDEND_DATE",
            "sortTypes": "-1",
            "pageNumber": "1",
            "pageSize": "5",
            "source": "HSF10",
            "client": "PC",
        }
        try:
            async with httpx.AsyncClient(timeout=TIMEOUT, headers=BASE_HEADERS) as client:
                resp = await client.get(
                    FINANCE_URL, params=params, headers={"Referer": "https://data.eastmoney.com/"}
                )
                resp.raise_for_status()
                payload = resp.json()
        except (httpx.HTTPError, ValueError) as exc:
            logger.warning("东财分红配送请求失败 %s: %s", code, exc)
            return None

        rows = ((payload or {}).get("result") or {}).get("data") or []
        if not rows:
            return None
        row = rows[0]
        # 每 10 股派息/送转 -> 每股
        pretax_10 = _num(row.get("PRETAX_BONUS_RMB")) or 0.0
        it_ratio = _num(row.get("IT_RATIO") or row.get("BONUS_IT_RATIO")) or 0.0
        return {
            "report_label": _report_label_of(row.get("REPORT_DATE")),
            "ex_date": _date_of(row.get("EX_DIVIDEND_DATE")),
            "cash_div_per_share": round(pretax_10 / 10, 4),
            "bonus_share_ratio": round(it_ratio / 10, 4),
            "progress": str(row.get("ASSIGN_PROGRESS") or ""),
        }

    # ------------------------------------------------------------------ #
    # 三表（东财 F10 完整字段）
    # ------------------------------------------------------------------ #
    async def get_financial_statements(
        self, code: str, mode: str = "annual", limit: int = 5
    ) -> list[dict[str, Any]]:
        """东财 F10 三表多期序列，映射为标准字段字典（字段完整，覆盖张氏分析）。"""
        income, balance, cashflow = await asyncio.gather(
            self._fetch_em_table("RPT_F10_FINANCE_GINCOME", code),
            self._fetch_em_table("RPT_F10_FINANCE_GBALANCE", code),
            self._fetch_em_table("RPT_F10_FINANCE_GCASHFLOW", code),
        )

        # 按 report_date 对齐三表
        merged: dict[str, dict[str, Any]] = {}
        for row in income:
            rd = _report_date_of(row)
            if rd:
                merged.setdefault(rd, {"code": code, "report_date": rd, "source": "free"})
                _map_em(merged[rd], row, EM_INCOME_MAP)
                merged[rd]["report_label"] = str(row.get("REPORT_DATE_NAME") or "")
        for row in balance:
            rd = _report_date_of(row)
            if rd and rd in merged:
                _map_em(merged[rd], row, EM_BALANCE_MAP)
        for row in cashflow:
            rd = _report_date_of(row)
            if rd and rd in merged:
                _map_em(merged[rd], row, EM_CASHFLOW_MAP)

        items = sorted(merged.values(), key=lambda x: x["report_date"], reverse=True)
        for item in items:
            item["report_type"] = "annual" if item["report_date"].month == 12 else "periodic"

        if mode == "annual":
            annual = [x for x in items if x["report_type"] == "annual"][:limit]
            periodic = [x for x in items if x["report_type"] == "periodic"][:2]
            return sorted(annual + periodic, key=lambda x: x["report_date"], reverse=True)
        return items[:limit]

    async def _fetch_em_table(self, report_name: str, code: str) -> list[dict[str, Any]]:
        params = {
            "reportName": report_name,
            "columns": "ALL",
            "filter": f'(SECURITY_CODE="{code}")',
            "sortColumns": "REPORT_DATE",
            "sortTypes": "-1",
            "pageNumber": "1",
            "pageSize": "20",
            "source": "HSF10",
            "client": "PC",
        }
        try:
            async with httpx.AsyncClient(timeout=TIMEOUT, headers=BASE_HEADERS) as client:
                resp = await client.get(
                    FINANCE_URL, params=params, headers={"Referer": "https://data.eastmoney.com/"}
                )
                resp.raise_for_status()
                payload = resp.json()
        except (httpx.HTTPError, ValueError) as exc:
            logger.warning("东财三表请求失败 %s %s: %s", code, report_name, exc)
            return []
        return ((payload or {}).get("result") or {}).get("data") or []


def _report_date_of(row: dict[str, Any]) -> date | None:
    value = str(row.get("REPORT_DATE") or "")
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        return None


def _map_em(target: dict[str, Any], row: dict[str, Any], mapping: dict[str, str]) -> None:
    for field, em_key in mapping.items():
        value = _num(row.get(em_key))
        if value is not None:
            target[field] = value


# 东财 F10 三表字段 -> 标准字段名
EM_INCOME_MAP = {
    "total_revenue": "TOTAL_OPERATE_INCOME",
    "revenue": "OPERATE_INCOME",
    "operating_cost": "OPERATE_COST",
    "tax_and_surcharge": "OPERATE_TAX_ADD",
    "selling_expense": "SALE_EXPENSE",
    "admin_expense": "MANAGE_EXPENSE",
    "rd_expense": "RESEARCH_EXPENSE",
    "financial_expense": "FINANCE_EXPENSE",
    "interest_expense": "INTEREST_EXPENSE",
    "interest_income": "INTEREST_INCOME",
    "other_income": "OTHER_INCOME",
    "investment_income": "INVEST_INCOME",
    "fair_value_change_income": "FAIRVALUE_CHANGE_INCOME",
    "asset_disposal_income": "ASSET_DISPOSAL_INCOME",
    "asset_impairment_loss": "ASSET_IMPAIRMENT_LOSS",
    "credit_impairment_loss": "CREDIT_IMPAIRMENT_LOSS",
    "operating_profit": "OPERATE_PROFIT",
    "total_profit": "TOTAL_PROFIT",
    "net_profit": "NETPROFIT",
    "parent_net_profit": "PARENT_NETPROFIT",
    "deduct_parent_net_profit": "DEDUCT_PARENT_NETPROFIT",
}

EM_BALANCE_MAP = {
    "monetary_funds": "MONETARYFUNDS",
    "trading_financial_assets": "TRADE_FINASSET",
    "other_current_assets": "OTHER_CURRENT_ASSET",
    "notes_and_accounts_receivable": "NOTE_ACCOUNTS_RECE",
    "accounts_receivable": "ACCOUNTS_RECE",
    "notes_receivable": "NOTE_RECE",
    "prepayments": "PREPAYMENT",
    "other_receivables": "OTHER_RECE",
    "inventories": "INVENTORY",
    "fixed_assets": "FIXED_ASSET",
    "construction_in_progress": "CIP",
    "intangible_assets": "INTANGIBLE_ASSET",
    "dev_expenditure": "DEVELOP_EXPENSE",
    "long_term_equity_investment": "LONG_EQUITY_INVEST",
    "goodwill": "GOODWILL",
    "total_current_assets": "TOTAL_CURRENT_ASSETS",
    "total_assets": "TOTAL_ASSETS",
    "short_term_loans": "SHORT_LOAN",
    "notes_and_accounts_payable": "NOTE_ACCOUNTS_PAYABLE",
    "contract_liabilities": "CONTRACT_LIAB",
    "advance_receipts": "ADVANCE_RECEIVABLES",
    "employee_benefits_payable": "STAFF_SALARY_PAYABLE",
    "taxes_payable": "TAX_PAYABLE",
    "other_payables": "OTHER_PAYABLE",
    "interest_payable": "INTEREST_PAYABLE",
    "non_current_liabilities_due_1y": "NONCURRENT_LIAB_1YEAR",
    "long_term_loans": "LONG_LOAN",
    "bonds_payable": "BOND_PAYABLE",
    "lease_liabilities": "LEASE_LIAB",
    "total_current_liabilities": "TOTAL_CURRENT_LIAB",
    "total_liabilities": "TOTAL_LIABILITIES",
    "paid_in_capital": "SHARE_CAPITAL",
    "capital_reserve": "CAPITAL_RESERVE",
    "surplus_reserve": "SURPLUS_RESERVE",
    "undistributed_profit": "UNASSIGN_RPOFIT",
    "minority_equity": "MINORITY_EQUITY",
    "parent_equity": "TOTAL_PARENT_EQUITY",
    "total_equity": "TOTAL_EQUITY",
}

EM_CASHFLOW_MAP = {
    "operating_cash_inflow": "TOTAL_OPERATE_INFLOW",
    "operating_cash_outflow": "TOTAL_OPERATE_OUTFLOW",
    "net_operating_cash_flow": "NETCASH_OPERATE",
    "capex_cash_outflow": "CONSTRUCT_LONG_ASSET",
    "net_investing_cash_flow": "NETCASH_INVEST",
    "borrowings_cash_inflow": "BORROW_FUND_ADD",
    "debt_repayment_cash_outflow": "PAY_DEBT_CASH",
    "dividend_interest_cash_outflow": "ASSIGN_DIVIDEND_PORFIT",
    "net_financing_cash_flow": "NETCASH_FINANCE",
    "net_cash_increase": "CCE_ADD",
}


free_client = FreeClient()
