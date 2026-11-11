"""positions 表 updated_at 字段隔离迁移（幂等）。

目标：
1. 确保 positions 表存在 updated_at 列（若没有则补齐）；
2. 把存量记录的 updated_at 统一重置为历史锚点时间 '2026-01-01 00:00:00+08'，
   便于用户一眼区分「尚未整理过的存量持仓」（前端据此决定是否显示「已核」标记）。

为什么需要这一步：
- 历史上后台「刷新现价 / 收盘一键更新」通过 ORM onupdate 把 updated_at 一起刷新了，
  导致所有持仓 updated_at 都变成了最近一次同步时间，失去「用户最近整理时间」语义。
- 本迁移把这些被污染的值统一锚定为历史时间，配合后端移除 onupdate 后，
  updated_at 将只在用户主动新增/修改持仓时更新为当前时间。
"""

import asyncio

from sqlalchemy import text

from app.db.session import engine


async def main() -> None:
    async with engine.begin() as conn:
        # 1) 幂等补列（表里可能还没有 updated_at）
        await conn.execute(
            text(
                """
                ALTER TABLE positions
                ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
                """
            )
        )
        # 2) 存量记录统一锚定为历史时间（北京时间 2026-01-01 00:00:00）
        await conn.execute(
            text(
                """
                UPDATE positions
                SET updated_at = '2026-01-01 00:00:00+08'
                """
            )
        )
    await engine.dispose()
    print("positions.updated_at 已就绪：存量记录已统一锚定为 2026-01-01 00:00:00+08")


if __name__ == "__main__":
    asyncio.run(main())
