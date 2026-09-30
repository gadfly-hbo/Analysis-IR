"""确认服务（proposal 7.3 / 9.2 ApprovalService.approve）。

确认对象是一组精确绑定（六类 digest），不是一句"同意"；
服务自身永不生成确认——必须携带真实 user_action。
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from adapters.local_context.context_port import LocalContext
from adapters.local_store.project_store import ProjectStore
from toolkit.contracts import validate_object
from toolkit.digest import digest
from toolkit.execution import get_registry
from toolkit.plan_service import PlanService

_VALID_ORIGINS = {"cli", "ui", "test"}


class ApprovalRejected(Exception):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(f"{code}: {message}")


def _now() -> str:
    return datetime.now(UTC).isoformat()


class ApprovalService:
    def __init__(
        self, store: ProjectStore, plan_service: PlanService, context: LocalContext
    ) -> None:
        self.store = store
        self.plan_service = plan_service
        self.context = context

    # ---------- approval_target ----------

    def build_target(self, plan: dict[str, Any]) -> dict[str, str]:
        contract = self.store.get(
            "analysis-contract", plan["contract_ref"]["id"], plan["contract_ref"]["version"]
        )
        if contract is None:
            raise ApprovalRejected("MISSING_REFERENCE", "contract_ref 不存在")
        binding = self.store.get(
            "binding-snapshot", plan["binding_ref"]["id"], plan["binding_ref"]["version"]
        )
        metrics = [
            self.store.get("metric-contract", m["id"], m["version"]) for m in plan["metric_refs"]
        ]
        manifest = self.store.get(
            "data-snapshot-manifest",
            plan["dataset_refs"][0]["id"],
            plan["dataset_refs"][0]["version"],
        )
        if binding is None or manifest is None or any(m is None for m in metrics):
            raise ApprovalRejected("MISSING_REFERENCE", "绑定/指标/快照对象缺失")

        method_registry = get_registry(
            self.store, "method-registry",
            plan["method_registry_ref"]["id"], str(plan["method_registry_ref"]["version"]),
        )
        check_registry = get_registry(
            self.store, "check-registry",
            plan["check_registry_ref"]["id"], str(plan["check_registry_ref"]["version"]),
        )
        if method_registry is None or check_registry is None:
            raise ApprovalRejected("MISSING_REFERENCE", "方法/检查注册表缺失或版本不符")

        exec_manifest = self._exec_manifest_for(plan)

        return {
            "analysis_contract_digest": digest(contract),
            "analysis_plan_digest": digest(plan),
            "metric_and_binding_digest": digest(
                {"metrics": metrics, "binding": binding}
            ),
            "data_snapshot_manifest_digest": digest(manifest),
            "method_and_check_registry_digest": digest(
                {"methods": method_registry, "checks": check_registry}
            ),
            "execution_manifest_digest": digest(exec_manifest),
        }

    def _exec_manifest_for(self, plan: dict[str, Any]) -> dict[str, Any]:
        exec_id = f"exec-{plan['plan_id']}"
        latest = self.store.latest_version("execution-manifest", exec_id)
        plan_digest = digest(plan)
        while latest and latest >= 1:
            doc = self.store.get("execution-manifest", exec_id, latest)
            if doc and doc["plan_digest"] == plan_digest:
                return doc
            latest -= 1
        raise ApprovalRejected(
            "MISSING_EXECUTION_MANIFEST",
            f"{exec_id} 没有匹配计划 digest 的执行清单，先编译再确认",
        )

    # ---------- 确认 ----------

    def approve(
        self, plan_id: str, plan_version: int, user_action: dict[str, Any]
    ) -> dict[str, Any]:
        operator = user_action.get("operator")
        if not operator or not isinstance(operator, str):
            raise ValueError("user_action.operator 缺失：服务自身不生成确认")
        if user_action.get("action") != "approve":
            raise ValueError("user_action.action 必须是 approve")
        if user_action.get("origin") not in _VALID_ORIGINS:
            raise ValueError("user_action.origin 必须是 cli/ui/test 之一")

        plan = self.plan_service.get_plan(plan_id, plan_version)
        report = self.plan_service.validate(plan_id, plan_version)
        if not report.g0_passed:
            raise ApprovalRejected("G0_NOT_PASSED", "计划未通过 G0 校验，不可确认")

        metrics = [
            self.store.get("metric-contract", m["id"], m["version"]) for m in plan["metric_refs"]
        ]
        for metric in metrics:
            if metric is None:
                raise ApprovalRejected("MISSING_REFERENCE", "指标契约缺失")
            for key, value in metric["treatments"].items():
                if key != "filters" and value == "unconfirmed":
                    raise ApprovalRejected(
                        "UNCONFIRMED_TREATMENT",
                        f"指标口径未确认: {metric['id']}.{key}（T03）",
                    )

        manifest = self.store.get(
            "data-snapshot-manifest",
            plan["dataset_refs"][0]["id"], plan["dataset_refs"][0]["version"],
        )
        assert manifest is not None  # G0 已保证存在
        if manifest["coverage"].get("kind") == "unverified":
            acks = {a["code"] for a in user_action.get("warnings_acknowledged", [])}
            if "coverage-unverified" not in acks:
                raise ApprovalRejected(
                    "NEEDS_ACKNOWLEDGEMENT",
                    "覆盖未验证：需显式确认 coverage-unverified 警告（G4 决议）",
                )
        if not self.context.verify_snapshot(manifest):
            raise ApprovalRejected(
                "SNAPSHOT_MISMATCH", "快照指纹与清单不符，拒绝确认（T11）"
            )

        target = self.build_target(plan)
        approval = {
            "schema_version": "1.0.0",
            "id": f"approval-{plan_id}-{plan_version}-{uuid.uuid4().hex[:4]}",
            "approval_target": target,
            "user_action": {
                "operator": operator,
                "action": "approve",
                "origin": user_action["origin"],
                "at": _now(),
                "warnings_acknowledged": user_action.get("warnings_acknowledged", []),
            },
            "result": "valid",
        }
        validate_object("approval", approval)
        self.store.put("approval", approval, created_at=_now())

        self.store.set_status(plan_id, plan_version, "APPROVED", _now())
        for version, status in self.store.list_plan_statuses(plan_id):
            if version < plan_version and status == "APPROVED":
                self.store.set_status(plan_id, version, "SUPERSEDED", _now())
        self.store.audit(_now(), operator, "approve", f"{plan_id}@{plan_version}")
        return approval

    def is_authorized(self, plan_id: str, plan_version: int) -> dict[str, Any] | None:
        """复算六类 digest，与有效确认精确匹配；任何内容变化即失效（T10）。"""
        try:
            plan = self.plan_service.get_plan(plan_id, plan_version)
            target = self.build_target(plan)
        except (ApprovalRejected, KeyError):
            return None
        for approval in self.store.list_objects("approval"):
            if (
                approval["result"] == "valid"
                and approval["approval_target"] == target
            ):
                return approval
        return None
