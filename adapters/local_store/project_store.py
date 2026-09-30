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
CREATE TABLE IF NOT EXISTS kv (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


_ID_KEYS = (
    "cr_id", "evidence_id", "finding_id", "template_id",
    "run_id", "plan_id", "id",
)


def _object_id(obj: dict[str, Any]) -> str:
    for key in _ID_KEYS:
        if key in obj:
            return str(obj[key])
    raise KeyError(f"对象缺少可识别的主键字段（{_ID_KEYS}）")


class ImmutableViolation(Exception):
    """同一 (kind, id, version) 以不同内容再次保存——对象版本不可覆盖。"""


class ProjectStore:
    """项目级对象存储。每个实例对应一个 SQLite 文件（一个本地项目）。"""

    def __init__(self, db_path: Path) -> None:
        db_path.parent.mkdir(parents=True, exist_ok=True)
        # check_same_thread=False：本地单进程服务 + FastAPI 线程池共用一个连接（WAL）
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.executescript(_SCHEMA_V1)
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    def put(self, kind: str, obj: dict[str, Any], created_at: str) -> str:
        """追加保存对象的一个版本，返回其内容摘要。

        同 (kind, id, version) 重复保存同内容幂等；内容不同则拒绝
        （proposal 7.1/8.3：正式对象版本不原地覆盖）。
        """
        obj_id = _object_id(obj)
        version = obj.get("version") or obj.get("plan_version") or 1
        doc = json.dumps(obj, ensure_ascii=False, sort_keys=True)
        content_digest = digest(obj)
        try:
            self._conn.execute(
                "INSERT INTO objects (kind, id, version, doc, content_digest, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (kind, obj_id, version, doc, content_digest, created_at),
            )
        except sqlite3.IntegrityError:
            existing = self._conn.execute(
                "SELECT content_digest FROM objects WHERE kind = ? AND id = ? AND version = ?",
                (kind, obj_id, version),
            ).fetchone()
            if existing and existing["content_digest"] != content_digest:
                raise ImmutableViolation(
                    f"{kind} {obj_id}@{version} 已存在且内容不同，禁止覆盖；请创建新版本"
                ) from None
        self._conn.commit()
        return content_digest

    def find_content_digest(self, kind: str, obj_id: str, version: int) -> str | None:
        row = self._conn.execute(
            "SELECT content_digest FROM objects WHERE kind = ? AND id = ? AND version = ?",
            (kind, obj_id, version),
        ).fetchone()
        return row["content_digest"] if row else None

    def list_objects(self, kind: str) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT doc FROM objects WHERE kind = ? ORDER BY id, version", (kind,)
        ).fetchall()
        return [json.loads(r["doc"]) for r in rows]

    def list_plan_statuses(self, plan_id: str) -> list[tuple[int, str]]:
        rows = self._conn.execute(
            "SELECT plan_version, status FROM plan_status WHERE plan_id = ? ORDER BY plan_version",
            (plan_id,),
        ).fetchall()
        return [(r["plan_version"], r["status"]) for r in rows]

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

    def next_seq(self, name: str) -> int:
        """原子递增命名序列（run_id 编号用）。"""
        cur = self._conn.execute("BEGIN IMMEDIATE")
        try:
            row = self._conn.execute("SELECT value FROM kv WHERE key = ?", (name,)).fetchone()
            next_value = (int(row["value"]) + 1) if row else 1
            self._conn.execute(
                "INSERT INTO kv (key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (name, str(next_value)),
            )
            self._conn.commit()
            return next_value
        except Exception:
            self._conn.rollback()
            raise
        finally:
            cur.close()

    def set_kv(self, key: str, value: str) -> None:
        self._conn.execute(
            "INSERT INTO kv (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )
        self._conn.commit()

    def get_kv(self, key: str) -> str | None:
        row = self._conn.execute("SELECT value FROM kv WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else None

    def audit(self, at: str, actor: str, action: str, detail: str = "") -> None:
        self._conn.execute(
            "INSERT INTO audit_log (at, actor, action, detail) VALUES (?, ?, ?, ?)",
            (at, actor, action, detail),
        )
        self._conn.commit()
