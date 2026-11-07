#!/usr/bin/env python3
"""资金流向与龙虎榜建表脚本（幂等，CREATE TABLE IF NOT EXISTS）。

不触碰现有 8 张表，仅创建 stock_fund_flow_daily 与 stock_lhb_daily。

用法：
    python migrate_fund_flow.py
"""

from __future__ import annotations

import asyncio
import sys

from sqlalchemy import text

from app.db.session import engine
from app.models import StockFundFlowDaily, StockLhbDaily

TABLES = [StockFundFlowDaily.__table__, StockLhbDaily.__table__]


async def migrate() -> int:
    async with engine.begin() as conn:
        for table in TABLES:
            await conn.run_sync(lambda sync_conn, t=table: t.create(sync_conn, checkfirst=True))

    async with engine.connect() as conn:
        print("=" * 62)
        for table in (StockFundFlowDaily.__tablename__, StockLhbDaily.__tablename__):
            exists = await conn.scalar(
                text(
                    "SELECT 1 FROM information_schema.tables "
                    "WHERE table_schema = current_schema() AND table_name = :name"
                ),
                {"name": table},
            )
            count = await conn.scalar(text(f'SELECT count(*) FROM "{table}"'))
            print(f"{table:<24} {'已就绪' if exists else '缺失'}   记录数={count}")
        print("=" * 62)

    await engine.dispose()
    return 0


def main() -> int:
    try:
        return asyncio.run(migrate())
    except Exception as exc:  # noqa: BLE001
        print(f"\n建表失败 ❌ {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
