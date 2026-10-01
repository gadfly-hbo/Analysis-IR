#!/bin/bash
# 一键启动分析计划工作台（Analysis Plan Studio，双端通用：Mac mini / MacBook）
# 流程：装依赖(首次) → 构建界面(首次) → 起本机服务 → 打开浏览器。
# 项目数据保存在仓库内 projects/default（已 gitignore，不随 git 同步；换机需自行迁移）。
set -e
cd "$(dirname "$0")"

PORT=8765
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

cleanup() { kill "${SERVER_PID:-}" 2>/dev/null || true; }
trap cleanup EXIT

if curl -s -o /dev/null "http://127.0.0.1:$PORT/api/health"; then
  echo "[提示] 端口 $PORT 已有服务在运行，直接打开界面（重启请先 Ctrl+C 旧进程）"
else
  echo "[启动] 本机服务 http://127.0.0.1:$PORT（Ctrl+C 退出）"
  uv run python -m app "$PROJECT_DIR" &
  SERVER_PID=$!
  for _ in $(seq 1 40); do
    if curl -s -o /dev/null "http://127.0.0.1:$PORT/api/health"; then break; fi
    sleep 1
  done
fi

open "http://127.0.0.1:$PORT"
wait "${SERVER_PID:-}" 2>/dev/null || true
