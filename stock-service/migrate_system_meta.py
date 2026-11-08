"""建 system_meta 表（幂等，CREATE TABLE IF NOT EXISTS）。

用于持久化「收盘一键更新」的 last_completed_at，避免进程重启丢失。
"""

import asyncio

from sqlalchemy import text

from app.db.session import engine


async def main() -> None:
    async with engine.begin() as conn:
        await conn.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS system_meta (
                    id INTEGER PRIMARY KEY,
                    last_daily_sync_at TIMESTAMPTZ,
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
                )
                """
            )
        )
        # 保证单例行存在
        await conn.execute(
            text(
                """
                INSERT INTO system_meta (id) VALUES (1)
                ON CONFLICT (id) DO NOTHING
                """
            )
        )
    await engine.dispose()
    print("system_meta 表就绪")


if __name__ == "__main__":
    asyncio.run(main())
