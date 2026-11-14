#!/usr/bin/env bash
# VS Code 左下分屏任务：本地 PostgreSQL + FastAPI 后端
# 关闭分屏（Ctrl+C / 点垃圾桶）时自动释放 8000 端口并关停数据库
set -u

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT/stock-service" || exit 1

BACKEND_PORT=8000
CLEANED=0

# 按端口精确清理（先 TERM 再 KILL），有残留返回 0
kill_port() {
  local port="$1" pids
  pids="$(lsof -nP -tiTCP:"$port" -sTCP:LISTEN 2>/dev/null)"
  [ -z "$pids" ] && return 1
  # shellcheck disable=SC2086
  kill $pids 2>/dev/null
  sleep 0.4
  pids="$(lsof -nP -tiTCP:"$port" -sTCP:LISTEN 2>/dev/null)"
  if [ -n "$pids" ]; then
    # shellcheck disable=SC2086
    kill -9 $pids 2>/dev/null
  fi
  return 0
}

# 退出钩子：guard 防止 EXIT 与 INT/TERM/HUP 重复触发
cleanup() {
  if [ "$CLEANED" -eq 1 ]; then return; fi
  CLEANED=1
  trap - EXIT INT TERM HUP QUIT

  echo ""
  echo "🧹 正在清理后端与本地数据库 ..."
  pkill -f "uvicorn app.main:app" 2>/dev/null
  kill_port "$BACKEND_PORT" >/dev/null 2>&1
  echo "⏹  已释放 ${BACKEND_PORT} 端口"
  if ./pg_local.sh stop >/dev/null 2>&1; then
    echo "⏹  已关停本地 PostgreSQL (127.0.0.1:5432)"
  fi
  echo "✅ 后端分屏已退出，无后台残留"
}
trap cleanup EXIT INT TERM HUP QUIT

# 启动前清场
if kill_port "$BACKEND_PORT"; then
  echo "⚠️  已清理 ${BACKEND_PORT} 端口残留进程"
fi
pkill -f "uvicorn app.main:app" 2>/dev/null
sleep 0.2

echo "🐘 启动本地 PostgreSQL ..."
if ! ./pg_local.sh start; then
  echo "❌ PostgreSQL 启动失败，已中止"
  exit 1
fi

echo "🚀 启动后端 http://127.0.0.1:${BACKEND_PORT}（reload 监听 app/）"
# 后台起子进程 + wait：这样 shell 收到 TERM/HUP 时能立刻执行 trap，
# 不会因为「前台命令未结束」而延迟清理（exec 则完全丢失 trap）。
.venv/bin/uvicorn app.main:app --host 0.0.0.0 --port "$BACKEND_PORT" --reload --reload-dir app &
UVICORN_PID=$!
wait "$UVICORN_PID"
exit $?
