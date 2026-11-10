"""主备智能路由器：同花顺主通道优先 + 异常自动降级免费备用通道。

- ``DATA_SOURCE_MODE=ths_first``（默认）：主通道优先；未配置 THS_API_KEY、认证失败(2001)、
  权限不足(2003)、标的不支持(3001/3004)、限流(4001/429)、上游不可用(5001/5002/5003)
  时自动降级免费通道。
- ``DATA_SOURCE_MODE=free_only``：强制走免费通道。

每个返回项标注 ``source``：``ths`` 或 ``free_fallback``。
"""

from __future__ import annotations

import logging
from typing import Any

from app.adapters.free_client import free_client
from app.adapters.ths_client import ThsUnavailableError, ths_client
from app.core.config import settings

logger = logging.getLogger(__name__)


def _mode() -> str:
    return (settings.DATA_SOURCE_MODE or "ths_first").strip().lower()


def _tag_items(items: dict[str, dict[str, Any]], source: str) -> dict[str, dict[str, Any]]:
    for item in items.values():
        item["source"] = source
    return items


async def get_quotes(codes: list[str]) -> dict[str, dict[str, Any]]:
    """批量行情，返回 {code: {name, price, changePct, pe, pb, volume, turnover, source}}。"""
    if _mode() == "free_only" or not settings.THS_API_KEY:
        return _tag_items(await free_client.get_quotes(codes), "free_fallback")

    try:
        items = await ths_client.get_quotes(codes)
        return _tag_items(items, "ths")
    except ThsUnavailableError as exc:
        logger.warning("同花顺行情降级到免费通道：%s", exc)
        return _tag_items(await free_client.get_quotes(codes), "free_fallback")
    except Exception as exc:  # noqa: BLE001
        logger.warning("同花顺行情异常，降级到免费通道：%s", exc)
        return _tag_items(await free_client.get_quotes(codes), "free_fallback")


async def get_stock_name(code: str) -> str | None:
    """单只名称。降级时记录日志。"""
    if _mode() == "free_only" or not settings.THS_API_KEY:
        return await free_client.get_stock_name(code)

    try:
        return await ths_client.get_stock_name(code)
    except ThsUnavailableError as exc:
        logger.warning("同花顺名称降级到免费通道 %s：%s", code, exc)
        return await free_client.get_stock_name(code)
    except Exception as exc:  # noqa: BLE001
        logger.warning("同花顺名称异常，降级到免费通道 %s：%s", code, exc)
        return await free_client.get_stock_name(code)


async def get_single_financial_indicators(code: str) -> dict[str, Any]:
    """单股完整指标（行情 + 估值 + 财务），返回含 source 的字典。"""
    if _mode() == "free_only" or not settings.THS_API_KEY:
        return await free_client.get_single_financial_indicators(code)

    try:
        return await ths_client.get_single_financial_indicators(code)
    except ThsUnavailableError as exc:
        logger.warning("同花顺单股指标降级到免费通道 %s：%s", code, exc)
        return await free_client.get_single_financial_indicators(code)
    except Exception as exc:  # noqa: BLE001
        logger.warning("同花顺单股指标异常，降级到免费通道 %s：%s", code, exc)
        return await free_client.get_single_financial_indicators(code)


# 张氏分析强依赖的资产负债表现金流关键字段（同花顺三表接口不返回这些）
_CRITICAL_FINANCIAL_FIELDS = {
    "goodwill", "inventories", "contract_liabilities", "short_term_loans",
    "long_term_loans", "fixed_assets", "construction_in_progress", "notes_and_accounts_payable",
    "trading_financial_assets", "other_current_assets", "deduct_parent_net_profit",
    "other_income", "investment_income", "asset_impairment_loss", "credit_impairment_loss",
}


async def get_financial_statements(code: str, mode: str = "annual", limit: int = 5) -> list[dict[str, Any]]:
    """三表多期序列。主通道同花顺优先，字段不足时自动用东财 F10 补全。"""
    if _mode() == "free_only" or not settings.THS_API_KEY:
        return await free_client.get_financial_statements(code, mode, limit)

    try:
        items = await ths_client.get_financial_statements(code, mode, limit)
    except ThsUnavailableError as exc:
        logger.warning("同花顺三表降级到免费通道 %s：%s", code, exc)
        return await free_client.get_financial_statements(code, mode, limit)
    except Exception as exc:  # noqa: BLE001
        logger.warning("同花顺三表异常，降级到免费通道 %s：%s", code, exc)
        return await free_client.get_financial_statements(code, mode, limit)

    # 字段覆盖检查：同花顺三表为简化版，缺张氏关键字段时用东财补全
    if _missing_critical_fields(items):
        logger.info("同花顺三表字段不足，改用东财 F10 补全 %s", code)
        return await free_client.get_financial_statements(code, mode, limit)
    return items


def _missing_critical_fields(items: list[dict[str, Any]]) -> bool:
    if not items:
        return False
    latest = items[0]
    present = sum(1 for field in _CRITICAL_FINANCIAL_FIELDS if latest.get(field) is not None)
    return present < len(_CRITICAL_FINANCIAL_FIELDS) * 0.5
