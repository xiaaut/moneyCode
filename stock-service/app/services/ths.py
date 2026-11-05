"""实际控制人 / 国企属性识别（同花顺 F10 为主、东财 F10 兜底）。

从 vite.config.ts 原样迁入：同花顺 HTML 解析、东财数据中心 ACTUAL_HOLDER 分类、
国资委央企名录匹配、最终控制人穿透。
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from typing import Any

import httpx

logger = logging.getLogger(__name__)

# 同花顺 F10 单源最小间隔（防封）
THS_MIN_INTERVAL = 0.9
_ths_lock = asyncio.Lock()
_ths_last = 0.0


async def acquire_ths_slot() -> None:
    """同花顺请求限流：同一时刻只放行一个，且相邻请求至少间隔 0.9s。"""
    global _ths_last
    async with _ths_lock:
        wait = THS_MIN_INTERVAL - (time.time() - _ths_last)
        if wait > 0:
            await asyncio.sleep(wait)
        _ths_last = time.time()

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

CONTROLLER_TTL_MS = 30 * 24 * 3600 * 1000

CENTRAL_SOE_URL = "http://www.sasac.gov.cn/n2588045/n27271785/n27271792/"

CATEGORY_LABELS: dict[str, str] = {
    "sasac-central": "国务院国资委",
    "sasac-local": "地方国资委",
    "government": "政府 / 财政部门",
    "state-other": "其他国有主体",
    "state-group-pending": "国有法人（待确认）",
    "non-state": "非国资",
    "none": "无实际控制人",
    "not-applicable": "ETF / 基金（不适用）",
    "unknown": "未获取",
}

STATE_CATEGORY_LABELS: dict[str, str] = {
    "sasac-central": "国务院国资委",
    "sasac-local": "地方国资委",
    "government": "政府 / 财政 / 事业单位",
    "state-finance": "国有资本运营主体",
    "state-group": "国有法人（待确认）",
    "collective": "集体所有制",
    "person": "非国资（自然人）",
    "none": "无实际控制人",
    "other": "其他 / 社会组织",
    "unknown": "未获取",
}

# 计入「国资/政府控股」统计的类别
STATE_CATEGORIES = {"sasac-central", "sasac-local", "government", "state-finance"}

A_SHARE_TYPES = {"上交所主板A股", "深交所主板A股", "北京证券交易所A股"}

ORG_INFO_URL = (
    "https://datacenter-web.eastmoney.com/api/data/v1/get"
    "?reportName=RPT_F10_BASIC_ORGINFO"
    "&columns=SECURITY_CODE,SECURITY_NAME_ABBR,SECURITY_TYPE,ACTUAL_HOLDER&pageNumber="
)

_controller_cache: dict[str, tuple[float, dict[str, Any]]] = {}
_central_soe_names: list[str] | None = None
_central_soe_fetched_at = 0.0


# --------------------------------------------------------------------------- #
# 通用
# --------------------------------------------------------------------------- #
async def _get_bytes(client: httpx.AsyncClient, url: str, headers: dict[str, str]) -> bytes:
    resp = await client.get(url, headers=headers)
    resp.raise_for_status()
    return resp.content


async def _get_json(client: httpx.AsyncClient, url: str, headers: dict[str, str]) -> Any:
    resp = await client.get(url, headers=headers)
    resp.raise_for_status()
    return resp.json()


def _gbk(data: bytes) -> str:
    return data.decode("gbk", errors="replace")


# --------------------------------------------------------------------------- #
# 同花顺解析
# --------------------------------------------------------------------------- #
def classify_controller(final_controller: str, actual_controller: str, name: str) -> str:
    text = f"{final_controller or ''} {actual_controller or ''}".strip()
    title = str(name or "")

    if not text:
        return "not-applicable" if re.search(r"ETF|LOF|基金", title) else "unknown"
    if re.search(r"无实际控制人|无最终控制人|无控股股东", text):
        return "none"
    if re.search(r"国务院国有资产监督管理委员会|国务院国资委", text):
        return "sasac-central"
    if re.search(r"国有资产监督管理委员会|国资委", text):
        return "sasac-local"
    if re.search(r"人民政府|财政(厅|局)|管理委员会|管委会|国务院", text):
        return "government"
    if re.search(r"中央汇金|全国社会保障基金|国有资本|国有资产经营|国有投资", text):
        return "state-other"
    if re.fullmatch(r"[\u4e00-\u9fa5]{2,4}", re.sub(r"[、,，\s]", "", text)):
        return "non-state"
    if re.search(r"集团|控股|投资|有限公司|有限责任公司|总公司|经营公司", text):
        return "state-group-pending"
    return "non-state"


def strip_tipbox_blocks(html: str) -> str:
    """删除同花顺页面里的悬浮提示块，避免把「旗下上市公司」表格当字段值。"""
    result = ""
    index = 0
    while index < len(html):
        start = html.find('<div class="tipbox', index)
        if start < 0:
            result += html[index:]
            break
        result += html[index:start]
        depth = 0
        cursor = start
        while cursor < len(html):
            open_pos = html.find("<div", cursor)
            close_pos = html.find("</div>", cursor)
            if close_pos < 0:
                cursor = len(html)
                break
            if 0 <= open_pos < close_pos:
                depth += 1
                cursor = open_pos + 4
            else:
                depth -= 1
                cursor = close_pos + 6
                if depth == 0:
                    break
        index = cursor
    return result


def parse_ths_field(html: str, label: str) -> dict[str, str] | None:
    marker = f"{label}：</strong>"
    marker_index = html.find(marker)
    if marker_index < 0:
        return None

    raw_chunk = html[marker_index + len(marker): marker_index + len(marker) + 40_000]
    without_tips = strip_tipbox_blocks(raw_chunk)
    cell_end = without_tips.find("</td>")
    chunk = without_tips[:cell_end] if cell_end >= 0 else without_tips[:2000]
    cleaned = (
        re.sub(r"<[^>]+>", " ", chunk)
        .replace("&nbsp;", " ")
        .replace("&amp;", "&")
        .replace("\u3000", " ")
    )
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    if not cleaned:
        return None

    ratio_match = re.search(r"比例[：:]\s*([^）)]+)", cleaned)
    name = re.sub(r"\s*[（(]?\s*持有[\s\S]*$", "", cleaned)
    name = re.sub(r"^[\s（(、,，]+|[\s（()）、,，]+$", "", name)
    name = re.sub(r"\s+", " ", name).strip()
    if not name:
        return None
    return {"name": name, "ratio": (ratio_match.group(1).strip() if ratio_match else "")}


async def fetch_ths_controller(code: str) -> dict[str, Any]:
    url = f"https://basic.10jqka.com.cn/{code}/company.html"
    async with httpx.AsyncClient(timeout=TIMEOUT, headers=BASE_HEADERS) as client:
        resp = await client.get(url, headers={"Referer": "https://basic.10jqka.com.cn/"})
        resp.raise_for_status()
        html = _gbk(resp.content)
    if not html or len(html) < 1000:
        raise RuntimeError("同花顺 F10 返回内容异常")

    title_match = re.search(r"<title>([\s\S]*?)</title>", html, re.I)
    raw_title = title_match.group(1) if title_match else ""
    name_match = re.match(r"^([^<(（]+)[(（]\d{6}", raw_title)

    return {
        "name": name_match.group(1).strip() if name_match else "",
        "finalController": parse_ths_field(html, "最终控制人"),
        "actualController": parse_ths_field(html, "实际控制人"),
        "controllingHolder": parse_ths_field(html, "控股股东"),
        "sourceUrl": url,
    }


async def fetch_eastmoney_controller(code: str) -> dict[str, Any]:
    """code 为 SH600519 / SZ000725 形式。"""
    url = f"https://emweb.securities.eastmoney.com/PC_HSF10/ShareholderResearch/PageAjax?code={code}"
    async with httpx.AsyncClient(timeout=TIMEOUT, headers=BASE_HEADERS) as client:
        data = await _get_json(client, url, {"Referer": "https://emweb.securities.eastmoney.com/"})
    lst = data.get("sjkzr") if isinstance(data, dict) else None
    lst = lst if isinstance(lst, list) else []
    first = lst[0] if lst else None
    name = str(first.get("HOLDER_NAME") or "").strip() if first else ""
    ratio = f"{first.get('HOLD_RATIO')}%" if first and first.get("HOLD_RATIO") is not None else ""
    return {
        "name": name,
        "holder": {"name": name, "ratio": ratio} if name else None,
        "sourceUrl": f"https://emweb.securities.eastmoney.com/PC_HSF10/ShareholderResearch/Index?type=web&code={code}",
    }


async def resolve_controller(code: str, force_refresh: bool) -> dict[str, Any]:
    """code 为 SH/SZ/BJ + 6 位代码。返回与旧接口一致的 item（含 fromCache）。"""
    cached = _controller_cache.get(code)
    if not force_refresh and cached and time.time() * 1000 - cached[0] < CONTROLLER_TTL_MS:
        return {**cached[1], "fromCache": True}

    pure_code = re.sub(r"^(SH|SZ|BJ)", "", code)

    ths: dict[str, Any] | None = None
    ths_error = ""
    try:
        await acquire_ths_slot()
        ths = await fetch_ths_controller(pure_code)
    except Exception as exc:  # noqa: BLE001
        ths_error = str(exc) or "同花顺 F10 抓取失败"

    em: dict[str, Any] | None = None
    em_error = ""
    need_fallback = not ths or not ths.get("finalController")
    if need_fallback:
        try:
            em = await fetch_eastmoney_controller(code)
        except Exception as exc:  # noqa: BLE001
            em_error = str(exc) or "东方财富 F10 抓取失败"

    final_name = (ths or {}).get("finalController") or {}
    final_name = final_name.get("name", "") if isinstance(final_name, dict) else ""
    actual_name = (ths or {}).get("actualController") or {}
    actual_name = actual_name.get("name", "") if isinstance(actual_name, dict) else ""
    controlling_name = (ths or {}).get("controllingHolder") or {}
    controlling_name = controlling_name.get("name", "") if isinstance(controlling_name, dict) else ""
    em_name = (em or {}).get("name", "")
    fallback_used = not final_name and not actual_name and bool(em_name)

    category = classify_controller(final_name or em_name, actual_name or em_name, (ths or {}).get("name", ""))
    note = ""
    source = ""
    source_url = ""

    if ths and (final_name or actual_name or controlling_name):
        source = "同花顺 F10 + 东方财富 F10" if fallback_used else "同花顺 F10·公司资料"
        source_url = ths.get("sourceUrl", "")
        if fallback_used:
            note = "同花顺未披露最终控制人，已用东方财富 F10 实际控制人兜底"
    elif em_name:
        source = "东方财富 F10·股东研究"
        source_url = (em or {}).get("sourceUrl", "")
        if category == "state-group-pending":
            note = "东财仅给出国有法人作为实际控制人，建议人工核对最终控制人"

    if not ths and em_name:
        note = note or f"同花顺抓取失败（{ths_error}），已用东方财富数据兜底"
    if not ths and not em_name:
        category = "unknown"
        note = "；".join(x for x in (ths_error, em_error) if x) or "两个数据源均未取到数据"
    if category == "state-group-pending" and not note:
        note = "同花顺未披露最终控制人，实际控制人为国有法人，建议人工核对"
    if em_name and final_name and em_name not in final_name and final_name not in em_name:
        note = "；".join(x for x in (note, f"东方财富口径：{em_name}") if x)
    if category == "unknown" and re.match(r"^(5\d|1[5-8])\d{4}$", pure_code):
        category = "not-applicable"
        note = note or "ETF / 基金标的，不适用实际控制人判定"

    item = {
        "code": code,
        "name": (ths or {}).get("name", ""),
        "finalController": final_name,
        "finalControllerRatio": ((ths or {}).get("finalController") or {}).get("ratio", ""),
        "actualController": actual_name,
        "actualControllerRatio": ((ths or {}).get("actualController") or {}).get("ratio", ""),
        "controllingHolder": controlling_name,
        "controllingHolderRatio": ((ths or {}).get("controllingHolder") or {}).get("ratio", ""),
        "eastmoneyController": em_name,
        "category": category,
        "categoryLabel": CATEGORY_LABELS.get(category, category),
        "source": source,
        "sourceUrl": source_url,
        "note": note,
        "fetchedAt": _now_iso(),
    }
    _controller_cache[code] = (time.time() * 1000, item)
    return {**item, "fromCache": False}


# --------------------------------------------------------------------------- #
# 东财 ACTUAL_HOLDER 批量分类
# --------------------------------------------------------------------------- #
def classify_bulk_holder(raw: str) -> dict[str, str]:
    text = str(raw or "").strip()
    if not text:
        return {"category": "unknown", "label": STATE_CATEGORY_LABELS["unknown"]}
    if re.fullmatch(r"无|无实际控制人|-|--|其他", text):
        return {"category": "none", "label": STATE_CATEGORY_LABELS["none"]}

    tokens = [t.strip() for t in re.split(r"[、,，;；/]", text) if t.strip()]
    joined = "、".join(tokens)

    is_person = lambda t: bool(re.fullmatch(r"[\u4e00-\u9fa5]{2,4}", t)) or bool(
        re.fullmatch(r"[A-Za-z][A-Za-z\s.]{1,30}", t)
    )
    person_tokens = [t for t in tokens if is_person(t)]
    has_state_keyword = bool(re.search(r"国资委|国有资产监督管理|人民政府|财政|汇金|国务院|国有资本|社会保障", joined))
    if person_tokens and not has_state_keyword:
        return {"category": "person", "label": STATE_CATEGORY_LABELS["person"]}

    if re.search(r"国务院国有资产监督管理委员会|国务院国资委", joined):
        return {"category": "sasac-central", "label": STATE_CATEGORY_LABELS["sasac-central"]}
    if re.search(r"国有资产监督管理委员会|国资委", joined):
        return {"category": "sasac-local", "label": STATE_CATEGORY_LABELS["sasac-local"]}
    if re.search(r"中央汇金|全国社会保障基金|国有资本|国有资产经营|国有投资|国有资本运营", joined):
        return {"category": "state-finance", "label": STATE_CATEGORY_LABELS["state-finance"]}
    if (
        re.search(r"中华人民共和国财政部|财政部|中华人民共和国国务院|国务院", joined)
        or re.search(r"人民政府|财政(厅|局|部)|国有资产管理(局|中心|办公室|委员会)|管理委员会|管委会|省委|市委|县委|宣传部|管理局", joined)
    ):
        return {"category": "government", "label": STATE_CATEGORY_LABELS["government"]}
    if re.search(r"中国中信集团|中信集团有限公司|中国光大集团|中国投资有限责任公司", joined):
        return {"category": "government", "label": STATE_CATEGORY_LABELS["government"]}
    if re.search(r"报业集团|日报社|广电集团|广播电视台|出版集团|传媒集团|出版社", joined):
        return {"category": "government", "label": STATE_CATEGORY_LABELS["government"]}
    if re.search(r"供销合作总社|合作社|村民委员会|社团经济企业联合会|集体经济", joined):
        return {"category": "collective", "label": STATE_CATEGORY_LABELS["collective"]}
    if re.search(r"促进会|基金会|联合会|协会|学会|社会团体", joined):
        return {"category": "other", "label": STATE_CATEGORY_LABELS["other"]}
    if re.search(r"集团|控股|投资|有限公司|有限责任公司|总公司|公司|研究院|科学院|大学|委员会|中心", joined):
        return {"category": "state-group", "label": STATE_CATEGORY_LABELS["state-group"]}
    return {"category": "other", "label": STATE_CATEGORY_LABELS["other"]}


def map_ths_category(category: str) -> str:
    return {
        "sasac-central": "sasac-central",
        "sasac-local": "sasac-local",
        "government": "government",
        "state-other": "state-finance",
        "state-group-pending": "state-group",
        "none": "none",
        "non-state": "person",
    }.get(category, "")


def board_of(sec_type: str) -> str:
    if "北京" in sec_type:
        return "北交所"
    if "上交所" in sec_type:
        return "沪主板"
    return "深主板"


def market_prefix_of(code: str) -> str:
    if re.match(r"^(43|83|87|92)", code):
        return "BJ"
    return "SH" if code.startswith("6") else "SZ"


# --------------------------------------------------------------------------- #
# 国资委央企名录
# --------------------------------------------------------------------------- #
def normalize_corp_name(name: str) -> str:
    text = re.sub(r"[\s\u3000]", "", str(name or ""))
    text = re.sub(r"[（(][^）)]*[）)]", "", text)
    return re.sub(r"有限责任公司|有限公司|股份有限公司|股份公司|集团有限公司|集团|控股|总公司|公司", "", text)


def match_central_soe(raw: str, names: list[str]) -> str:
    source = str(raw or "").strip()
    if not source or not names:
        return ""
    cores = [normalize_corp_name(x) for x in re.split(r"[、,，;；/]", source)]
    cores = [c for c in cores if len(c) >= 2]
    if not cores:
        return ""
    for name in names:
        name_core = normalize_corp_name(name)
        if len(name_core) < 2:
            continue
        for core in cores:
            if core == name_core or core in name_core or name_core in core:
                return name
    return ""


async def load_central_soe_names() -> list[str]:
    global _central_soe_names, _central_soe_fetched_at
    if _central_soe_names and time.time() * 1000 - _central_soe_fetched_at < 5 * 24 * 3600 * 1000:
        return _central_soe_names

    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(20.0, connect=6.0), headers=BASE_HEADERS) as client:
            resp = await client.get(CENTRAL_SOE_URL, headers={"Referer": "http://www.sasac.gov.cn/"})
            resp.raise_for_status()
            html = resp.content.decode("utf-8", errors="replace")
        raw_names = [m.group(1).strip() for m in re.finditer(r'<td[^>]*align="left"[^>]*>\s*<a[^>]*>([^<]+)</a>', html)]
        names: list[str] = []
        for name in raw_names:
            primary = re.sub(r"\[[^\]]*\]", "", name).strip()
            if primary:
                names.append(primary)
            for alt in re.findall(r"\[([^\]]+)\]", name):
                alt_name = alt.strip()
                if alt_name:
                    names.append(alt_name)
        if not names:
            raise RuntimeError("未能从国资委官网解析出央企名录")
        _central_soe_names = names
        _central_soe_fetched_at = time.time() * 1000
        return names
    except Exception as exc:  # noqa: BLE001
        logger.warning("[central-soe] 央企名录获取失败，将跳过集团映射: %s", exc)
        return []


def _now_iso() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat()
