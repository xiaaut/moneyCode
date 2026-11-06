#!/usr/bin/env python3
"""stocks 表字段平滑扩展脚本（幂等）。

用 ALTER TABLE ... ADD COLUMN IF NOT EXISTS 为 stocks 表补充 AKShare 全市场
行情与财报字段，保留已有 3596+ 条国企 / MSCI 标记数据不受影响。

用法：
    python migrate_stocks.py
"""

from __future__ import annotations

import asyncio
import sys

from sqlalchemy import text

from app.db.session import engine

# 新增字段：字段名 -> 列定义（PostgreSQL 类型）
NEW_COLUMNS: dict[str, str] = {
    "volume": "numeric(20, 4)",
    "turnover_amount": "numeric(20, 4)",
    "turnover_rate": "numeric(10, 4)",
    "circ_mv": "numeric(20, 4)",
    "change_60d": "numeric(10, 4)",
    "change_ytd": "numeric(10, 4)",
    "revenue": "numeric(20, 4)",
    "revenue_yoy": "numeric(12, 4)",
    "net_profit": "numeric(20, 4)",
    "net_profit_yoy": "numeric(12, 4)",
    "gross_margin": "numeric(10, 4)",
    "report_period": "varchar(16)",
}

# 字段中文注释
COLUMN_COMMENTS: dict[str, str] = {
    "volume": "成交量（手）",
    "turnover_amount": "成交额（元）",
    "turnover_rate": "换手率(%)",
    "circ_mv": "流通市值（元）",
    "change_60d": "60 日涨跌幅(%)",
    "change_ytd": "年初至今涨跌幅(%)",
    "revenue": "营业总收入（元）",
    "revenue_yoy": "营收同比增长(%)",
    "net_profit": "净利润（元）",
    "net_profit_yoy": "净利润同比增长(%)",
    "gross_margin": "销售毛利率(%)",
    "report_period": "最新财报期，如 2026Q3",
}


async def migrate() -> int:
    added: list[str] = []
    existing: list[str] = []

    async with engine.begin() as conn:
        for column, definition in NEW_COLUMNS.items():
            result = await conn.execute(
                text(
                    "SELECT 1 FROM information_schema.columns "
                    "WHERE table_schema = current_schema() "
                    "AND table_name = 'stocks' AND column_name = :name"
                ),
                {"name": column},
            )
            if result.scalar() is not None:
                existing.append(column)
                continue
            await conn.execute(
                text(f'ALTER TABLE stocks ADD COLUMN IF NOT EXISTS "{column}" {definition}')
            )
            added.append(column)

        # 补充列注释（COMMENT ON COLUMN 本身幂等；PG 的 COMMENT 不支持绑定参数，需内联字面量）
        for column in added:
            comment = COLUMN_COMMENTS.get(column, "").replace("'", "''")
            await conn.execute(
                text(f"COMMENT ON COLUMN stocks.{column} IS '{comment}'")
            )

        total = await conn.scalar(text("SELECT count(*) FROM stocks"))

    print("=" * 62)
    print(f"stocks 表字段扩展完成")
    print(f"  新增字段 ({len(added)}) : {', '.join(added) if added else '无（均已存在）'}")
    print(f"  已存在   ({len(existing)}) : {', '.join(existing) if existing else '无'}")
    print(f"  当前总行数 : {total}")
    print("=" * 62)
    return 0


def main() -> int:
    try:
        return asyncio.run(migrate())
    except Exception as exc:  # noqa: BLE001
        print(f"\n迁移失败 ❌ {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
