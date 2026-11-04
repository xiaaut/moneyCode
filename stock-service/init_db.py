#!/usr/bin/env python3
"""自动建表脚本（幂等）。

读取 app.core.config 中的 NAS 连接配置与 app.db.base 的 Base.metadata，
在 portfolio_db 中创建所有不存在的表，并打印建表结果与各表当前记录数。

用法：
    python init_db.py
"""

from __future__ import annotations

import asyncio
import sys

from sqlalchemy import inspect, text
from sqlalchemy.ext.asyncio import AsyncConnection

import app.models  # noqa: F401  注册全部模型到 Base.metadata
from app.core.config import settings
from app.db.base import Base
from app.db.session import engine


async def _table_names(conn: AsyncConnection) -> set[str]:
    """当前 schema 下的表名集合。"""
    return set(await conn.run_sync(lambda sync_conn: inspect(sync_conn).get_table_names()))


async def init_models() -> int:
    tables = sorted(Base.metadata.tables)
    print("=" * 62)
    print(f"目标数据库 : {settings.POSTGRES_HOST}:{settings.POSTGRES_PORT}/{settings.POSTGRES_DB}")
    print(f"待检查表   : {', '.join(tables)}")
    print("=" * 62)

    async with engine.begin() as conn:
        before = await _table_names(conn)
        await conn.run_sync(Base.metadata.create_all)
        after = await _table_names(conn)

    created = sorted(after - before)
    existing = sorted(after & before)
    missing = sorted(set(tables) - after)

    print(f"新建表 ({len(created)}) : {', '.join(created) if created else '无（均已存在）'}")
    print(f"已存在 ({len(existing)}) : {', '.join(existing) if existing else '无'}")
    if missing:
        print(f"⚠️  仍未创建 : {', '.join(missing)}")

    print("-" * 62)
    print(f"{'表名':<22}{'记录数':>8}{'字段数':>8}")
    print("-" * 62)
    async with engine.connect() as conn:
        for table in tables:
            count = await conn.scalar(text(f'SELECT count(*) FROM "{table}"'))
            # 用库里真实的列数，而不是模型定义（便于发现模型与库结构漂移）
            columns = await conn.scalar(
                text(
                    "SELECT count(*) FROM information_schema.columns "
                    "WHERE table_schema = current_schema() AND table_name = :name"
                ),
                {"name": table},
            )
            print(f"{table:<22}{count if count is not None else '-':>8}{columns or 0:>8}")

    await engine.dispose()
    print("=" * 62)
    print("建表完成 ✅")
    return 0 if not missing else 1


def main() -> int:
    try:
        return asyncio.run(init_models())
    except Exception as exc:  # noqa: BLE001 - 脚本入口统一兜底，输出可读错误
        print(f"\n建表失败 ❌ {type(exc).__name__}: {exc}", file=sys.stderr)
        print(
            "排查建议：1) 确认 .env 中 POSTGRES_HOST/PORT/PASSWORD 正确；"
            "2) 确认 NAS 上数据库 portfolio_db 已创建；3) 确认局域网可达 5434 端口。",
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
