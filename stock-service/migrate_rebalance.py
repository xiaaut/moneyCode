"""建 rebalance_plans 表（幂等，CREATE TABLE IF NOT EXISTS）。"""

import asyncio

from sqlalchemy import text

from app.db.session import engine


async def main() -> None:
    async with engine.begin() as conn:
        await conn.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS rebalance_plans (
                    id SERIAL PRIMARY KEY,
                    code VARCHAR(20) NOT NULL,
                    name VARCHAR(50),
                    account_tag VARCHAR(50),
                    action_type VARCHAR(30) NOT NULL,
                    trigger_condition VARCHAR(10),
                    target_price NUMERIC(10, 3),
                    plan_shares INTEGER,
                    current_cost NUMERIC(10, 3),
                    reason_tag VARCHAR(100),
                    notes TEXT,
                    status VARCHAR(20) NOT NULL DEFAULT 'PENDING',
                    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
                )
                """
            )
        )
        await conn.execute(
            text(
                "CREATE INDEX IF NOT EXISTS ix_rebalance_plans_code ON rebalance_plans (code)"
            )
        )
        await conn.execute(
            text(
                "CREATE INDEX IF NOT EXISTS ix_rebalance_plans_status ON rebalance_plans (status)"
            )
        )
    await engine.dispose()
    print("rebalance_plans 表就绪")


if __name__ == "__main__":
    asyncio.run(main())
