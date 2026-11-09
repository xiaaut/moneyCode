"""建 stock_kline_daily 表（幂等，CREATE TABLE IF NOT EXISTS）。"""

import asyncio

from sqlalchemy import text

from app.db.session import engine


async def main() -> None:
    async with engine.begin() as conn:
        await conn.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS stock_kline_daily (
                    code VARCHAR(20) NOT NULL,
                    trade_date DATE NOT NULL,
                    open NUMERIC(10, 3),
                    high NUMERIC(10, 3),
                    low NUMERIC(10, 3),
                    close NUMERIC(10, 3),
                    pre_close NUMERIC(10, 3),
                    volume NUMERIC(18, 2),
                    amount NUMERIC(18, 2),
                    turnover_ratio NUMERIC(6, 3),
                    adjust_factor NUMERIC(10, 4),
                    qfq_close NUMERIC(10, 3),
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                    PRIMARY KEY (code, trade_date)
                )
                """
            )
        )
        await conn.execute(
            text(
                "CREATE INDEX IF NOT EXISTS ix_kline_trade_date ON stock_kline_daily (trade_date)"
            )
        )
    await engine.dispose()
    print("stock_kline_daily 表就绪")


if __name__ == "__main__":
    asyncio.run(main())
