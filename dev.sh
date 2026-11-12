#!/usr/bin/env bash
# 一键启停：本地 PostgreSQL + 后端(8000) + 前端(3000)
#
# 用法：
#   ./dev.sh start     启动三者（数据库 → 后端 → 前端，缺什么起什么，幂等）
#   ./dev.sh stop      停止三者（后端 → 前端 → 数据库，做到 0 进程残留）
#   ./dev.sh restart   先全停再全起
#   ./dev.sh status    查看三者状态
#
# 日志：.logs/backend.log、.logs/frontend.log
# 说明：进程由你自己的终端持有（nohup 脱离），关掉终端也不会停；
#       需要彻底让出内存时执行 ./dev.sh stop。
#       后端刻意不加 --reload：避免后台持续扫描 .py 文件变更。
#       改完代码需要生效时执行 ./dev.sh restart 即可。

set -uo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
SVC="$ROOT/stock-service"
WEB="$ROOT/stock-web"
LOG="$ROOT/.logs"
BACKEND_PORT=8000
FRONTEND_PORT=3000

mkdir -p "$LOG"

port_up() { lsof -nP -iTCP:"$1" -sTCP:LISTEN >/dev/null 2>&1; }

wait_port() {  # wait_port <port> <seconds>
  local port=$1 secs=${2:-40} i=0
  while [ "$i" -lt $((secs * 2)) ]; do
    port_up "$port" && return 0
    sleep 0.5
    i=$((i + 1))
  done
  return 1
}

start_pg() {
  if "$SVC/pg_local.sh" start >/dev/null 2>&1; then
    echo "✅ 数据库  PostgreSQL 127.0.0.1:5432/portfolio_db"
  else
    echo "❌ 数据库启动失败，请执行 $SVC/pg_local.sh status 查看原因"
  fi
}

start_backend() {
  if port_up "$BACKEND_PORT"; then
    echo "✅ 后端    已在运行 http://127.0.0.1:$BACKEND_PORT"
    return 0
  fi
  (
    cd "$SVC" || exit 1
    nohup .venv/bin/uvicorn app.main:app --port "$BACKEND_PORT" \
      >>"$LOG/backend.log" 2>&1 &
    echo $! >"$LOG/backend.pid"
  )
  if wait_port "$BACKEND_PORT" 40; then
    echo "✅ 后端    http://127.0.0.1:$BACKEND_PORT  （日志 .logs/backend.log）"
  else
    echo "❌ 后端启动超时，请看 .logs/backend.log 末尾"
    return 1
  fi
}

start_frontend() {
  if port_up "$FRONTEND_PORT"; then
    echo "✅ 前端    已在运行 http://localhost:$FRONTEND_PORT"
    return 0
  fi
  (
    cd "$WEB" || exit 1
    nohup npm run dev >>"$LOG/frontend.log" 2>&1 &
    echo $! >"$LOG/frontend.pid"
  )
  if wait_port "$FRONTEND_PORT" 60; then
    echo "✅ 前端    http://localhost:$FRONTEND_PORT  （日志 .logs/frontend.log）"
  else
    echo "❌ 前端启动超时，请看 .logs/frontend.log 末尾"
    return 1
  fi
}

stop_services() {
  # 1) 后端 + 前端（先停应用，再停库，避免库被强制中断连接）
  for port in "$BACKEND_PORT" "$FRONTEND_PORT"; do
    pids=$(lsof -nP -tiTCP:"$port" -sTCP:LISTEN 2>/dev/null)
    if [ -n "$pids" ]; then
      # shellcheck disable=SC2086
      kill $pids 2>/dev/null
      echo "⏹  已停止端口 ${port}（pid: $(echo "$pids" | tr '\n' ' ')）"
    else
      echo "ℹ️  端口 $port 未在监听"
    fi
  done
  # 兜底：清掉早期带 --reload 启动的父进程 / 残留 node 子进程
  # （--reload 的父进程会在子进程被杀后立刻重生一个，必须整组清掉）
  pkill -f "uvicorn app.main:app" 2>/dev/null
  pkill -f "stock-web.*vite" 2>/dev/null
  rm -f "$LOG/backend.pid" "$LOG/frontend.pid"

  sleep 1
  if pgrep -f "uvicorn app.main:app" >/dev/null 2>&1 || pgrep -f "stock-web.*vite" >/dev/null 2>&1; then
    pkill -9 -f "uvicorn app.main:app" 2>/dev/null
    pkill -9 -f "stock-web.*vite" 2>/dev/null
    sleep 0.5
  fi

  # 2) 等端口真正释放
  for _ in $(seq 1 20); do
    port_up "$BACKEND_PORT" || port_up "$FRONTEND_PORT" || break
    sleep 0.25
  done

  # 3) 数据库（-m fast：断开剩余连接后干净关闭）
  if "$SVC/pg_local.sh" stop >/dev/null 2>&1; then
    echo "⏹  已停止数据库 127.0.0.1:5432"
  else
    echo "ℹ️  数据库未在运行"
  fi

  # 4) 残留自查
  sleep 0.5
  left=$(pgrep -f "uvicorn app.main:app|stock-web.*vite|pgdata17" 2>/dev/null | wc -l | tr -d ' ')
  echo
  if [ "$left" = "0" ]; then
    echo "✅ 全部停止：数据库 / 后端 / 前端，后台残留进程 0 个"
  else
    echo "⚠️  仍有 $left 个相关进程未退出，可执行：pgrep -fl \"uvicorn|vite|pgdata17\""
  fi
}

status() {
  if "$SVC/pg_local.sh" status >/dev/null 2>&1; then
    echo "✅ 数据库  127.0.0.1:5432/portfolio_db"
  else
    echo "❌ 数据库  未运行（./dev.sh start 或 stock-service/pg_local.sh start）"
  fi
  if port_up "$BACKEND_PORT"; then
    code=$(curl -s -o /dev/null -m 5 -w '%{http_code}' http://127.0.0.1:"$BACKEND_PORT"/api/v1/health || true)
    echo "✅ 后端    127.0.0.1:$BACKEND_PORT  /health=$code"
  else
    echo "❌ 后端    未运行"
  fi
  if port_up "$FRONTEND_PORT"; then
    echo "✅ 前端    http://localhost:$FRONTEND_PORT"
  else
    echo "❌ 前端    未运行"
  fi
}

case "${1:-start}" in
  start)
    start_pg
    start_backend
    start_frontend
    echo
    echo "👉 打开 http://localhost:$FRONTEND_PORT  （API 文档 http://127.0.0.1:$BACKEND_PORT/docs）"
    ;;
  stop) stop_services ;;
  restart)
    stop_services
    sleep 1
    start_pg
    start_backend
    start_frontend
    ;;
  status) status ;;
  *)
    echo "用法：$0 {start|stop|restart|status}" >&2
    exit 2
    ;;
esac
