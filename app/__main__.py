"""本地启动入口：python -m app → http://127.0.0.1:8765（仅回环）。"""

from __future__ import annotations

import sys
from pathlib import Path

import uvicorn

from app.server import create_app


def main(project_root: Path | None = None) -> None:
    root = project_root or Path.cwd()
    app = create_app(root)
    uvicorn.run(app, host="127.0.0.1", port=8765, log_level="info")


if __name__ == "__main__":
    main(Path(sys.argv[1]) if len(sys.argv) > 1 else None)
