"""证据回收与验收（proposal 9.4 / G3）：EvidencePort.collect / validate。

证据与本次运行绑定：run_id 与 plan_digest 关联校验（T17），
产物摘要逐文件复算（T14），必需检查 FAIL/UNKNOWN 阻断一切接受（G4 决议）。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from adapters.local_store.project_store import ProjectStore
from toolkit.contracts import validate_object
from toolkit.digest import digest_bytes

_ARTIFACT_STEP = {
    "quality-evidence": "quality",
    "scope-evidence": "scope",
    "overall-delta": "overall",
    "store-delta": "store",
    "category-delta": "category",
    "reconciliation-evidence": "validate",
}
_CHECK_ARTIFACTS = {"quality-evidence", "reconciliation-evidence"}


class EvidenceError(Exception):
    pass


@dataclass(frozen=True)
class VerificationResult:
    status: str  # PASSED / LIMITED / FAILED
    checks: list[dict[str, Any]] = field(default_factory=list)
    limitations: list[str] = field(default_factory=list)

    @property
    def acceptance_allowed(self) -> bool:
        return self.status in ("PASSED", "LIMITED")


def _now() -> str:
    return datetime.now(UTC).isoformat()


class EvidenceService:
    def __init__(self, store: ProjectStore, project_dir: Path) -> None:
        self.store = store
        self.project_dir = project_dir

    # ---------- 回收 ----------

    def _run_record(self, run_id: str) -> dict[str, Any]:
        record = self.store.get("run-record", run_id, 1)
        if record is None:
            raise EvidenceError(f"运行记录不存在: {run_id}")
        return record

    def collect(self, run_id: str) -> dict[str, Any]:
        run = self._run_record(run_id)
        plan = self.store.get("analysis-plan-ir", run["plan_id"], run["plan_version"])
        assert plan is not None
        step_versions = {
            s["step_id"]: s["method_ref"].rsplit("@", 1)[1] for s in plan["steps"]
        }
        artifacts_dir = self.project_dir / "runs" / run_id / "artifacts"
        items: list[dict[str, Any]] = []
        for name in sorted(_ARTIFACT_STEP):
            path = artifacts_dir / f"{name}.json"
            if not path.exists():
                continue  # 部分证据（T16）：缺失留给 evidence-coverage 检查判定
            payload = json.loads(path.read_text(encoding="utf-8"))
            items.append({
                "item_id": f"ev-{name}",
                "step_id": _ARTIFACT_STEP[name],
                "kind": "check-record" if name in _CHECK_ARTIFACTS else "method-output",
                "method_or_check_version": step_versions[_ARTIFACT_STEP[name]],
                "artifact": f"runs/{run_id}/artifacts/{name}.json",
                "artifact_digest": digest_bytes(path.read_bytes()),
                "generated_at": _now(),
                "status": (
                    payload.get("status", "DONE")
                    if name in _CHECK_ARTIFACTS
                    else "PASS"
                ),
            })
        if not items:
            raise EvidenceError(f"运行无任何证据产物: {run_id}")

        bundle = {
            "schema_version": "1.0.0",
            "evidence_id": f"evidence-{run_id}",
            "run_id": run_id,
            "plan_digest": run["plan_digest"],
            "items": items,
            "limitations": self._reconcile_limitations(artifacts_dir),
        }
        validate_object("evidence-bundle", bundle)
        self.store.put("evidence-bundle", bundle, created_at=_now())
        return bundle

    @staticmethod
    def _reconcile_limitations(artifacts_dir: Path) -> list[str]:
        path = artifacts_dir / "reconciliation-evidence.json"
        if not path.exists():
            return []
        payload = json.loads(path.read_text(encoding="utf-8"))
        return list(payload.get("limitations", []))

    def latest_bundle(self, run_id: str) -> dict[str, Any]:
        bundle = self.store.get("evidence-bundle", f"evidence-{run_id}", 1)
        if bundle is None:
            raise EvidenceError(f"证据束不存在: {run_id}")
        return bundle

    # ---------- 验收 ----------

    def verify(self, run_id: str) -> VerificationResult:
        run = self._run_record(run_id)
        bundle = self.latest_bundle(run_id)
        checks: list[dict[str, Any]] = []
        limitations: list[str] = list(bundle.get("limitations", []))

        # T17：跨运行伪回执 → 关联不匹配
        checks.append({
            "name": "run-linkage",
            "status": "PASS"
            if (bundle["run_id"] == run_id and bundle["plan_digest"] == run["plan_digest"])
            else "FAIL",
            "detail": f"bundle {bundle['run_id']} vs run {run_id}",
        })

        # T14：产物摘要逐文件复算
        integrity_ok, missing = True, []
        for item in bundle["items"]:
            path = self.project_dir / item["artifact"]
            if not path.exists() or digest_bytes(path.read_bytes()) != item["artifact_digest"]:
                integrity_ok = False
                missing.append(item["artifact"])
        checks.append({
            "name": "artifact-integrity",
            "status": "PASS" if integrity_ok else "FAIL",
            "detail": "产物缺失或摘要不符" if missing else "全部产物摘要一致",
        })

        # 输出契约覆盖（evidence-coverage）
        plan = self.store.get("analysis-plan-ir", run["plan_id"], run["plan_version"])
        assert plan is not None
        present = {i["artifact"].rsplit("/", 1)[-1][: -len(".json")] for i in bundle["items"]}
        required = set(plan["output_contract"]["required_artifacts"])
        uncovered = sorted(required - present)
        checks.append({
            "name": "evidence-coverage",
            "status": "PASS" if not uncovered else "FAIL",
            "detail": f"缺失产物: {uncovered}" if uncovered else "输出契约产物齐全",
        })

        # 必需检查状态（来自检查记录）
        for item in bundle["items"]:
            if item["kind"] == "check-record":
                checks.append({
                    "name": item["item_id"],
                    "status": item["status"],
                    "detail": f"检查记录状态 {item['status']}",
                })

        blocking = [c for c in checks if c["status"] in ("FAIL", "UNKNOWN")]
        status = "FAILED" if blocking else "PASSED"
        result = VerificationResult(status=status, checks=checks, limitations=limitations)
        self.store.set_kv(f"verification:{run_id}", json.dumps(result.__dict__, ensure_ascii=False))
        return result

    def accept(self, run_id: str, decision: str, operator: str, note: str = "") -> dict[str, Any]:
        """人审：ACCEPTED / ACCEPTED_WITH_LIMITATIONS / REJECTED（5.4 三状态分离）。"""
        if decision not in ("ACCEPTED", "ACCEPTED_WITH_LIMITATIONS", "REJECTED"):
            raise ValueError(f"非法验收决定: {decision}")
        stored = self.store.get_kv(f"verification:{run_id}")
        if stored is None:
            raise EvidenceError("先执行 verify 再验收")
        verification = json.loads(stored)
        if decision != "REJECTED" and verification["status"] == "FAILED":
            # G4 决议 + proposal 8.2：必需检查 FAIL/UNKNOWN 阻断一切接受（含带限制）
            raise EvidenceError(
                f"验证 FAILED，不可接受（含带限制）；阻断项: "
                f"{[c['name'] for c in verification['checks'] if c['status'] != 'PASS']}"
            )
        record = {
            "run_id": run_id,
            "decision": decision,
            "operator": operator,
            "at": _now(),
            "note": note,
            "verification_status": verification["status"],
        }
        self.store.set_kv(f"acceptance:{run_id}", json.dumps(record, ensure_ascii=False))
        self.store.audit(_now(), operator, f"accept:{decision}", run_id)
        return record
