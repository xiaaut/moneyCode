#!/usr/bin/env bash
# Mac 本地 PostgreSQL 17 启停脚本
#
# 背景：本机没有 Homebrew（也没有 sudo 权限），因此 PostgreSQL 17 通过
# micromamba + conda-forge 安装在用户目录，等价于 brew install postgresql@17：
#   二进制：~/.local/pg17/bin        数据目录：~/.local/pgdata17
#   监听：127.0.0.1:5432            认证：127.0.0.1 trust 免密（密码 mypg123456 同时有效）
#
# 用法：
#   ./pg_local.sh start     启动（幂等）
#   ./pg_local.sh stop      停止
#   ./pg_local.sh restart   重启
#   ./pg_local.sh status    查看状态 + 版本 + 数据库列表

set -euo pipefail

PGBIN="$HOME/.local/pg17/bin"
PGDATA="$HOME/.local/pgdata17"
LOG="$PGDATA/server.log"

if [ ! -x "$PGBIN/pg_ctl" ]; then
  echo "❌ 未找到 $PGBIN/pg_ctl，请先执行：" >&2
  echo "   ~/.local/bin/micromamba create -y -p \$HOME/.local/pg17 -c conda-forge postgresql=17" >&2
  exit 1
fi

is_up() { "$PGBIN/pg_isready" -q -h 127.0.0.1 -p 5432 >/dev/null 2>&1; }

case "${1:-status}" in
  start)
    if is_up; then
      echo "✅ PostgreSQL 已在运行（127.0.0.1:5432）"
      exit 0
    fi
    "$PGBIN/pg_ctl" -D "$PGDATA" -l "$LOG" -w start
    echo "✅ PostgreSQL 已启动（127.0.0.1:5432，日志 $LOG）"
    ;;
  stop)
    if ! is_up; then
      echo "ℹ️  PostgreSQL 未在运行"
      exit 0
    fi
    "$PGBIN/pg_ctl" -D "$PGDATA" -m fast -w stop
    echo "✅ PostgreSQL 已停止"
    ;;
  restart)
    "$0" stop
    "$0" start
    ;;
  status)
    if is_up; then
      echo "✅ 运行中：127.0.0.1:5432"
      "$PGBIN/psql" -h 127.0.0.1 -U postgres -d portfolio_db -tAc "SELECT version();" 2>/dev/null || true
      "$PGBIN/psql" -h 127.0.0.1 -U postgres -d postgres -tAc \
        "SELECT datname || ' (' || pg_size_pretty(pg_database_size(datname)) || ')' FROM pg_database WHERE datistemplate = false ORDER BY datname;"
    else
      echo "❌ 未运行（执行 ./pg_local.sh start 启动）"
      exit 1
    fi
    ;;
  *)
    echo "用法：$0 {start|stop|restart|status}" >&2
    exit 2
    ;;
esac
