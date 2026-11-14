"""网格配对回合（grid_cycles）建表 + 成交流水补字段（幂等，可重复执行）。

新增：
- 表 grid_cycles：一开一平的回合账本（LONG_FIRST / SHORT_FIRST / CLOSED）
- 列 grid_trade_records.pair_cycle_id：手工指定配对回合

用法：python migrate_grid_cycles.py
"""

import asyncio

from sqlalchemy import func, select, text

from app.db.session import AsyncSessionLocal, engine
from app.models import GridCycle, GridStrategy, GridTradeRecord
from app.services.grid_cycles import build_level_prices, rebuild_cycles

CREATE_CYCLES = """
CREATE TABLE IF NOT EXISTS grid_cycles (
    id                 VARCHAR(40) PRIMARY KEY,
    strategy_id        VARCHAR(40) NOT NULL REFERENCES grid_strategies(id) ON DELETE CASCADE,
    code               VARCHAR(8)  NOT NULL,
    grid_level         INTEGER,
    cycle_type         VARCHAR(16) NOT NULL,
    status             VARCHAR(20) NOT NULL,

    open_trade_id      VARCHAR(40),
    open_direction     VARCHAR(8)  NOT NULL,
    open_price         NUMERIC(14, 4),
    open_shares        INTEGER,
    open_fee           NUMERIC(14, 2),
    open_time          TIMESTAMPTZ,

    target_close_price NUMERIC(14, 4),

    close_trade_id     VARCHAR(40),
    close_price        NUMERIC(14, 4),
    close_shares       INTEGER,
    close_fee          NUMERIC(14, 2),
    close_time         TIMESTAMPTZ,

    net_profit         NUMERIC(18, 2),

    created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at         TIMESTAMPTZ NOT NULL DEFAULT now()
)
"""

INDEXES = [
    "CREATE INDEX IF NOT EXISTS ix_grid_cycles_strategy_id ON grid_cycles (strategy_id)",
    "CREATE INDEX IF NOT EXISTS ix_grid_cycles_code ON grid_cycles (code)",
    "CREATE INDEX IF NOT EXISTS ix_grid_cycles_grid_level ON grid_cycles (grid_level)",
    "CREATE INDEX IF NOT EXISTS ix_grid_cycles_status ON grid_cycles (status)",
]


async def backfill_cycles() -> None:
    """为「有成交但还没回合」的历史策略补建账本（幂等：已有回合则跳过）。"""
    filled = 0
    async with AsyncSessionLocal() as db:
        strategies = (await db.scalars(select(GridStrategy))).all()
        for strategy in strategies:
            trades = (
                await db.scalars(
                    select(GridTradeRecord).where(GridTradeRecord.strategy_id == strategy.id)
                )
            ).all()
            if not trades:
                continue
            existing = await db.scalar(
                select(func.count()).select_from(GridCycle).where(GridCycle.strategy_id == strategy.id)
            )
            if existing:
                continue

            levels = build_level_prices(
                {
                    "gen_mode": strategy.gen_mode or "range",
                    "mode": strategy.mode or "arithmetic",
                    "base_price": float(strategy.base_price or 0),
                    "upper_price": float(strategy.upper_price or 0),
                    "lower_price": float(strategy.lower_price or 0),
                    "grid_count": int(strategy.grid_count or 0),
                    "step_type": strategy.step_type or "pct",
                    "step_input": float(strategy.step_input or 0),
                    "grids_up": int(strategy.grids_up or 0),
                    "grids_down": int(strategy.grids_down or 0),
                }
            )
            payload = [
                {
                    "id": t.id,
                    "side": t.side,
                    "price": t.price,
                    "shares": t.shares,
                    "fee": t.fee,
                    "executed_at": t.executed_at,
                    "level_index": t.level_index,
                    "pair_cycle_id": t.pair_cycle_id,
                }
                for t in trades
            ]
            cycles = rebuild_cycles(payload, levels)
            if cycles:
                await db.execute(
                    GridCycle.__table__.insert().values(
                        [{**c, "strategy_id": strategy.id, "code": strategy.code} for c in cycles]
                    )
                )
                filled += 1
        await db.commit()
    print(f"  历史策略回填回合：{filled} 个")


async def main() -> None:
    async with engine.begin() as conn:
        await conn.execute(text(CREATE_CYCLES))
        for statement in INDEXES:
            await conn.execute(text(statement))

        added = []
        exists = await conn.scalar(
            text(
                "SELECT 1 FROM information_schema.columns "
                "WHERE table_schema = 'public' AND table_name = 'grid_trade_records' "
                "AND column_name = 'pair_cycle_id'"
            )
        )
        if not exists:
            await conn.execute(
                text("ALTER TABLE grid_trade_records ADD COLUMN IF NOT EXISTS pair_cycle_id VARCHAR(40)")
            )
            await conn.execute(
                text(
                    "COMMENT ON COLUMN grid_trade_records.pair_cycle_id IS "
                    "'手工指定的配对回合 ID；为空则自动 LIFO 配对'"
                )
            )
            added.append("grid_trade_records.pair_cycle_id")

        cycle_count = await conn.scalar(text("SELECT count(*) FROM grid_cycles"))
        trade_count = await conn.scalar(text("SELECT count(*) FROM grid_trade_records"))

    await engine.dispose()
    await backfill_cycles()
    print("grid_cycles 表就绪")
    print(f"  新增字段：{', '.join(added) if added else '无（均已存在）'}")
    print(f"  当前回合数：{cycle_count} / 成交笔数：{trade_count}")


if __name__ == "__main__":
    asyncio.run(main())
