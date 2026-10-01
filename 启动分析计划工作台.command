#!/bin/bash
# 一键启动分析计划工作台（Analysis Plan Studio，双端通用：Mac mini / MacBook）
# 流程：装依赖(首次) → 构建界面(首次) → 起本机服务 → 打开浏览器。
# 端口策略：8765 起依次探测——已被本工作台占用则复用；空闲则使用；
#           被其他进程占用（如别的静态服务器）则跳过并顺延，绝不误开别人的页面。
# 项目数据保存在仓库内 projects/default（已 gitignore，不随 git 同步；换机需自行迁移）。
set -e
cd "$(dirname "$0")"

PROJECT_DIR="$(pwd)/projects/default"

echo "== 分析计划工作台 (Analysis Plan Studio) =="
if [ ! -d .venv ]; then
  echo "[首次运行] 安装 Python 依赖…"
  uv sync
fi
if [ ! -f app/web/dist/index.html ]; then
  echo "[首次运行] 构建工作台界面…"
  (cd app/web && npm install --no-audit --no-fund && npm run build)
fi
mkdir -p "$PROJECT_DIR"

# 严格探活：必须以 200 返回本应用的健康检查 JSON（curl -f 对 4xx/5xx 判失败）
is_our_app() { curl -fsS --max-time 2 "http://127.0.0.1:$1/api/health" 2>/dev/null | grep -q '"status"'; }
# 端口上是否有任何进程在应答（无论是不是本应用）
port_responds() { curl -s -o /dev/null --max-time 2 "http://127.0.0.1:$1/" 2>/dev/null; }

CHOSEN_PORT="" REUSE=0
for p in 8765 8766 8767 8768; do
  if is_our_app "$p"; then CHOSEN_PORT=$p; REUSE=1; break; fi
  if ! port_responds "$p"; then CHOSEN_PORT=$p; break; fi
  echo "[提示] 端口 $p 被其他进程占用，跳过"
done
if [ -z "$CHOSEN_PORT" ]; then
  echo "错误：8765-8768 端口均被占用，请检查后重试"; exit 1
fi

cleanup() { kill "${SERVER_PID:-}" 2>/dev/null || true; }
trap cleanup EXIT

if [ "$REUSE" = "1" ]; then
  echo "[复用] 工作台已在 http://127.0.0.1:$CHOSEN_PORT 运行，直接打开界面"
else
  echo "[启动] 本机服务 http://127.0.0.1:$CHOSEN_PORT（Ctrl+C 退出）"
  uv run python -m app "$PROJECT_DIR" --port "$CHOSEN_PORT" &
  SERVER_PID=$!
  for _ in $(seq 1 40); do
    if is_our_app "$CHOSEN_PORT"; then break; fi
    sleep 1
  done
  if ! is_our_app "$CHOSEN_PORT"; then
    echo "错误：服务未能通过健康检查（端口 $CHOSEN_PORT）"; exit 1
  fi
fi

open "http://127.0.0.1:$CHOSEN_PORT"
wait "${SERVER_PID:-}" 2>/dev/null || true
