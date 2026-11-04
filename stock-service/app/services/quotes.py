"""实时行情抓取服务。

把原 `vite.config.ts` 里用 Node https 写的那套逻辑迁到 httpx：
    * 东方财富 push2  -> 批量行情（现价/涨跌幅/PB/PE(TTM)/行业）
    * 腾讯 qt.gtimg.cn -> GBK 编码的名称/行情兜底
    * 东方财富 datacenter -> 加权 ROE

缓存策略保留原设计，但放在进程内存（单实例部署，等价于原来的 JSON 缓存文件）：
    行情 30 秒，ROE 7 天，控制人 30 天。
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from typing import Any

import httpx

logger = logging.getLogger(__name__)

# ---------------- 常量 ----------------
QUOTE_URL = "https://push2.eastmoney.com/api/qt/ulist.np/get"
QUOTE_FIELDS = "f2,f3,f4,f5,f6,f8,f9,f10,f12,f13,f14,f15,f16,f17,f18,f20,f21,f23,f100,f115"
TENCENT_URL = "https://qt.gtimg.cn/q="
ROE_URL = "https://datacenter-web.eastmoney.com/api/data/v1/get"

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)
BASE_HEADERS = {
    "User-Agent": UA,
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "zh-CN,zh;q=0.9",
}

QUOTE_TTL = 30.0
ROE_TTL = 7 * 24 * 3600.0
TIMEOUT = httpx.Timeout(12.0, connect=5.0)

SECID_RE = re.compile(r"^[01]\.\d{6}$")
PREFIXED_CODE_RE = re.compile(r"^(SH|SZ)\d{6}$")


def secid_of(code: str) -> str:
    """6 位代码 -> 东财 secid（1=沪市，0=深市/北交所）。"""
    pure = "".join(ch for ch in str(code) if ch.isdigit())[-6:]
    market = "1" if pure[:1] in {"6", "5", "9"} else "0"
    return f"{market}.{pure}"


def tencent_symbol(code: str) -> str:
    """6 位代码 -> 腾讯前缀代码（sh/sz/bj）。"""
    pure = "".join(ch for ch in str(code) if ch.isdigit())[-6:]
    if pure[:2] in {"43", "83", "87", "92"}:
        return f"bj{pure}"
    if pure[:1] in {"6", "5", "9"}:
        return f"sh{pure}"
    return f"sz{pure}"


def _num(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result


def _chunks(items: list[str], size: int) -> list[list[str]]:
    return [items[i : i + size] for i in range(0, len(items), size)]


class QuoteService:
    """带 TTL 内存缓存的行情服务。"""

    def __init__(self) -> None:
        self._quote_cache: dict[str, tuple[float, dict[str, Any]]] = {}
        self._roe_cache: dict[str, tuple[float, float]] = {}
        self._lock = asyncio.Lock()

    # ------------------------------------------------------------------ #
    # 东方财富批量行情
    # ------------------------------------------------------------------ #
    async def resolve_quotes(
        self, secids: list[str], *, force: bool = False
    ) -> tuple[list[dict[str, Any]], bool, str]:
        """返回 (diff, stale, message)。一个可用数据都没有时 diff 为空。"""
        now = time.time()
        hits: dict[str, dict[str, Any]] = {}
        missing: list[str] = []

        for secid in secids:
            cached = self._quote_cache.get(secid)
            if cached and not force and now - cached[0] < QUOTE_TTL:
                hits[secid] = cached[1]
            else:
                missing.append(secid)

        stale = False
        message = ""

        if missing:
            fetched, error = await self._fetch_quote_chunks(missing)
            if fetched:
                for secid, item in fetched.items():
                    self._quote_cache[secid] = (now, item)
                    hits[secid] = item
            # 拉取失败的用旧缓存兜底
            for secid in missing:
                if secid in hits:
                    continue
                cached = self._quote_cache.get(secid)
                if cached:
                    hits[secid] = cached[1]
                    stale = True
            if error and not fetched:
                message = error

        ordered = [hits[s] for s in secids if s in hits]
        return ordered, stale, message

    async def _fetch_quote_chunks(
        self, secids: list[str]
    ) -> tuple[dict[str, dict[str, Any]], str]:
        result: dict[str, dict[str, Any]] = {}
        error = ""
        async with httpx.AsyncClient(timeout=TIMEOUT, headers=BASE_HEADERS) as client:
            for chunk in _chunks(secids, 100):
                params = {
                    "fltt": "2",
                    "invt": "2",
                    "fields": QUOTE_FIELDS,
                    "secids": ",".join(chunk),
                }
                try:
                    resp = await client.get(
                        QUOTE_URL, params=params, headers={"Referer": "https://quote.eastmoney.com/"}
                    )
                    resp.raise_for_status()
                    payload = resp.json()
                except (httpx.HTTPError, ValueError) as exc:
                    error = f"行情接口请求失败：{type(exc).__name__}"
                    logger.warning("push2 行情请求失败: %s", exc)
                    continue

                rows = ((payload or {}).get("data") or {}).get("diff") or []
                for row in rows:
                    code = str(row.get("f12") or "")
                    market = row.get("f13")
                    secid = f"{market}.{code}" if market is not None else secid_of(code)
                    result[secid] = row
        return result, error

    # ------------------------------------------------------------------ #
    # 腾讯行情（名称 / 快照）
    # ------------------------------------------------------------------ #
    async def fetch_tencent(self, codes: list[str]) -> dict[str, dict[str, Any]]:
        """返回 {6位代码: {name, price, changePct, peTtm, pb, marketCapYi, turnover}}。"""
        result: dict[str, dict[str, Any]] = {}
        if not codes:
            return result

        async with httpx.AsyncClient(timeout=TIMEOUT, headers=BASE_HEADERS) as client:
            for index, chunk in enumerate(_chunks(codes, 60)):
                if index:
                    await asyncio.sleep(0.12)
                symbols = ",".join(tencent_symbol(code) for code in chunk)
                try:
                    resp = await client.get(
                        f"{TENCENT_URL}{symbols}", headers={"Referer": "http://gu.qq.com/"}
                    )
                    resp.raise_for_status()
                    text = resp.content.decode("gbk", errors="replace")
                except (httpx.HTTPError, UnicodeDecodeError) as exc:
                    logger.warning("腾讯行情请求失败: %s", exc)
                    continue

                for match in re.finditer(r'v_(\w+)="([^"]*)"', text):
                    fields = match.group(2).split("~")
                    if len(fields) < 47:
                        continue
                    name = fields[1].strip()
                    code = match.group(1)[2:]
                    if not name:
                        continue
                    price = _num(fields[3])
                    prev_close = _num(fields[4])
                    change_pct = None
                    if price and prev_close:
                        change_pct = round((price - prev_close) / prev_close * 100, 2)
                    pb = _num(fields[46])
                    result[code] = {
                        "name": name,
                        "price": price if price and price > 0 else None,
                        "changePct": change_pct,
                        "turnover": _num(fields[38]),
                        "peTtm": _num(fields[39]),
                        "marketCapYi": _num(fields[45]),
                        "pb": pb if pb and pb > 0 else None,
                    }
        return result

    # ------------------------------------------------------------------ #
    # 东财 ROE（加权）
    # ------------------------------------------------------------------ #
    async def resolve_roe(
        self, codes: list[str], *, force: bool = False
    ) -> tuple[dict[str, dict[str, Any]], str]:
        """codes 为 SH600519 / SZ000725 形式；返回 {带前缀代码: {roe, ts, fromCache}}。"""
        now = time.time()
        items: dict[str, dict[str, Any]] = {}
        missing: list[str] = []

        for code in codes:
            cached = self._roe_cache.get(code)
            if cached and not force and now - cached[0] < ROE_TTL:
                items[code] = {
                    "roe": cached[1],
                    "ts": int(cached[0] * 1000),
                    "fromCache": True,
                }
            else:
                missing.append(code)

        if not missing:
            return items, ""

        fetched, error = await self._fetch_roe_batch(missing)
        for code, value in fetched.items():
            self._roe_cache[code] = (now, value)
            items[code] = {"roe": value, "ts": int(now * 1000), "fromCache": False}
        for code in missing:
            if code not in fetched:
                items.setdefault(code, {"roe": None, "ts": 0, "fromCache": False, "error": error or "未获取到数据"})
        return items, error

    async def _fetch_roe_batch(self, codes: list[str]) -> tuple[dict[str, float], str]:
        result: dict[str, float] = {}
        error = ""
        pure_codes = [code[2:] for code in codes]
        filter_expr = '(SECURITY_CODE in ("' + '","'.join(pure_codes) + '"))'

        async with httpx.AsyncClient(timeout=TIMEOUT, headers=BASE_HEADERS) as client:
            for chunk in _chunks(pure_codes, 80):
                expr = '(SECURITY_CODE in ("' + '","'.join(chunk) + '"))'
                params = {
                    "reportName": "RPT_F10_FINANCE_MAINFINADATA",
                    "columns": "SECURITY_CODE,REPORT_DATE,REPORT_DATE_NAME,ROEJQ",
                    "filter": expr,
                    "sortColumns": "REPORT_DATE",
                    "sortTypes": "-1",
                    "pageNumber": "1",
                    "pageSize": "500",
                    "source": "HSF10",
                    "client": "PC",
                }
                try:
                    resp = await client.get(
                        ROE_URL, params=params, headers={"Referer": "https://data.eastmoney.com/"}
                    )
                    resp.raise_for_status()
                    payload = resp.json()
                except (httpx.HTTPError, ValueError) as exc:
                    error = f"ROE 接口请求失败：{type(exc).__name__}"
                    logger.warning("ROE 请求失败 (chunk=%d): %s", len(filter_expr), exc)
                    continue

                rows = ((payload or {}).get("result") or {}).get("data") or []
                for row in rows:
                    code = str(row.get("SECURITY_CODE") or "")
                    if not code or code in result:
                        continue
                    value = _num(row.get("ROEJQ"))
                    if value is not None:
                        result[code] = value
        return {f"{'SH' if c.startswith(('6', '5', '9')) else 'SZ'}{c}": v for c, v in result.items()}, error


quote_service = QuoteService()
