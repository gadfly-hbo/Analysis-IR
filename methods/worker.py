"""受限本地 Worker：独立子进程执行固定方法（proposal 10.5）。

协议（JSON-lines over stdio）：
  父进程发一行请求 → Worker 逐步骤执行，每完成一步输出一行进度 → 结束输出 done/error/fatal。
安全边界：
  - 所有路径先 resolve，必须落在 allowed_roots 内（T15）
  - 只执行请求中登记的方法名，方法实现在安装包内版本锁定
  - DuckDB 内存/线程/临时目录受限；输入快照只读
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import duckdb

from methods import sales_methods as m

_STEP_METHODS = {
    "quality": m.run_quality,
    "scope": m.run_scope,
    "period-delta": m.run_period_delta,
    "group-delta": m.run_group_delta,
    "reconcile": m.run_reconcile,
}


def _emit(payload: dict[str, Any]) -> None:
    print(json.dumps(payload, ensure_ascii=False), flush=True)


def _resolve_within(path: Path, roots: list[Path], code: str) -> Path:
    resolved = path.resolve()
    for root in roots:
        try:
            resolved.relative_to(root)
            return resolved
        except ValueError:
            continue
    raise PermissionError(f"{code}: {resolved} 不在允许范围内")


def run_request(request: dict[str, Any]) -> int:
    roots = [Path(r).resolve() for r in request["allowed_roots"]]
    run_dir = _resolve_within(Path(request["run_dir"]), roots, "PATH_NOT_ALLOWED")
    snapshot_path = _resolve_within(Path(request["snapshot_path"]), roots, "PATH_NOT_ALLOWED")
    limits = request["limits"]
    scope = request["scope"]
    coverage = request["coverage"]
    currency = request.get("currency", "CNY")

    conn = duckdb.connect(":memory:")
    conn.execute("SET threads=1")
    conn.execute(f"SET memory_limit='{int(limits['memory_mb'])}MB'")
    (run_dir / "tmp").mkdir(parents=True, exist_ok=True)
    conn.execute(f"SET temp_directory='{run_dir / 'tmp'}'")

    step_results: list[dict[str, Any]] = []
    blocked = False
    context: dict[str, Any] = {}

    try:
        m.load_snapshot(conn, str(snapshot_path))
    except Exception as exc:  # noqa: BLE001 - 回执需要原始错误
        _emit({"type": "error", "step_id": "quality", "message": f"数据读取失败: {exc}"})
        return 2

    for step in request["steps"]:
        step_id, method = step["step_id"], step["method"]
        if blocked:
            step_results.append({"step_id": step_id, "status": "SKIPPED"})
            _emit({"type": "step", "step_id": step_id, "status": "SKIPPED"})
            continue
        try:
            if method == "quality":
                output = m.run_quality(conn, scope, coverage, currency)
                context["quality"] = output
                artifact_name = "quality-evidence"
            elif method == "scope":
                output = m.run_scope(conn, scope, scope.get("eligible_stores"))
                context["scope"] = output
                artifact_name = "scope-evidence"
            elif method == "period-delta":
                output = m.run_period_delta(conn, scope)
                context["overall"] = output
                artifact_name = "overall-delta"
            elif method == "group-delta":
                output = m.run_group_delta(conn, scope, step["params"]["dimension"])
                context[
                    "store_delta" if step["params"]["dimension"] == "store_id" else "category_delta"
                ] = output
                artifact_name = (
                    "store-delta" if step["params"]["dimension"] == "store_id" else "category-delta"
                )
            elif method == "reconcile":
                output = m.run_reconcile(
                    conn, scope, context["overall"], context["store_delta"],
                    context["category_delta"],
                )
                artifact_name = "reconciliation-evidence"
            else:
                raise ValueError(f"未登记的方法: {method}")

            m.write_artifact(run_dir, artifact_name, output)
            status = output.get("status", "DONE")
            step_results.append({"step_id": step_id, "status": "DONE", "check": status})
            _emit({"type": "step", "step_id": step_id, "status": "DONE", "check": status})

            if output.get("kind") == "check-record":
                if output.get("status") == "FAIL" and output.get("failure_action") == "block-step":
                    blocked = True
        except Exception as exc:  # noqa: BLE001 - 回执需要原始错误
            step_results.append({"step_id": step_id, "status": "FAILED", "error": str(exc)})
            _emit({"type": "step", "step_id": step_id, "status": "FAILED", "error": str(exc)})
            blocked = True

    run_status = "FAILED" if blocked else "COMPLETED"
    receipt = {"run_id": request["run_id"], "status": run_status, "steps": step_results}
    (run_dir / "receipt.json").write_text(
        json.dumps(receipt, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    _emit({"type": "done", "status": run_status, "receipt": "receipt.json"})
    return 0 if run_status == "COMPLETED" else 1


def main() -> int:
    try:
        line = sys.stdin.readline()
        request = json.loads(line)
        return run_request(request)
    except PermissionError as exc:
        _emit({"type": "fatal", "code": "PATH_NOT_ALLOWED", "message": str(exc)})
        return 3
    except (KeyError, ValueError, json.JSONDecodeError) as exc:
        _emit({"type": "fatal", "code": "BAD_REQUEST", "message": str(exc)})
        return 4


if __name__ == "__main__":
    sys.exit(main())
