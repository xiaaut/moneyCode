"""网格正交模式升级（幂等，可重复执行）。

背景：把「网格模式」与「步长类型」做成两个正交维度，步长类型枚举由
`pct / fixed` 统一为 `percent / amount`，并把新建默认组合切到
「按基准价步长 + 等比网格 + 百分比 %」。

用法：python migrate_grid_orthogonal.py
"""

import asyncio

from sqlalchemy import text

from app.db.session import engine

STATEMENTS = [
    # 1) 列默认值对齐新默认组合
    "ALTER TABLE grid_strategies ALTER COLUMN gen_mode SET DEFAULT 'step'",
    "ALTER TABLE grid_strategies ALTER COLUMN mode SET DEFAULT 'geometric'",
    "ALTER TABLE grid_strategies ALTER COLUMN step_type SET DEFAULT 'percent'",
    # 2) 历史枚举值更名：fixed -> amount，其余（pct 等）-> percent
    "UPDATE grid_strategies SET step_type = 'amount' WHERE step_type = 'fixed'",
    "UPDATE grid_strategies SET step_type = 'percent' WHERE step_type NOT IN ('amount', 'percent')",
    # 3) 等比网格不允许固定差价步长（正交约束），纠正历史脏组合
    "UPDATE grid_strategies SET step_type = 'percent' WHERE mode = 'geometric' AND step_type = 'amount'",
    # 4) 注释同步
    "COMMENT ON COLUMN grid_strategies.step_type IS '步长类型：percent 百分比 / amount 固定差价'",
    "COMMENT ON COLUMN grid_strategies.mode IS '网格模式(grid_type)：arithmetic 等差 / geometric 等比'",
]


async def main() -> None:
    async with engine.begin() as conn:
        for statement in STATEMENTS:
            await conn.execute(text(statement))

        rows = await conn.execute(
            text(
                "SELECT gen_mode, mode, step_type, count(*) AS n "
                "FROM grid_strategies GROUP BY 1, 2, 3 ORDER BY 1, 2, 3"
            )
        )
        print("grid_strategies 正交维度分布：")
        for row in rows:
            print(f"  gen_mode={row[0]:<6} mode={row[1]:<12} step_type={row[2]:<8} -> {row[3]} 条")
    await engine.dispose()
    print("正交模式迁移完成（默认：step + geometric + percent）")


if __name__ == "__main__":
    asyncio.run(main())
