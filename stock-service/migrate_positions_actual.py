"""positions 表「已落袋」字段迁移（幂等，支持本地库 / NAS 备份库）。

新增两列，供前端在主行下方渲染「实际持仓还原」附属行：
- realized_profit  做T已实现盈亏（元）
- dividend_amount  累计现金分红（元）

口径（前端附属行）：
    已落袋       = realized_profit + dividend_amount
    实际成本金额 = 主行成本金额 + 已落袋
    实际成本价   = 实际成本金额 / 持仓量
    当前实际获利 = 主行市值 - 实际成本金额

列为 nullable + server_default 0：整数组覆盖接口（刷新现价等）不带这两个字段时，
后端用 COALESCE 保留库中原值，不会被静默清零。

用法：
    python migrate_positions_actual.py                  # 只迁移本地主库（默认）
    python migrate_positions_actual.py --target nas     # 只迁移 NAS 冷备库
    python migrate_positions_actual.py --target both    # 双库都迁（NAS 需在线）
    python migrate_positions_actual.py --compare        # 只读比对双库 positions 列结构

注意：--compare 仅读 information_schema，不写任何数据；迁移只做 ADD COLUMN + 空值补 0，
      不会把 NAS 的旧持仓数据反向覆盖到本地库。
"""

from __future__ import annotations

import argparse
import asyncio
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.config import settings

TABLE = "positions"
COLUMNS: tuple[tuple[str, str], ...] = (
    ("realized_profit", "做T已实现盈亏(元)"),
    ("dividend_amount", "累计现金分红(元)"),
)

# 比对时只看这些属性：名称 + 类型 + 精度 + 可空 + 默认值
COLUMN_ATTRS = (
    "data_type",
    "numeric_precision",
    "numeric_scale",
    "is_nullable",
    "column_default",
)

_COLUMN_SQL = text(
    """
    SELECT column_name, data_type, numeric_precision, numeric_scale,
           is_nullable, column_default, ordinal_position
    FROM information_schema.columns
    WHERE table_schema = 'public' AND table_name = :table
    ORDER BY ordinal_position
    """
)


def _target_url(target: str) -> str:
    if target == "nas":
        url = settings.NAS_ASYNC_DATABASE_URL
        if not url:
            raise SystemExit("❌ 未配置 NAS_DATABASE_URL（见 stock-service/.env），无法迁移 NAS 库")
        return url
    return settings.ASYNC_DATABASE_URL


def _make_engine(url: str) -> AsyncEngine:
    return create_async_engine(
        url,
        poolclass=NullPool,
        connect_args={
            "timeout": 10,
            "server_settings": {"application_name": "migrate_positions_actual"},
        },
    )


async def migrate(engine: AsyncEngine, label: str) -> None:
    async with engine.begin() as conn:
        for column, comment in COLUMNS:
            await conn.execute(
                text(
                    f"""
                    ALTER TABLE {TABLE}
                    ADD COLUMN IF NOT EXISTS {column} NUMERIC(18, 2) DEFAULT 0
                    """
                )
            )
            await conn.execute(text(f"COMMENT ON COLUMN {TABLE}.{column} IS '{comment}'"))
            await conn.execute(
                text(f"UPDATE {TABLE} SET {column} = 0 WHERE {column} IS NULL")
            )
        rows = (
            await conn.execute(_COLUMN_SQL, {"table": TABLE})
        ).mappings().all()
    await engine.dispose()

    owned = {name for name, _ in COLUMNS}
    print(f"✅ {label}：{TABLE} 共 {len(rows)} 列，已落袋字段：")
    for row in rows:
        if row["column_name"] in owned:
            print(
                f"   - {row['column_name']:<18} {row['data_type']}"
                f"({row['numeric_precision']},{row['numeric_scale']})"
                f" nullable={row['is_nullable']} default={row['column_default']}"
            )


async def fetch_columns(engine: AsyncEngine) -> dict[str, dict[str, Any]]:
    async with engine.connect() as conn:
        rows = (await conn.execute(_COLUMN_SQL, {"table": TABLE})).mappings().all()
    await engine.dispose()
    return {row["column_name"]: dict(row) for row in rows}


async def compare() -> bool:
    """只读比对：本地主库 vs NAS 冷备库，positions 列结构是否完全一致。"""
    nas_url = settings.NAS_ASYNC_DATABASE_URL
    if not nas_url:
        raise SystemExit("❌ 未配置 NAS_DATABASE_URL，无法比对")

    local = await fetch_columns(_make_engine(settings.ASYNC_DATABASE_URL))
    nas = await fetch_columns(_make_engine(nas_url))

    names = list(dict.fromkeys([*local.keys(), *nas.keys()]))
    diffs: list[str] = []
    for name in names:
        left, right = local.get(name), nas.get(name)
        if left is None:
            diffs.append(f"   - {name:<22} 仅 NAS 有（本地缺失）")
            continue
        if right is None:
            diffs.append(f"   - {name:<22} 仅本地有（NAS 缺失）")
            continue
        mismatched = [
            f"{attr}: 本地={left[attr]!r} / NAS={right[attr]!r}"
            for attr in COLUMN_ATTRS
            if left[attr] != right[attr]
        ]
        if mismatched:
            diffs.append(f"   - {name:<22} " + "；".join(mismatched))

    print(f"本地库 positions：{len(local)} 列 ｜ NAS 库 positions：{len(nas)} 列")
    if diffs:
        print("❌ 双库列结构存在差异：")
        print("\n".join(diffs))
        return False
    print("✅ 双库 positions 列结构完全一致（名称 / 类型 / 精度 / 可空 / 默认值）")
    return True


async def main() -> int:
    parser = argparse.ArgumentParser(description="positions 已落袋字段迁移 / 双库结构比对")
    parser.add_argument(
        "--target",
        choices=("local", "nas", "both"),
        default="local",
        help="迁移目标库：local（默认）/ nas / both",
    )
    parser.add_argument("--compare", action="store_true", help="迁移后只读比对双库列结构")
    args = parser.parse_args()

    targets = ("local", "nas") if args.target == "both" else (args.target,)
    for target in targets:
        label = "本地主库" if target == "local" else "飞牛 NAS 冷备库"
        await migrate(_make_engine(_target_url(target)), label)

    if args.compare:
        print()
        ok = await compare()
        return 0 if ok else 1
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
