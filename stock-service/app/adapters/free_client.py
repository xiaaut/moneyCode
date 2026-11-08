"""免费备用通道适配器：封装东财 push2 / 腾讯 qt.gtimg / 东财 datacenter 财务。

优化点：单股财务指标优先用东财 datacenter 单股财务接口（``RPT_F10_FINANCE_MAINFINADATA``，
一次 HTTP 返回 EPS/ROE/营收/净利/同比/毛利率），替代原先「为 1 只股票拉取
``ak.stock_yjbb_em`` 全市场 5500 行」的慢逻辑。

返回结构与 ``ths_client`` 完全对齐，保证上层路由器可无缝切换。
"""

from __future__ import annotations

import logging
import re
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


free_client = FreeClient()
