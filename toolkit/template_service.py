"""模板服务（proposal 9.2 TemplateService）：复制方法，不复制结论资格。

extract：从已审阅计划提取可复用方法（模板内不含旧批准、旧数据、旧结论）。
instantiate：参数化重建下一周期任务草稿（G3 决议：新 plan_id 从 v1 起）。
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from adapters.local_store.project_store import ProjectStore
from toolkit.contracts import validate_object
from toolkit.plan_service import PlanService

_FORBIDDEN_KEYS = ("run_id", "approval", "result", "byte_digest", "snapshot_path",
                   "plan_digest", "finding_id", "evidence_id")


def _now() -> str:
    return datetime.now(UTC).isoformat()


class TemplateError(Exception):
    pass


class TemplateService:
    def __init__(self, store: ProjectStore, plan_service: PlanService) -> None:
        self.store = store
        self.plan_service = plan_service

    def extract(
        self, plan_id: str, plan_version: int, name: str, operator: str
    ) -> dict[str, Any]:
        plan = self.plan_service.get_plan(plan_id, plan_version)
        contract = self.store.get(
            "analysis-contract", plan["contract_ref"]["id"], plan["contract_ref"]["version"]
        )
        if contract is None:
            raise TemplateError("计划引用的业务约定不存在")

        template = {
            "schema_version": "1.0.0",
            "template_id": f"template-{plan_id}",
            "version": 1,
            "name": name,
            "question_frame": contract["question"],
            "steps": [
                {
                    "step_id": s["step_id"],
                    "method_ref": s["method_ref"],
                    "depends_on": list(s["depends_on"]),
                    "outputs": list(s["outputs"]),
                    **self._param_slot_of(s),
                }
                for s in plan["steps"]
            ],
            "param_slots": [
                {"name": "question", "kind": "text", "required": True},
                {"name": "decision_purpose", "kind": "text", "required": True},
                {"name": "base_period", "kind": "period", "required": True},
                {"name": "report_period", "kind": "period", "required": True},
            ],
            "applicable_conditions": [
                {"condition": "单一事实表（行级销售记录）、单一币种", "boundary": "混合币种阻断"},
                {
                    "condition": "方法集与检查集版本: 见 required_check_refs",
                    "boundary": "版本不符阻断",
                },
            ],
            "required_artifacts": list(plan["output_contract"]["required_artifacts"]),
            "required_check_refs": list(plan["required_check_refs"]),
        }
        validate_object("plan-template", template)
        # T19 清除保证：模板结构本身不承载任何运行期字段
        self._assert_cleared(template)
        self.store.put("plan-template", template, created_at=_now())
        self.store.audit(_now(), operator, "template_extract", template["template_id"])
        return template

    @staticmethod
    def _param_slot_of(step: dict[str, Any]) -> dict[str, str]:
        if "params_ref" in step:
            return {"param_slot": "comparison-scope"}
        dimension = (step.get("params") or {}).get("dimension")
        if dimension == "store_id":
            return {"param_slot": "dimension-store"}
        if dimension == "category_id":
            return {"param_slot": "dimension-category"}
        return {}

    @staticmethod
    def _assert_cleared(template: dict[str, Any]) -> None:
        def walk(node: Any) -> None:
            if isinstance(node, dict):
                for key, value in node.items():
                    if key in _FORBIDDEN_KEYS:
                        raise TemplateError(f"模板携带不应保留的字段: {key}")
                    walk(value)
            elif isinstance(node, list):
                for item in node:
                    walk(item)

        walk(template)

    def instantiate(
        self, template_id: str, params: dict[str, Any], operator: str
    ) -> Any:
        latest = self.store.latest_version("plan-template", template_id)
        if latest is None:
            raise KeyError(f"template not found: {template_id}")
        template = self.store.get("plan-template", template_id, latest)
        assert template is not None
        return self.plan_service.instantiate_template(template, params, operator)
