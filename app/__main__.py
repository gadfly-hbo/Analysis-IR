"""本地启动入口：python -m app [project_root] [--port N] → http://127.0.0.1:<port>（仅回环）。"""

from __future__ import annotations

import argparse
from pathlib import Path

import uvicorn

from app.server import create_app


def main() -> None:
    parser = argparse.ArgumentParser(prog="aps", description="Analysis Plan Studio 本地服务")
    parser.add_argument(
        "project_root", nargs="?", default=None, help="项目目录（含 project.db 与 files/）"
    )
    parser.add_argument("--port", type=int, default=8765, help="监听端口（默认 8765，仅回环）")
    args = parser.parse_args()

    root = Path(args.project_root) if args.project_root else Path.cwd()
    app = create_app(root)
    uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="info")


if __name__ == "__main__":
    main()
