"""行业 / 板块名称统一清洗。

背景：申万二级行业在不同数据源里的写法不一致——
同花顺常见「中药Ⅱ」，东财常见「中药 II」（半角空格 + ASCII 罗马数字），
少数源还会给「化学制药Ⅲ」。这些后缀只是行业分级标记，不应进入业务数据：
一旦入库，就会出现「中药」与「中药Ⅱ」被当成两个行业，筛选、分组、行业占比全乱。

统一规则：去首尾空白 + 剥离末尾的（可选空白）+ 罗马数字分级后缀。
入口统一调用本函数，出口（SQL 存量清洗）用等价正则。
"""

from __future__ import annotations

import re

# 末尾：可选空白（含半角空格、全角空格 \u3000、制表符）+ 罗马数字串
# ASCII: I / II / III / IV / V ；Unicode: Ⅰ / Ⅱ / Ⅲ / Ⅳ / Ⅴ
INDUSTRY_SUFFIX_RE = re.compile(r"[\s\u3000]*([IⅠⅡⅢⅣV]+)$")

# 供 SQL 侧复用的字符类（PostgreSQL 不支持 \uXXXX 转义，必须用字面量全角空格）
SQL_SUFFIX_PATTERN = "[[:space:]　]*[IⅠⅡⅢⅣV]+$"


def clean_industry_name(val: str | None) -> str:
    """清洗行业/板块名称，返回干净字符串（None -> ""）。

    示例：
        "中药 II"    -> "中药"
        "白酒 II"    -> "白酒"
        "化学制药Ⅱ"  -> "化学制药"
        "中药"       -> "中药"
        "  中药　"    -> "中药"
        None         -> ""
    """
    if val is None:
        return ""
    text = str(val).strip()
    if not text:
        return ""
    cleaned = INDUSTRY_SUFFIX_RE.sub("", text).strip()
    # 极端兜底：整串只有罗马数字（脏数据）时保留原值，避免把字段洗成空串后覆盖库内有效值
    return cleaned or text


def clean_industry_or_none(val: str | None) -> str | None:
    """入库专用：清洗后为空则返回 None，配合 coalesce 语义（空值不覆盖旧值）。"""
    cleaned = clean_industry_name(val)
    return cleaned or None
