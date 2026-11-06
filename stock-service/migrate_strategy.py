#!/usr/bin/env python3
"""strategy_config 表建表脚本（幂等，CREATE TABLE IF NOT EXISTS）。

不触碰现有 7 张表，仅创建选股策略配置单例表。

用法：
    python migrate_strategy.py
"""

from __future__ import annotations

import asyncio
import sys

from sqlalchemy import text

from app.db.session import engine
from app.models import StrategyConfig


async def migrate() -> int:
    async with engine.begin() as conn:
        # 仅针对 strategy_config 表，走 SQLAlchemy 的 create（等价 CREATE TABLE IF NOT EXISTS）
        await conn.run_sync(
            lambda sync_conn: StrategyConfig.__table__.create(sync_conn, checkfirst=True)
        )

    async with engine.connect() as conn:
        exists = await conn.scalar(
            text(
                "SELECT 1 FROM information_schema.tables "
                "WHERE table_schema = current_schema() AND table_name = 'strategy_config'"
            )
        )
        count = await conn.scalar(text("SELECT count(*) FROM strategy_config"))

    print("=" * 62)
    print(f"strategy_config 表：{'已就绪' if exists else '缺失'}")
    print(f"  当前记录数：{count}")
    print("=" * 62)
    await engine.dispose()
    return 0 if exists else 1


def main() -> int:
    try:
        return asyncio.run(migrate())
    except Exception as exc:  # noqa: BLE001
        print(f"\n建表失败 ❌ {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
