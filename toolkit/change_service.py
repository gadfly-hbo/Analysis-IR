"""变更请求：提议、语义合入、外观变更注释化（proposal 5.3 / G3 决议）。"""

from __future__ import annotations

import copy
import uuid
from datetime import UTC, datetime
from typing import Any

from adapters.local_store.project_store import ProjectStore
from toolkit.contracts import validate_object
from toolkit.plan_service import PlanService


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _set_path(obj: dict[str, Any], path: str, value: Any) -> None:
    keys = path.split(".")
    node: Any = obj
    for key in keys[:-1]:
        node = node[key]
    node[keys[-1]] = value


class ChangeService:
    def __init__(self, store: ProjectStore, plan_service: PlanService) -> None:
        self.store = store
        self.plan_service = plan_service

    def propose(
        self,
        plan_id: str,
        plan_version: int,
        reason: str,
        proposed_by: str,
        changes: list[dict[str, Any]],
    ) -> dict[str, Any]:
        cr = {
            "schema_version": "1.0.0",
            "cr_id": f"cr-{uuid.uuid4().hex[:8]}",
            "version": 1,
            "plan_id": plan_id,
            "reason": reason,
            "proposed_by": proposed_by,
            "changes": changes,
            "status": "proposed",
        }
        validate_object("change-request", cr)
        self.store.put("change-request", cr, created_at=_now())
        return cr

    def _load_latest(self, cr_id: str) -> dict[str, Any]:
        latest = self.store.latest_version("change-request", cr_id)
        if latest is None:
            raise KeyError(f"change request not found: {cr_id}")
        cr = self.store.get("change-request", cr_id, latest)
        assert cr is not None
        return cr

    def withdraw(self, cr_id: str, operator: str) -> dict[str, Any]:
        cr = self._load_latest(cr_id)
        if cr["status"] != "proposed":
            raise ValueError(f"CR 不可撤回（状态 {cr['status']}）")
        withdrawn = {**cr, "version": cr["version"] + 1, "status": "withdrawn",
                     "decided_at": _now()}
        validate_object("change-request", withdrawn)
        self.store.put("change-request", withdrawn, created_at=_now())
        self.store.audit(_now(), operator, "withdraw_cr", cr_id)
        return withdrawn

    def merge(self, cr_id: str, operator: str) -> dict[str, Any]:
        cr = self._load_latest(cr_id)
        if cr["status"] != "proposed":
            raise ValueError(f"CR 不可合入（状态 {cr['status']}）")

        if all(c["impact"] == "cosmetic" for c in cr["changes"]):
            # proposal 5.3：仅展示性修改 → 注释记录，不产生新版本
            self.store.audit(_now(), operator, "annotate", f"{cr_id}: {cr['reason']}")
            merged = {**cr, "version": cr["version"] + 1, "status": "merged",
                      "decided_at": _now()}
            validate_object("change-request", merged)
            self.store.put("change-request", merged, created_at=_now())
            return merged

        plan = self.plan_service.get_plan(cr["plan_id"], _latest_before_merge(self, cr))
        plan = copy.deepcopy(plan)
        contract = self.store.get(
            "analysis-contract", plan["contract_ref"]["id"], plan["contract_ref"]["version"]
        )
        assert contract is not None
        contract = copy.deepcopy(contract)

        old_contract_version = contract["version"]
        contract_changed = False
        for change in cr["changes"]:
            path = change["path"]
            if path.startswith("contract."):
                _set_path(contract, path.removeprefix("contract."), change["new"])
                contract_changed = True
            elif path.startswith("plan."):
                _set_path(plan, path.removeprefix("plan."), change["new"])
            else:
                raise ValueError(f"不支持的变更路径: {path}")

        if contract_changed:
            contract["version"] = old_contract_version + 1
            validate_object("analysis-contract", contract)
            self.store.put("analysis-contract", contract, created_at=_now())
            plan["contract_ref"]["version"] = contract["version"]
            for step in plan["steps"]:
                if "params_ref" in step:
                    step["params_ref"] = step["params_ref"].replace(
                        f"@{old_contract_version}:", f"@{contract['version']}:"
                    )

        new_plan = self.plan_service.save_draft(plan, operator=operator, bump=True)
        merged = {
            **cr,
            "version": cr["version"] + 1,
            "status": "merged",
            "merged_plan_version": new_plan["plan_version"],
            "decided_at": _now(),
        }
        validate_object("change-request", merged)
        self.store.put("change-request", merged, created_at=_now())
        self.store.audit(_now(), operator, "merge_cr",
                         f"{cr_id} -> plan@{new_plan['plan_version']}")
        return merged


def _latest_before_merge(service: ChangeService, cr: dict[str, Any]) -> int:
    latest = service.store.latest_version("analysis-plan-ir", cr["plan_id"])
    if latest is None:
        raise KeyError(f"plan not found: {cr['plan_id']}")
    return latest
