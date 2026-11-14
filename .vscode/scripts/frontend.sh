#!/usr/bin/env bash
# VS Code 右下分屏任务：Vite 前端
# 关闭分屏（Ctrl+C / 点垃圾桶）时自动释放 3000 端口
set -u

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT/stock-web" || exit 1

FRONTEND_PORT=3000
CLEANED=0

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

cleanup() {
  if [ "$CLEANED" -eq 1 ]; then return; fi
  CLEANED=1
  trap - EXIT INT TERM HUP QUIT

  echo ""
  echo "🧹 正在清理前端 ..."
  kill_port "$FRONTEND_PORT" >/dev/null 2>&1
  pkill -f "node.*stock-web.*vite" 2>/dev/null
  echo "✅ 前端分屏已退出，${FRONTEND_PORT} 端口已释放"
}
trap cleanup EXIT INT TERM HUP QUIT

if kill_port "$FRONTEND_PORT"; then
  echo "⚠️  已清理 ${FRONTEND_PORT} 端口残留进程"
fi

echo "🚀 启动前端 http://localhost:${FRONTEND_PORT}"
# 后台起子进程 + wait：关闭分屏时 shell 能立刻执行 trap 释放端口
npm run dev &
VITE_PID=$!
wait "$VITE_PID"
exit $?
