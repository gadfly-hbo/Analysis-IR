"""Run Controller：G2 运行前门禁、子进程 Worker 调度、预算与状态机（proposal 8/10.5）。"""

from __future__ import annotations

import json
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from adapters.local_context.context_port import LocalContext
from adapters.local_store.project_store import ProjectStore
from methods.sales_methods import SALES_METHODS
from toolkit.approval_service import ApprovalService
from toolkit.contracts import validate_object
from toolkit.plan_service import PlanService


def _now() -> str:
    return datetime.now(UTC).isoformat()


class RunController:
    def __init__(
        self,
        store: ProjectStore,
        plan_service: PlanService,
        approval_service: ApprovalService,
        context: LocalContext,
        project_dir: Path,
        worker_cmd: list[str] | None = None,
    ) -> None:
        self.store = store
        self.plan_service = plan_service
        self.approval_service = approval_service
        self.context = context
        self.project_dir = project_dir
        self.worker_cmd = worker_cmd or [sys.executable, "-m", "methods.worker"]

    # ---------- 请求组装 ----------

    def _build_request(
        self, plan: dict[str, Any], run_id: str, run_dir: Path, manifest: dict[str, Any]
    ) -> dict[str, Any]:
        contract = self.store.get(
            "analysis-contract", plan["contract_ref"]["id"], plan["contract_ref"]["version"]
        )
        binding = self.store.get(
            "binding-snapshot", plan["binding_ref"]["id"], plan["binding_ref"]["version"]
        )
        assert contract is not None and binding is not None
        snapshot_manifest = self.store.get(
            "data-snapshot-manifest",
            plan["dataset_refs"][0]["id"], plan["dataset_refs"][0]["version"],
        )
        assert snapshot_manifest is not None
        metric = self.store.get(
            "metric-contract", plan["metric_refs"][0]["id"], plan["metric_refs"][0]["version"]
        )

        scope: dict[str, Any] = {
            "base": contract["comparison_scope"]["base_period"],
            "report": contract["comparison_scope"]["report_period"],
        }
        if binding["scope"].get("same_store") and binding["scope"].get("eligible_stores"):
            scope["eligible_stores"] = binding["scope"]["eligible_stores"]

        steps = []
        for step in plan["steps"]:
            method = step["method_ref"].split(":", 1)[1].rsplit("@", 1)[0]
            steps.append({
                "step_id": step["step_id"],
                "method": method,
                "params": step.get("params", {}),
            })
        return {
            "run_id": run_id,
            "run_dir": str(run_dir),
            "snapshot_path": snapshot_manifest["snapshot_path"],
            "allowed_roots": [str(self.project_dir)],
            "limits": manifest["resource_limits"],
            "scope": scope,
            "coverage": snapshot_manifest["coverage"],
            "currency": metric["currency"] if metric else "CNY",
            "steps": steps,
        }

    # ---------- 执行 ----------

    def start(self, plan_id: str, plan_version: int, operator: str) -> dict[str, Any]:
        plan = self.plan_service.get_plan(plan_id, plan_version)
        started_at = _now()
        run_id = f"run-{self.store.next_seq('run'):04d}"

        # G2 门禁：能力 → 授权 → 快照
        block = self._preflight_block(plan)
        if block:
            return self._record(
                run_id, plan, started_at, "BLOCKED", [], block, used_approval=None
            )

        approval = self.approval_service.is_authorized(plan_id, plan_version)
        assert approval is not None  # preflight 已保证

        exec_manifest = self._exec_manifest_for(plan)
        run_dir = self.project_dir / "runs" / run_id
        run_dir.mkdir(parents=True, exist_ok=True)
        request = self._build_request(plan, run_id, run_dir, exec_manifest)

        process = subprocess.Popen(  # noqa: S603 - 固定命令，无 shell
            self.worker_cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, text=True,
        )
        assert process.stdin is not None and process.stdout is not None
        try:
            out, err = process.communicate(
                input=json.dumps(request, ensure_ascii=False) + "\n",
                timeout=exec_manifest["resource_limits"]["run_timeout_seconds"],
            )
        except subprocess.TimeoutExpired:
            process.kill()
            try:
                out, err = process.communicate(timeout=5)
            except subprocess.TimeoutExpired:
                out, err = "", ""
            steps = self._progress_from_stdout(out)
            return self._record(run_id, plan, started_at, "TIMED_OUT", steps,
                                "timeout", approval["id"])

        steps, final_status = self._parse_worker_output(out, err)
        status = final_status  # COMPLETED 只表示计算完成（8.3），不代表验收通过
        reason = "completed" if status == "COMPLETED" else "error"
        return self._record(run_id, plan, started_at, status, steps, reason, approval["id"])

    def cancel(self, run_id: str, operator: str) -> dict[str, Any]:
        raise NotImplementedError("同步执行模型下取消由超时/中断路径处理；异步调度属后续增强")

    # ---------- 门禁 ----------

    def _preflight_block(self, plan: dict[str, Any]) -> str | None:
        for step in plan["steps"]:
            method_ref = step["method_ref"]
            name, _, version = method_ref.split(":", 1)[1].rpartition("@")
            if SALES_METHODS.get(name) != version:
                return "CAPABILITY_MISMATCH"
        if self.approval_service.is_authorized(plan["plan_id"], plan["plan_version"]) is None:
            return "NOT_AUTHORIZED"
        manifest = self.store.get(
            "data-snapshot-manifest",
            plan["dataset_refs"][0]["id"], plan["dataset_refs"][0]["version"],
        )
        if manifest is None or not self.context.verify_snapshot(manifest):
            return "SNAPSHOT_MISMATCH"
        contract = self.store.get(
            "analysis-contract", plan["contract_ref"]["id"], plan["contract_ref"]["version"]
        )
        scope = (contract or {}).get("comparison_scope", {})
        if not all(
            isinstance(scope.get(p), dict) and scope[p].get("start") and scope[p].get("end")
            for p in ("base_period", "report_period")
        ):
            return "NOT_AUTHORIZED"  # 缺比较范围不可能持有有效确认；防御性兜底
        return None

    def _exec_manifest_for(self, plan: dict[str, Any]) -> dict[str, Any]:
        exec_id = f"exec-{plan['plan_id']}"
        from toolkit.digest import digest

        latest = self.store.latest_version("execution-manifest", exec_id)
        plan_digest = digest(plan)
        while latest and latest >= 1:
            doc = self.store.get("execution-manifest", exec_id, latest)
            if doc and doc["plan_digest"] == plan_digest:
                return doc
            latest -= 1
        raise ValueError(f"缺少匹配的执行清单: {exec_id}")

    # ---------- 回执 ----------

    @staticmethod
    def _progress_from_stdout(out: str) -> list[dict[str, Any]]:
        steps = []
        for line in out.splitlines():
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if event.get("type") == "step":
                entry: dict[str, Any] = {
                    "step_id": event["step_id"],
                    "status": event.get("status", "FAILED"),
                }
                if event.get("error"):
                    entry["error"] = str(event["error"])
                steps.append(entry)
        return steps

    def _parse_worker_output(self, out: str, err: str) -> tuple[list[dict[str, Any]], str]:
        steps = self._progress_from_stdout(out)
        final = None
        for line in out.splitlines():
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if event.get("type") == "done":
                final = event["status"]
            if event.get("type") == "fatal":
                final = "FAILED"
        if final is None:
            final = "FAILED"
            if err.strip():
                steps.append({"step_id": "worker", "status": "FAILED", "error": err.strip()[:500]})
        return steps, final

    def _record(
        self,
        run_id: str,
        plan: dict[str, Any],
        started_at: str,
        status: str,
        steps: list[dict[str, Any]],
        terminated_reason: str,
        used_approval: str | None,
    ) -> dict[str, Any]:
        from toolkit.digest import digest

        reason = {
            "CAPABILITY_MISMATCH": "capability-mismatch",
            "NOT_AUTHORIZED": "not-authorized",
            "SNAPSHOT_MISMATCH": "snapshot-mismatch",
        }.get(terminated_reason, terminated_reason)
        record: dict[str, Any] = {
            "schema_version": "1.0.0",
            "run_id": run_id,
            "plan_id": plan["plan_id"],
            "plan_version": plan["plan_version"],
            "plan_digest": digest(plan),
            "status": status,
            "steps": [
                {k: v for k, v in
                 (("step_id", s["step_id"]), ("status", s["status"]),
                  ("error", s.get("error"))) if v is not None}
                for s in steps
            ] or [{"step_id": "preflight", "status": "FAILED"}],
            "started_at": started_at,
            "ended_at": _now(),
            "terminated_reason": reason,
        }
        if used_approval is not None:
            record["used_approval"] = used_approval
        validate_object("run-record", record)
        self.store.put("run-record", record, created_at=_now())
        self.store.audit(_now(), "controller", "run", f"{run_id} {status}")
        return record
