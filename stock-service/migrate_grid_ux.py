"""grid_strategies 表新增网格 UX 字段（幂等，可重复执行）。

新增字段：
- min_commission 单笔最低佣金（元），替代原「免五」布尔开关的粗粒度表达
- gen_mode       网格生成方式：range 区间等分 / step 基准价步长
- step_type      步长类型：pct 百分比 / fixed 固定差价
- step_input     步长输入的原始值（pct 为百分数，fixed 为元）
- grids_up       向上卖出格数
- grids_down     向下买入格数

用法：python migrate_grid_ux.py
"""

import asyncio

from sqlalchemy import text

from app.db.session import engine

COLUMNS: dict[str, str] = {
    "min_commission": "NUMERIC(10, 2) NOT NULL DEFAULT 0.20",
    "gen_mode": "VARCHAR(8) NOT NULL DEFAULT 'range'",
    "step_type": "VARCHAR(8) NOT NULL DEFAULT 'pct'",
    "step_input": "NUMERIC(14, 4)",
    "grids_up": "INTEGER NOT NULL DEFAULT 0",
    "grids_down": "INTEGER NOT NULL DEFAULT 0",
}

COMMENTS: dict[str, str] = {
    "min_commission": "单笔最低佣金(元)，免五填 0",
    "gen_mode": "生成方式：range 区间等分 / step 基准价步长",
    "step_type": "步长类型：pct 百分比 / fixed 固定差价",
    "step_input": "步长输入原始值",
    "grids_up": "向上卖出格数",
    "grids_down": "向下买入格数",
}


async def main() -> None:
    added: list[str] = []
    async with engine.begin() as conn:
        for column, ddl in COLUMNS.items():
            existing = await conn.scalar(
                text(
                    "SELECT 1 FROM information_schema.columns "
                    "WHERE table_schema = 'public' AND table_name = 'grid_strategies' "
                    "AND column_name = :name"
                ),
                {"name": column},
            )
            if existing:
                continue
            await conn.execute(text(f"ALTER TABLE grid_strategies ADD COLUMN IF NOT EXISTS {column} {ddl}"))
            added.append(column)

        for column in added:
            comment = COMMENTS[column].replace("'", "''")
            await conn.execute(text(f"COMMENT ON COLUMN grid_strategies.{column} IS '{comment}'"))

        # 存量 ETF 策略的印花税归零（历史数据可能沿用了 A 股的 0.0005）
        fixed = await conn.execute(
            text(
                "UPDATE grid_strategies SET stamp_duty_rate = 0 "
                "WHERE stamp_duty_rate <> 0 AND ("
                "code LIKE '51%' OR code LIKE '15%' OR code LIKE '56%' OR code LIKE '58%')"
            )
        )

        total = await conn.scalar(text("SELECT count(*) FROM grid_strategies"))
    await engine.dispose()
    print("grid_strategies 字段就绪")
    print(f"  新增字段：{', '.join(added) if added else '无（均已存在）'}")
    print(f"  ETF 印花税归零：{fixed.rowcount} 条")
    print(f"  当前策略数：{total}")


if __name__ == "__main__":
    asyncio.run(main())
