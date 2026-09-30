"""IR → 执行清单的受限编译（proposal 9.1）。

只做可确定的绑定：方法能力、输入快照、资源预算、允许路径、预期输出。
不携带自由 SQL/代码/远程路径；过滤条件由方法实现内部按类型化操作符生成。
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from adapters.local_store.project_store import ProjectStore
from toolkit.contracts import validate_object
from toolkit.digest import digest

_DEFAULT_LIMITS = {  # G7 决议：保守可运行初值，可被调用方覆盖
    "run_timeout_seconds": 600,
    "step_timeout_seconds": 300,
    "memory_mb": 2048,
    "temp_space_mb": 1024,
}


def get_registry(
    store: ProjectStore, kind: str, registry_id: str, semver: str
) -> dict[str, Any] | None:
    latest = store.latest_version(kind, registry_id)
    while latest and latest >= 1:
        doc = store.get(kind, registry_id, latest)
        if doc and doc.get("semver") == semver:
            return doc
        latest -= 1
    return None


def compile_execution_manifest(
    store: ProjectStore,
    plan: dict[str, Any],
    resource_limits: dict[str, int] | None = None,
) -> dict[str, Any]:
    """为给定计划版本编译执行清单并存档（幂等内容 → 同版本；变化 → 新版本）。"""
    method_registry = get_registry(
        store, "method-registry", plan["method_registry_ref"]["id"],
        str(plan["method_registry_ref"]["version"]),
    )
    check_registry = get_registry(
        store, "check-registry", plan["check_registry_ref"]["id"],
        str(plan["check_registry_ref"]["version"]),
    )
    if method_registry is None or check_registry is None:
        raise ValueError("方法或检查注册表缺失/版本不符")

    used_methods = {s["method_ref"].split(":", 1)[1].rsplit("@", 1)[0] for s in plan["steps"]}
    method_versions = {
        e["name"]: e["version"]
        for e in method_registry["entries"]
        if e["name"] in used_methods
    }
    used_checks = {r.split(":", 1)[1].rsplit("@", 1)[0] for r in plan["required_check_refs"]}
    check_versions = {
        e["name"]: e["version"] for e in check_registry["entries"] if e["name"] in used_checks
    }

    snapshot_refs = []
    for ref in plan["dataset_refs"]:
        manifest = store.get("data-snapshot-manifest", ref["id"], ref["version"])
        if manifest is None:
            raise ValueError(f"数据快照清单缺失: {ref['id']}@{ref['version']}")
        snapshot_refs.append(
            {
                "id": manifest["id"],
                "version": manifest["version"],
                "byte_digest": manifest["byte_digest"],
            }
        )

    metric = store.get(
        "metric-contract", plan["metric_refs"][0]["id"], plan["metric_refs"][0]["version"]
    )
    limits = {**_DEFAULT_LIMITS, **(resource_limits or {})}
    manifest = {
        "schema_version": "1.0.0",
        "id": f"exec-{plan['plan_id']}",
        "version": (store.latest_version("execution-manifest", f"exec-{plan['plan_id']}") or 0) + 1,
        "plan_digest": digest(plan),
        "runner_id": "local-runner",
        "runner_capability_digest": digest(
            {"runner": "local-runner", "ir_versions": ["1.0.0"],
             "methods": method_versions, "checks": check_versions,
             "cancellation": "process-termination"}
        ),
        "method_versions": method_versions,
        "check_versions": check_versions,
        "input_snapshots": snapshot_refs,
        "resource_limits": limits,
        "allowed_paths": ["snapshots/", "runs/"],
        "network_policy": "denied",
        "expected_outputs": list(plan["output_contract"]["required_artifacts"]),
        "money_config": {
            "currency": metric["currency"] if metric else "CNY",
            "decimal_places": 2,
            "timezone": "Asia/Shanghai",
        },
    }
    validate_object("execution-manifest", manifest)
    store.put("execution-manifest", manifest, created_at=datetime.now(UTC).isoformat())
    return manifest
