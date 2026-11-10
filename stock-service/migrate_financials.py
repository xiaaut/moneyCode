"""建 stock_financial_statements / stock_analysis_notes 表（幂等，CREATE TABLE IF NOT EXISTS）。"""

import asyncio

from sqlalchemy import text

from app.db.session import engine
from app.models.financial import ALL_MONEY_FIELDS


async def main() -> None:
    money_cols = ",\n    ".join(f'"{name}" NUMERIC(20, 2)' for name in ALL_MONEY_FIELDS)
    async with engine.begin() as conn:
        await conn.execute(
            text(
                f"""
                CREATE TABLE IF NOT EXISTS stock_financial_statements (
                    code VARCHAR(20) NOT NULL,
                    report_date DATE NOT NULL,
                    report_label VARCHAR(20),
                    report_type VARCHAR(10),
                    source VARCHAR(20),
                    derived_metrics JSONB NOT NULL DEFAULT '{{}}'::jsonb,
                    {money_cols},
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                    PRIMARY KEY (code, report_date)
                )
                """
            )
        )
        await conn.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS stock_analysis_notes (
                    code VARCHAR(20) PRIMARY KEY,
                    notes TEXT,
                    tags VARCHAR(200),
                    rating VARCHAR(20),
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
                )
                """
            )
        )
    await engine.dispose()
    print("stock_financial_statements / stock_analysis_notes 表就绪")


if __name__ == "__main__":
    asyncio.run(main())
