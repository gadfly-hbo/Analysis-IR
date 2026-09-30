"""本地项目存储（SQLite 参考实现，proposal 10.1 / PRD 决策 7）。

只做对象索引、状态轴与历史保存；大文件产物由 S3 的快照/证据层落文件系统。
对象按 (kind, id, version) 追加保存，永不覆盖（proposal 7.1/8.3）。
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from toolkit.digest import digest

_SCHEMA_V1 = """
CREATE TABLE IF NOT EXISTS objects (
    kind TEXT NOT NULL,
    id TEXT NOT NULL,
    version INTEGER NOT NULL,
    doc TEXT NOT NULL,
    content_digest TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (kind, id, version)
);
CREATE TABLE IF NOT EXISTS plan_status (
    plan_id TEXT NOT NULL,
    plan_version INTEGER NOT NULL,
    status TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (plan_id, plan_version)
);
CREATE TABLE IF NOT EXISTS audit_log (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    at TEXT NOT NULL,
    actor TEXT NOT NULL,
    action TEXT NOT NULL,
    detail TEXT
);
"""


class ProjectStore:
    """项目级对象存储。每个实例对应一个 SQLite 文件（一个本地项目）。"""

    def __init__(self, db_path: Path) -> None:
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(db_path)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.executescript(_SCHEMA_V1)
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    def put(self, kind: str, obj: dict[str, Any], created_at: str) -> str:
        """追加保存对象的一个版本，返回其内容摘要。同版本重复保存同内容幂等。"""
        obj_id = obj["id"] if "id" in obj else obj["plan_id"]
        version = obj["version"] if "version" in obj else obj["plan_version"]
        doc = json.dumps(obj, ensure_ascii=False, sort_keys=True)
        content_digest = digest(obj)
        self._conn.execute(
            "INSERT OR REPLACE INTO objects (kind, id, version, doc, content_digest, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (kind, obj_id, version, doc, content_digest, created_at),
        )
        self._conn.commit()
        return content_digest

    def get(self, kind: str, obj_id: str, version: int) -> dict[str, Any] | None:
        row = self._conn.execute(
            "SELECT doc FROM objects WHERE kind = ? AND id = ? AND version = ?",
            (kind, obj_id, version),
        ).fetchone()
        return json.loads(row["doc"]) if row else None

    def exists(self, kind: str, obj_id: str, version: int | str) -> bool:
        if isinstance(version, str):
            row = self._conn.execute(
                "SELECT 1 FROM objects WHERE kind = ? AND id = ? AND doc LIKE ?",
                (kind, obj_id, f'%"version": "{version}"%'),
            ).fetchone()
            return row is not None
        return self.get(kind, obj_id, version) is not None

    def latest_version(self, kind: str, obj_id: str) -> int | None:
        row = self._conn.execute(
            "SELECT MAX(version) AS v FROM objects WHERE kind = ? AND id = ?",
            (kind, obj_id),
        ).fetchone()
        return row["v"] if row and row["v"] is not None else None

    def set_status(self, plan_id: str, plan_version: int, status: str, updated_at: str) -> None:
        self._conn.execute(
            "INSERT OR REPLACE INTO plan_status (plan_id, plan_version, status, updated_at) "
            "VALUES (?, ?, ?, ?)",
            (plan_id, plan_version, status, updated_at),
        )
        self._conn.commit()

    def get_status(self, plan_id: str, plan_version: int) -> str | None:
        row = self._conn.execute(
            "SELECT status FROM plan_status WHERE plan_id = ? AND plan_version = ?",
            (plan_id, plan_version),
        ).fetchone()
        return row["status"] if row else None

    def audit(self, at: str, actor: str, action: str, detail: str = "") -> None:
        self._conn.execute(
            "INSERT INTO audit_log (at, actor, action, detail) VALUES (?, ?, ?, ?)",
            (at, actor, action, detail),
        )
        self._conn.commit()
