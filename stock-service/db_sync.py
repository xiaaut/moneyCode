#!/usr/bin/env python3
"""Mac ↔ NAS PostgreSQL 双库同步工具。

架构前提：
- 主库（Primary）＝ Mac 本地 PostgreSQL（DATABASE_URL），日常运行全走它；
- 冷备（Backup）＝ 飞牛 NAS PostgreSQL（NAS_DATABASE_URL），按需开机。

三个命令：
    python db_sync.py pull-from-nas   # NAS -> Mac：建/对齐表结构 + 全量数据 + 序列校准
    python db_sync.py push-to-nas     # Mac -> NAS：NAS 开机后推送备份（NAS 离线自动安全退出）
    python db_sync.py status          # 双库逐表行数对比；NAS 离线自动降级提示

实现要点：
1) 表结构：以 ORM 模型（Base.metadata）为准 create_all，再逐列补 ALTER TABLE ADD COLUMN
   与 CREATE INDEX IF NOT EXISTS，保证目标库拿到「所有 migrate 脚本里的最新字段与索引」。
2) 数据搬运：两端都用 asyncpg 的 COPY（CSV）流式落临时文件再导入，单事务内完成，
   比 ORM 逐行插入快 1~2 个数量级，且不受内存限制。
3) 序列校准：同步结束后对每个 SERIAL/IDENTITY 列执行 setval(pg_get_serial_sequence(...))，
   避免在 Mac 新增记录时主键冲突。
4) 结果校验：行数逐表比对 + 内容 md5 指纹比对（--no-verify 可跳过）。
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import tempfile
import time
import unicodedata
from typing import Iterable, Sequence

import asyncpg
from sqlalchemy import text
from sqlalchemy.dialects import postgresql as pg_dialect
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from sqlalchemy.pool import NullPool
from sqlalchemy.schema import CreateColumn

import app.models  # noqa: F401  确保 Base.metadata 收集齐全（勿删）
from app.core.config import settings
from app.db.base import Base

# 业务表（按外键依赖拓扑排序，父表在前）
TABLES: list[str] = [t.name for t in Base.metadata.sorted_tables]
_TABLE_MAP = {t.name: t for t in Base.metadata.sorted_tables}

COPY_BUFFER_BYTES = 64 * 1024 * 1024  # 超过此阈值自动落到磁盘临时文件


class SyncError(RuntimeError):
    """同步中止（原因已打印给用户，避免 SystemExit 在退出时重复输出）。"""


# --------------------------------------------------------------------------- #
# 小工具
# --------------------------------------------------------------------------- #
def qi(name: str) -> str:
    """安全引用 SQL 标识符（表名/列名全部来自 ORM，仍做转义兜底）。"""
    return '"' + name.replace('"', '""') + '"'


def disp_width(s: str) -> int:
    """终端显示宽度（中文按 2 列计）。"""
    return sum(2 if unicodedata.east_asian_width(ch) in "WF" else 1 for ch in s)


def pad(s: str, width: int, align: str = "left") -> str:
    gap = max(0, width - disp_width(s))
    return (" " * gap + s) if align == "right" else (s + " " * gap)


def hr(char: str = "=", width: int = 66) -> str:
    return char * width


def log(msg: str = "") -> None:
    print(msg, flush=True)


def fmt_duration(seconds: float) -> str:
    return f"{seconds * 1000:.0f} ms" if seconds < 1 else f"{seconds:.2f} s"


# --------------------------------------------------------------------------- #
# 连接
# --------------------------------------------------------------------------- #
def make_engine(async_url: str, timeout: int) -> AsyncEngine:
    """DDL 专用引擎：NullPool + 短超时，避免 NAS 离线时长时间挂住。"""
    return create_async_engine(
        async_url,
        poolclass=NullPool,
        connect_args={
            "timeout": timeout,
            "server_settings": {"application_name": "db_sync"},
        },
    )


async def connect(dsn: str, label: str, timeout: int) -> asyncpg.Connection:
    """带友好中文报错的 asyncpg 连接。"""
    try:
        conn = await asyncio.wait_for(asyncpg.connect(dsn=dsn, timeout=timeout), timeout + 1)
    except (asyncio.TimeoutError, TimeoutError):
        log(f"❌ 连接 {label} 超时（>{timeout}s），请确认服务是否运行、网络是否可达。")
        raise SyncError("connect-timeout") from None
    except (ConnectionRefusedError, OSError) as exc:
        log(f"❌ 连接 {label} 失败：{type(exc).__name__}: {exc}")
        raise SyncError("connect-failed") from None
    except asyncpg.PostgresError as exc:
        log(f"❌ 连接 {label} 失败：{type(exc).__name__}: {exc}")
        raise SyncError("connect-failed") from None
    # COPY / 指纹统一用 UTC 渲染 timestamptz，保证两端文本表示一致
    await conn.execute("SET TIME ZONE 'UTC'")
    return conn


# --------------------------------------------------------------------------- #
# 表结构对齐
# --------------------------------------------------------------------------- #
async def ensure_schema(engine: AsyncEngine, label: str) -> tuple[list[str], int]:
    """按 ORM 模型建表 / 补列 / 补索引。返回（新增列，索引总数）。"""
    # 1) 缺表直接建（含模型内声明的索引）
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    # 2) 已存在的表补字段（create_all 不会改动既有表）
    added: list[str] = []
    async with engine.begin() as conn:
        for table in Base.metadata.sorted_tables:
            rows = await conn.execute(
                text(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_schema = 'public' AND table_name = :t"
                ),
                {"t": table.name},
            )
            existing = {r[0] for r in rows}
            for col in table.columns:
                if col.name in existing:
                    continue
                ddl = str(CreateColumn(col).compile(dialect=pg_dialect.dialect()))
                await conn.execute(
                    text(f"ALTER TABLE {qi(table.name)} ADD COLUMN IF NOT EXISTS {ddl}")
                )
                added.append(f"{table.name}.{col.name}")

    # 3) 补索引（同样不会被 create_all 覆盖）
    def _create_indexes(sync_conn) -> int:
        count = 0
        for table in Base.metadata.sorted_tables:
            for index in table.indexes:
                index.create(bind=sync_conn, checkfirst=True)
                count += 1
        return count

    async with engine.begin() as conn:
        index_count = await conn.run_sync(_create_indexes)

    detail = f"，补字段 {len(added)} 个（{', '.join(added)}）" if added else ""
    log(f"   ✓ {label} 表结构已对齐：{len(TABLES)} 张表 / {index_count} 个索引{detail}")
    return added, index_count


# --------------------------------------------------------------------------- #
# 数据搬运
# --------------------------------------------------------------------------- #
async def source_tables(conn: asyncpg.Connection) -> set[str]:
    rows = await conn.fetch(
        """
        SELECT table_name FROM information_schema.tables
        WHERE table_schema = 'public' AND table_type = 'BASE TABLE'
        """
    )
    return {r["table_name"] for r in rows}


async def table_columns(conn: asyncpg.Connection, table: str) -> set[str]:
    rows = await conn.fetch(
        """
        SELECT column_name FROM information_schema.columns
        WHERE table_schema = 'public' AND table_name = $1
        """,
        table,
    )
    return {r["column_name"] for r in rows}


async def copy_table(
    src: asyncpg.Connection,
    dst: asyncpg.Connection,
    table: str,
    columns: Sequence[str],
) -> int:
    """COPY ... TO STDOUT -> 临时文件 -> COPY ... FROM STDIN，单表流式搬运。"""
    with tempfile.SpooledTemporaryFile(max_size=COPY_BUFFER_BYTES, mode="w+b") as buf:
        await src.copy_from_table(table, output=buf, columns=list(columns), format="csv")
        buf.seek(0)
        await dst.copy_to_table(table, source=buf, columns=list(columns), format="csv")
    return await dst.fetchval(f"SELECT count(*) FROM {qi(table)}")


async def reset_sequences(conn: asyncpg.Connection, tables: Iterable[str]) -> list[str]:
    """把目标库每个 SERIAL/IDENTITY 序列校准到 MAX(col)，防主键冲突。"""
    fixed: list[str] = []
    for table in tables:
        rows = await conn.fetch(
            """
            SELECT a.attname AS column_name,
                   pg_get_serial_sequence($1, a.attname) AS seq
            FROM pg_attribute a
            WHERE a.attrelid = $1::regclass
              AND a.attnum > 0
              AND NOT a.attisdropped
              AND (a.attidentity <> '' OR pg_get_serial_sequence($1, a.attname) IS NOT NULL)
            """,
            qi(table),
        )
        for row in rows:
            seq = row["seq"]
            if not seq:
                continue
            column = row["column_name"]
            max_value = await conn.fetchval(
                f"SELECT COALESCE(MAX({qi(column)}), 0) FROM {qi(table)}"
            )
            max_value = int(max_value or 0)
            await conn.execute(
                "SELECT setval($1, $2, $3)", seq, max(1, max_value), max_value > 0
            )
            fixed.append(f"{table}.{column} -> {max_value}")
    return fixed


async def table_counts(conn: asyncpg.Connection, tables: Iterable[str]) -> dict[str, int]:
    """逐表精确行数；库中尚不存在的表按 0 计（空库 status 也能跑）。"""
    present = await source_tables(conn)
    counts: dict[str, int] = {}
    for table in tables:
        if table not in present:
            counts[table] = 0
            continue
        counts[table] = await conn.fetchval(f"SELECT count(*) FROM {qi(table)}")
    return counts


async def table_checksum(
    conn: asyncpg.Connection, table: str, columns: Sequence[str]
) -> str:
    """整表内容指纹。

    注意：必须显式指定列顺序再取 ROW(...)::text。两端表的物理列顺序可能不同
    （历史 ALTER TABLE ADD COLUMN 会把新列追加到末尾），直接 t::text 会误报差异。
    """
    row_expr = "ROW(" + ", ".join(qi(c) for c in columns) + ")::text"
    value = await conn.fetchval(
        f"SELECT md5(COALESCE(string_agg(h, '' ORDER BY h), '')) "
        f"FROM (SELECT md5({row_expr}) AS h FROM {qi(table)}) s"
    )
    return value or ""


# --------------------------------------------------------------------------- #
# 双向同步
# --------------------------------------------------------------------------- #
async def run_sync(
    *,
    src_dsn: str,
    dst_dsn: str,
    dst_engine: AsyncEngine,
    src_label: str,
    dst_label: str,
    connect_timeout: int,
    verify: bool,
) -> int:
    started = time.perf_counter()
    log(hr())
    log(f"同步方向：{src_label}  ->  {dst_label}")
    log(hr())

    log(f"[1/5] 连接 {src_label} 与 {dst_label}")
    src = await connect(src_dsn, src_label, connect_timeout)
    dst = await connect(dst_dsn, dst_label, connect_timeout)

    try:
        log("[2/5] 对齐目标库表结构")
        try:
            await ensure_schema(dst_engine, dst_label)
        except Exception as exc:  # noqa: BLE001 - NAS 可能半途掉线（超时/DNS/权限）
            log(f"❌ 目标库表结构对齐失败：{type(exc).__name__}: {exc}")
            raise SyncError("schema-failed") from None

        # 只有两端都存在的列才搬运（防止历史结构差异导致 COPY 失败）
        plan: dict[str, list[str]] = {}
        for table in TABLES:
            src_cols = await table_columns(src, table)
            dst_cols = await table_columns(dst, table)
            common = [c.name for c in _TABLE_MAP[table].columns if c.name in src_cols and c.name in dst_cols]
            if not common:
                log(f"❌ 表 {table} 在两端没有可同步的公共列，已中止（目标库未被改动）")
                raise SyncError("no-common-columns")
            plan[table] = common

        extra = sorted(await source_tables(src) - set(TABLES))
        if extra:
            log(f"   ⚠ 源库存在未纳入 ORM 模型的表，已跳过：{', '.join(extra)}")

        before = await table_counts(src, TABLES)
        total_rows = sum(before.values())
        log(f"   源库共 {len(TABLES)} 张表 / {total_rows:,} 行")

        log("[3/5] 清空目标库业务表（单事务，失败自动回滚）")
        truncate_sql = "TRUNCATE TABLE " + ", ".join(qi(t) for t in TABLES) + " RESTART IDENTITY CASCADE"

        copied: dict[str, int] = {}
        async with dst.transaction():
            await dst.execute(truncate_sql)
            log("[4/5] COPY 流式搬运数据")
            for table in TABLES:
                t0 = time.perf_counter()
                rows = await copy_table(src, dst, table, plan[table])
                copied[table] = rows
                log(
                    f"   - {pad(table, 30)} {rows:>9,} 行   {fmt_duration(time.perf_counter() - t0)}"
                )
            if sum(copied.values()) != total_rows:
                raise RuntimeError(
                    f"搬运行数不一致（源 {total_rows:,} / 目标 {sum(copied.values()):,}），已回滚"
                )

        log("[5/5] 校准自增序列")
        fixed = await reset_sequences(dst, TABLES)
        log(f"   已校准 {len(fixed)} 个序列" + (f"：{', '.join(fixed[:6])}{' ...' if len(fixed) > 6 else ''}" if fixed else ""))

        # 校验
        ok = True
        if verify:
            log("校验：逐表行数 + 内容指纹")
            for table in TABLES:
                dst_rows = await dst.fetchval(f"SELECT count(*) FROM {qi(table)}")
                if dst_rows != before[table]:
                    ok = False
                    log(f"   ✗ {table}: 行数 源 {before[table]:,} / 目标 {dst_rows:,}")
                    continue
                if before[table] == 0:
                    continue
                src_sum = await table_checksum(src, table, plan[table])
                dst_sum = await table_checksum(dst, table, plan[table])
                if src_sum != dst_sum:
                    ok = False
                    log(f"   ✗ {table}: 内容指纹不一致 ({src_sum[:8]} vs {dst_sum[:8]})")
            log("   " + ("✓ 全部一致" if ok else "✗ 存在差异，请检查上面的表"))
        else:
            log("   已跳过深度校验（--no-verify）")

        elapsed = time.perf_counter() - started
        log(hr("-"))
        log(
            f"{'✅ 同步完成' if ok else '⚠ 同步完成但校验未通过'}：{total_rows:,} 行 / "
            f"{len(TABLES)} 张表，耗时 {elapsed:.2f} s"
        )
        log(f"   目标库 {dst_label} 已可独立读写（自增序列已校准）")
        log(hr())
        return 0 if ok else 2
    finally:
        await src.close()
        await dst.close()
        await dst_engine.dispose()


# --------------------------------------------------------------------------- #
# 状态
# --------------------------------------------------------------------------- #
async def cmd_status() -> int:
    if not settings.NAS_DATABASE_DSN:
        log("❌ 未配置 NAS_DATABASE_URL，无法对比 NAS 状态（Mac 本地主库不受影响）")
        return 1

    local = await connect(settings.DATABASE_DSN, "Mac 本地库", 3)
    local_counts = await table_counts(local, TABLES)
    await local.close()

    nas_counts: dict[str, int] | None = None
    nas_error = ""
    try:
        nas = await asyncio.wait_for(
            asyncpg.connect(dsn=settings.NAS_DATABASE_DSN, timeout=2), timeout=3
        )
    except (asyncio.TimeoutError, TimeoutError):
        nas_error = "连接超时"
    except Exception as exc:  # noqa: BLE001 - 离线属于正常业务状态
        nas_error = f"{type(exc).__name__}: {exc}"
    else:
        nas_counts = await table_counts(nas, TABLES)
        await nas.close()

    name_w, num_w, state_w = 30, 12, 14
    log()
    log(hr())
    log("Mac 本地主库  ↔  飞牛 NAS 冷备   逐表行数对比")
    log(hr())
    log(
        pad("表名", name_w)
        + pad("Mac 本地", num_w, "right")
        + pad("NAS", num_w, "right")
        + pad("  状态", state_w, "right")
    )
    log(hr("-"))

    total_local = 0
    total_nas = 0
    diff_tables: list[str] = []
    for table in sorted(TABLES):
        local_n = local_counts[table]
        total_local += local_n
        if nas_counts is None:
            state = "  NAS 离线"
            nas_text = "-"
        else:
            nas_n = nas_counts.get(table, 0)
            total_nas += nas_n
            if nas_n == local_n:
                state = "  ✓ 一致"
                nas_text = f"{nas_n:,}"
            else:
                state = f"  ✗ 差 {local_n - nas_n:+,}"
                nas_text = f"{nas_n:,}"
                diff_tables.append(table)
        log(
            pad(table, name_w)
            + pad(f"{local_n:,}", num_w, "right")
            + pad(nas_text, num_w, "right")
            + pad(state, state_w, "right")
        )

    log(hr("-"))
    if nas_counts is None:
        log(
            pad("合计", name_w)
            + pad(f"{total_local:,}", num_w, "right")
            + pad("-", num_w, "right")
            + pad("  NAS 离线", state_w, "right")
        )
        log(hr())
        log(f"NAS 当前离线（Mac 本地主库独立运行中）")
        log(f"  离线原因：{nas_error or '未知'}")
        log(f"  主库地址：{settings.DB_LABEL}   数据量：{total_local:,} 行 / {len(TABLES)} 张表")
        log(f"  NAS 开机后执行：python db_sync.py push-to-nas   即可推送备份")
    else:
        same = total_local == total_nas and not diff_tables
        log(
            pad("合计", name_w)
            + pad(f"{total_local:,}", num_w, "right")
            + pad(f"{total_nas:,}", num_w, "right")
            + pad("  ✓ 一致" if same else "  ✗ 有差异", state_w, "right")
        )
        log(hr())
        if same:
            log("两库完全一致（逐表行数与内容均可对齐）")
        else:
            log(f"存在差异的表：{', '.join(diff_tables) or '（合计不等）'}")
            log("按需执行：python db_sync.py pull-from-nas（NAS→Mac）或 push-to-nas（Mac→NAS）")
    log()
    return 0 if nas_counts is not None else 0


# --------------------------------------------------------------------------- #
# 命令
# --------------------------------------------------------------------------- #
async def cmd_pull(verify: bool) -> int:
    if not settings.NAS_DATABASE_DSN:
        log("❌ 未配置 NAS_DATABASE_URL，无法从 NAS 拉取（请在 .env 补上 NAS_DATABASE_URL）")
        return 1

    engine = make_engine(settings.ASYNC_DATABASE_URL, settings.DB_CONNECT_TIMEOUT)
    log("目标：Mac 本地主库 -> " + settings.DB_LABEL)
    try:
        return await run_sync(
            src_dsn=settings.NAS_DATABASE_DSN,
            dst_dsn=settings.DATABASE_DSN,
            dst_engine=engine,
            src_label="飞牛 NAS 冷备",
            dst_label="Mac 本地主库",
            connect_timeout=max(settings.NAS_DB_CONNECT_TIMEOUT, 3),
            verify=verify,
        )
    except SyncError:
        log("")
        log("NAS 未连接成功，Mac 本地主库保持不变（未做任何改动）。")
        log("排查：1) NAS 是否开机  2) 同网段能否 ping 通  3) Docker 里 PostgreSQL 容器是否运行")
        return 1
    finally:
        await engine.dispose()


async def cmd_push(verify: bool) -> int:
    if not settings.NAS_DATABASE_DSN:
        log("❌ 未配置 NAS_DATABASE_URL，无法推送到 NAS（请在 .env 补上 NAS_DATABASE_URL）")
        return 1

    engine = make_engine(settings.NAS_ASYNC_DATABASE_URL, settings.NAS_DB_CONNECT_TIMEOUT)
    log("目标：飞牛 NAS 冷备 -> " + settings.NAS_DATABASE_DSN.split("@")[-1])
    try:
        return await run_sync(
            src_dsn=settings.DATABASE_DSN,
            dst_dsn=settings.NAS_DATABASE_DSN,
            dst_engine=engine,
            src_label="Mac 本地主库",
            dst_label="飞牛 NAS 冷备",
            connect_timeout=settings.NAS_DB_CONNECT_TIMEOUT,
            verify=verify,
        )
    except SyncError:
        log("")
        log("NAS 当前离线，已安全退出（Mac 本地主库正常，无需任何处理）。")
        log("等 NAS 开机后再执行一次 push-to-nas 即可。")
        return 1
    finally:
        await engine.dispose()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Mac 本地主库 ↔ 飞牛 NAS 冷备 双库同步工具",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "示例：\n"
            "  python db_sync.py pull-from-nas     # 首次迁移/从 NAS 恢复\n"
            "  python db_sync.py push-to-nas       # NAS 开机后推送备份\n"
            "  python db_sync.py status            # 双库行数对比（NAS 离线自动降级）\n"
        ),
    )
    parser.add_argument(
        "command",
        choices=["pull-from-nas", "push-to-nas", "status"],
        help="pull-from-nas | push-to-nas | status",
    )
    parser.add_argument(
        "--no-verify",
        action="store_true",
        help="跳过同步后的行数与内容指纹校验（更快）",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    verify = not args.no_verify
    try:
        if args.command == "status":
            return asyncio.run(cmd_status())
        if args.command == "pull-from-nas":
            return asyncio.run(cmd_pull(verify))
        return asyncio.run(cmd_push(verify))
    except KeyboardInterrupt:
        log("\n已中断。")
        return 130


if __name__ == "__main__":
    sys.exit(main())
