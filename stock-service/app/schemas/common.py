"""Schema 公共基类与通用工具。

前端全部使用 camelCase，后端内部使用 snake_case：
    alias_generator=to_camel        -> 序列化输出 camelCase
    populate_by_name=True           -> 也接受 snake_case 入参
"""

from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from pydantic import BaseModel, ConfigDict
from pydantic.alias_generators import to_camel


class CamelModel(BaseModel):
    """camelCase 序列化基类。"""

    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
        from_attributes=True,
    )


class OkResponse(CamelModel):
    ok: bool = True
    message: str = ""


# --------------------------------------------------------------------------- #
# 脏值转换：前端会传 "15.46%" / "-" / "" / null
# --------------------------------------------------------------------------- #
NULL_TOKENS = {"", "-", "--", "—", "nan", "null", "none", "n/a", "无", "无数据"}


def parse_number(value: Any) -> Decimal | None:
    """把 '15.46%' / '-' / 12.3 统一转 Decimal；无法解析返回 None。"""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, Decimal):
        return value if value.is_finite() else None
    if isinstance(value, (int, float)):
        result = Decimal(str(value))
        return result if result.is_finite() else None
    text = str(value).strip().replace(",", "")
    if text.lower() in NULL_TOKENS:
        return None
    text = text.rstrip("%").strip()
    try:
        result = Decimal(text)
        return result if result.is_finite() else None
    except (InvalidOperation, ValueError):
        return None


def parse_float(value: Any) -> float | None:
    result = parse_number(value)
    return float(result) if result is not None else None


def fmt_pct(value: Any, digits: int = 2, suffix: str = "%", dash: str = "-") -> str:
    """3.5 -> '3.50%'；None -> '-'（前端表格直接渲染字符串）。"""
    result = parse_number(value)
    if result is None:
        return dash
    return f"{result:.{digits}f}{suffix}"


def fmt_num(value: Any, digits: int = 2, dash: str = "-") -> str:
    result = parse_number(value)
    if result is None:
        return dash
    return f"{result:.{digits}f}"


def num_or_dash(value: Any, digits: int = 2) -> float | str:
    """数值字段：有值给 float（保留两位），无值给 '-'，与前端原格式一致。"""
    result = parse_number(value)
    if result is None:
        return "-"
    return round(float(result), digits)


def to_iso(value: datetime | date | None) -> str | None:
    if value is None:
        return None
    return value.isoformat()
