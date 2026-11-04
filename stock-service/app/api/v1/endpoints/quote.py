"""实时行情 / ROE / 股票名称代理接口。

原实现散落在 vite.config.ts 的 Node 中间件里，这里用 httpx 异步重写：
    GET /api/quote?secids=1.600519,0.000725&symbol=600519
    GET /api/roe?codes=SH600519,SZ000725
    GET /api/stock-name?codes=600519,000725

错误响应统一为 {"message": "...", ...}，与前端 `json?.message` 取值方式保持一致。
"""

from __future__ import annotations

import re
import time

from fastapi import APIRouter, Query, Response, status

from app.schemas.quote import QuoteResponse, RoeResponse, StockNameResponse
from app.services.quotes import PREFIXED_CODE_RE, SECID_RE, quote_service, secid_of

router = APIRouter(tags=["quote"])

CODE_RE = re.compile(r"^\d{6}$")


def _split(raw: str | None) -> list[str]:
    return [item.strip() for item in str(raw or "").split(",") if item.strip()]


def _parse_symbol_to_secid(raw: str) -> str | None:
    value = raw.strip().upper()
    match = re.search(r"(\d{6})", value)
    if not match:
        return None
    code = match.group(1)
    market = "1" if ("SH" in value or code[:1] in {"6", "5", "9"}) else "0"
    return f"{market}.{code}"


@router.get("/quote", response_model=QuoteResponse, summary="批量实时行情（东方财富 push2）")
async def get_quote(
    response: Response,
    secids: str | None = Query(None, description="1.600519,0.000725"),
    symbol: str | None = Query(None, description="600519,SH600519"),
) -> QuoteResponse:
    collected: list[str] = []
    for item in _split(secids):
        if not SECID_RE.match(item):
            response.status_code = status.HTTP_400_BAD_REQUEST
            return QuoteResponse(message=f"secids 参数格式错误：{item}，应为 1.600519")
        collected.append(item)
    for item in _split(symbol):
        secid = _parse_symbol_to_secid(item)
        if not secid:
            response.status_code = status.HTTP_400_BAD_REQUEST
            return QuoteResponse(message=f"symbol 参数格式错误：{item}，应为 600519 或 SH600519")
        collected.append(secid)

    unique = list(dict.fromkeys(collected))
    if not unique:
        response.status_code = status.HTTP_400_BAD_REQUEST
        return QuoteResponse(message="请提供 secids 或 symbol 参数")

    diff, stale, error = await quote_service.resolve_quotes(unique)
    if not diff:
        response.status_code = status.HTTP_502_BAD_GATEWAY
        return QuoteResponse(diff=[], fetched_at=int(time.time() * 1000), message=error or "未能获取到行情数据")

    return QuoteResponse(
        diff=diff,
        fetched_at=int(time.time() * 1000),
        stale=stale,
        message=f"行情接口暂不可用，已使用缓存数据（{error}）" if stale and error else "",
    )


@router.get("/roe", response_model=RoeResponse, summary="批量加权 ROE（东方财富）")
async def get_roe(
    response: Response,
    codes: str | None = Query(None, description="SH600519,SZ000725"),
    code: str | None = Query(None, description="codes 的单值写法"),
) -> RoeResponse:
    raw = codes or code
    items = [item.upper() for item in _split(raw)]
    if not items:
        response.status_code = status.HTTP_400_BAD_REQUEST
        return RoeResponse(message="codes 参数不能为空")
    invalid = [item for item in items if not PREFIXED_CODE_RE.match(item)]
    if invalid:
        response.status_code = status.HTTP_400_BAD_REQUEST
        return RoeResponse(message=f"codes 参数格式错误：{','.join(invalid)}，应为 SH600519,SZ000725")

    unique = list(dict.fromkeys(items))
    data, error = await quote_service.resolve_roe(unique)
    return RoeResponse(items=data, fetched_at=int(time.time() * 1000), message=error if error else "")


@router.get("/stock-name", response_model=StockNameResponse, summary="腾讯行情快照（名称/价格/PE/PB）")
async def get_stock_name(
    response: Response,
    codes: str | None = Query(None, description="6 位代码，逗号分隔，最多 50 个"),
) -> StockNameResponse:
    items = [item for item in _split(codes) if CODE_RE.match(item)]
    if not items:
        response.status_code = status.HTTP_400_BAD_REQUEST
        return StockNameResponse(items={})
    unique = list(dict.fromkeys(items))[:50]
    data = await quote_service.fetch_tencent(unique)
    return StockNameResponse(items={code: data[code] for code in data})


__all__ = ["router", "secid_of"]
