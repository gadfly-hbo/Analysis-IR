"""计划服务：模板实例化草稿、G0 校验、状态轴与版本化（proposal 9.2 接口子集）。

G0 只检查计划本身的结构与引用；绑定确认（G1）在 S4 的 ApprovalService。
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from adapters.local_store.project_store import ProjectStore
from toolkit.contracts import validate_object
from toolkit.diff import DiffEntry, leaf_diff
from toolkit.validation import ValidationIssue, ValidationReport

_PLAN_KIND = "analysis-plan-ir"
_CONTRACT_KIND = "analysis-contract"
_TEMPLATE_KIND = "plan-template"

_STATUS_DRAFT = "DRAFT"
_STATUS_NEEDS_INPUT = "NEEDS_INPUT"
_STATUS_READY = "READY_FOR_REVIEW"


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _valid_period(value: Any) -> bool:
    """期间必须形如 {start, end} 的合法 ISO 日期且 start ≤ end（R2-2）。"""
    from datetime import date as _date

    if not isinstance(value, dict):
        return False
    try:
        start = _date.fromisoformat(str(value.get("start")))
        end = _date.fromisoformat(str(value.get("end")))
    except ValueError:
        return False
    return start <= end


@dataclass(frozen=True)
class DraftResult:
    contract: dict[str, Any]
    plan: dict[str, Any]
    unresolved: list[str]


class PlanService:
    def __init__(self, store: ProjectStore, builtin_dir: Path | None = None) -> None:
        self.store = store
        self._builtin_dir = builtin_dir or (
            Path(__file__).resolve().parents[1] / "methods"
        )
        self._templates: dict[str, dict[str, Any]] = {}
        self._load_builtin_templates()

    # ---------- 模板 ----------

    def _load_builtin_templates(self) -> None:
        for path in sorted((self._builtin_dir / "templates").glob("*.json")):
            template = json.loads(path.read_text(encoding="utf-8"))
            validate_object(_TEMPLATE_KIND, template)
            key = template["template_id"].removeprefix("template-")
            self._templates[key] = template

    def register_builtin_registries(self) -> None:
        """把产品内置的方法/检查注册表写入项目存储（版本锁定，G12）。"""
        for filename, kind in (
            ("sales-methods-1.0.0.json", "method-registry"),
            ("sales-checks-1.0.0.json", "check-registry"),
        ):
            registry = json.loads(
                (self._builtin_dir / "registries" / filename).read_text(encoding="utf-8")
            )
            stored = {
                "id": registry["id"],
                "version": registry["revision"],
                "semver": registry["semver"],
                "entries": registry.get("methods") or registry.get("checks"),
            }
            self.store.put(kind, stored, created_at=_now())

    def _get_registry(self, kind: str, registry_id: str, semver: str) -> dict[str, Any] | None:
        latest = self.store.latest_version(kind, registry_id)
        while latest and latest >= 1:
            doc = self.store.get(kind, registry_id, latest)
            if doc and doc.get("semver") == semver:
                return doc
            latest -= 1
        return None

    # ---------- 草稿创建 ----------

    def create_draft(
        self, template_name: str, params: dict[str, Any], operator: str
    ) -> DraftResult:
        if template_name not in self._templates:
            raise KeyError(f"no-such-template: {template_name}")
        return self.instantiate_template(self._templates[template_name], params, operator)

    def instantiate_template(
        self, template: dict[str, Any], params: dict[str, Any], operator: str
    ) -> DraftResult:
        """从任意模板（内置或用户保存）实例化新计划草稿（G3 决议：新 plan_id）。"""
        validate_object(_TEMPLATE_KIND, template)
        template_name = template["template_id"].removeprefix("template-")

        slug = f"{template_name}-{datetime.now(UTC):%Y%m%d}-{uuid.uuid4().hex[:4]}"
        unresolved: list[str] = []
        for slot in template["param_slots"]:
            if slot.get("required", True) and slot["name"] not in params:
                unresolved.append(f"param {slot['name']} 未确认")

        contract = self._build_contract(template, slug, params, unresolved)
        plan = self._build_plan(template, slug, contract)

        now = _now()
        self.store.put(_CONTRACT_KIND, contract, created_at=now)
        self.store.put(_PLAN_KIND, plan, created_at=now)
        self.store.set_status(plan["plan_id"], plan["plan_version"], _STATUS_DRAFT, now)
        self.store.audit(now, operator, "instantiate_template",
                         f"{plan['plan_id']}@{plan['plan_version']}")
        return DraftResult(contract=contract, plan=plan, unresolved=unresolved)

    def _build_contract(
        self,
        template: dict[str, Any],
        slug: str,
        params: dict[str, Any],
        unresolved: list[str],
    ) -> dict[str, Any]:
        scope: dict[str, Any] = {"store_eligibility": {"mode": "all-stores"}}
        for period in ("base_period", "report_period"):
            if period in params:
                value = params[period]
                if not _valid_period(value):
                    raise ValueError(
                        f"{period} 非法（需 {{start, end}} 且为合法日期，start ≤ end）: {value}"
                    )
                scope[period] = value
        return {
            "schema_version": "1.0.0",
            "id": f"contract-{slug}",
            "version": 1,
            "decision_purpose": params.get("decision_purpose", ""),
            "question": params.get("question", ""),
            "non_goals": [
                "不自动输出因果结论或经营动作建议",
                "不把销售分解结果直接等同于降价依据",
            ],
            "success_criteria": [
                "整体与分组变化额可对账回整体",
                "每条数值发现可追溯至本次运行证据",
            ],
            "comparison_scope": scope,
            "open_questions": [
                {"topic": u, "status": "pending"} for u in unresolved
            ],
        }

    def _build_plan(
        self, template: dict[str, Any], slug: str, contract: dict[str, Any]
    ) -> dict[str, Any]:
        dataset_id = f"dataset-{slug}"
        binding_id = f"binding-{slug}"
        steps = []
        for tstep in template["steps"]:
            step: dict[str, Any] = {
                "step_id": tstep["step_id"],
                "method_ref": tstep["method_ref"],
                "depends_on": list(tstep["depends_on"]),
                "inputs": (
                    [f"{dataset_id}@1"]
                    if tstep["step_id"] in ("quality", "scope")
                    else ["scoped-sales"]
                ),
                "outputs": list(tstep["outputs"]),
            }
            if tstep.get("param_slot") == "comparison-scope":
                step["params_ref"] = f"{contract['id']}@{contract['version']}:comparison-scope"
            elif tstep.get("param_slot") == "dimension-store":
                step["params"] = {"dimension": "store_id"}
            elif tstep.get("param_slot") == "dimension-category":
                step["params"] = {"dimension": "category_id"}
            if tstep["step_id"] == "validate":
                step["inputs"] = ["overall-delta", "store-delta", "category-delta"]
            steps.append(step)

        outputs = set(template.get(
            "required_artifacts",
            {name for s in steps for name in s["outputs"]},
        ))
        return {
            "schema_version": "1.0.0",
            "plan_id": slug,
            "plan_version": 1,
            "contract_ref": {"id": contract["id"], "version": contract["version"]},
            "binding_ref": {"id": binding_id, "version": 1},
            "dataset_refs": [{"id": dataset_id, "version": 1}],
            "metric_refs": [{"id": "net-sales", "version": 1}],
            "method_registry_ref": {"id": "sales-methods", "version": "1.0.0"},
            "check_registry_ref": {"id": "sales-checks", "version": "1.0.0"},
            "execution_policy_ref": {"id": "local-fixed-methods", "version": 1},
            "steps": steps,
            "required_check_refs": list(template["required_check_refs"]),
            "output_contract": {
                "required_artifacts": sorted(outputs),
                "allowed_finding_types": ["descriptive", "arithmetic-decomposition"],
                "require_evidence_refs": True,
                "require_limitations": True,
            },
        }

    # ---------- 读取与保存 ----------

    def get_plan(self, plan_id: str, version: int) -> dict[str, Any]:
        doc = self.store.get(_PLAN_KIND, plan_id, version)
        if doc is None:
            raise KeyError(f"plan not found: {plan_id}@{version}")
        return doc

    def get_contract(self, contract_id: str, version: int) -> dict[str, Any]:
        doc = self.store.get(_CONTRACT_KIND, contract_id, version)
        if doc is None:
            raise KeyError(f"contract not found: {contract_id}@{version}")
        return doc

    def save_draft(
        self, plan: dict[str, Any], operator: str, bump: bool = False
    ) -> dict[str, Any]:
        """保存草稿。bump=True 时生成新版本（proposal 8.3：不原地覆盖）。"""
        validate_object(_PLAN_KIND, plan)
        if bump:
            latest = self.store.latest_version(_PLAN_KIND, plan["plan_id"]) or plan["plan_version"]
            plan = {**plan, "plan_version": latest + 1}
        now = _now()
        self.store.put(_PLAN_KIND, plan, created_at=now)
        self.store.set_status(plan["plan_id"], plan["plan_version"], _STATUS_DRAFT, now)
        self.store.audit(now, operator, "save_draft", f"{plan['plan_id']}@{plan['plan_version']}")
        return plan

    def get_status(self, plan_id: str, version: int) -> str | None:
        return self.store.get_status(plan_id, version)

    def diff(self, plan_id: str, version_a: int, version_b: int) -> list[DiffEntry]:
        """两个明确版本之间的语义级差异（proposal F06）。"""
        plan_a = self.get_plan(plan_id, version_a)
        plan_b = self.get_plan(plan_id, version_b)

        def contract_for(plan: dict[str, Any]) -> dict[str, Any]:
            ref = plan["contract_ref"]
            doc = self.store.get("analysis-contract", ref["id"], ref["version"])
            return doc if doc is not None else {}

        return leaf_diff(
            {"plan": plan_a, "contract": contract_for(plan_a)},
            {"plan": plan_b, "contract": contract_for(plan_b)},
            "",
        )

    # ---------- G0 校验 ----------

    def validate(self, plan_id: str, version: int) -> ValidationReport:
        plan = self.get_plan(plan_id, version)
        issues: list[ValidationIssue] = []

        steps = plan["steps"]
        step_ids = [s["step_id"] for s in steps]
        seen: set[str] = set()
        for idx, sid in enumerate(step_ids):
            if sid in seen:
                issues.append(
                    ValidationIssue("DUPLICATE_STEP_ID", f"steps[{idx}]", f"step_id 重复: {sid}")
                )
            seen.add(sid)

        for idx, step in enumerate(steps):
            for dep in step["depends_on"]:
                if dep not in step_ids:
                    issues.append(
                        ValidationIssue(
                            "UNKNOWN_DEPENDENCY", f"steps[{idx}].depends_on", f"未知依赖: {dep}"
                        )
                    )
        issues.extend(self._cycle_issues(steps))

        method_registry = self._require_registry(
            issues, plan, "method_registry_ref", "method-registry"
        )
        check_registry = self._require_registry(
            issues, plan, "check_registry_ref", "check-registry"
        )
        if method_registry is not None:
            known = {f"{e['name']}@{e['version']}" for e in method_registry["entries"]}
            for idx, step in enumerate(steps):
                method = step["method_ref"].split(":", 1)[1]
                if method not in known:
                    issues.append(
                        ValidationIssue(
                            "UNSUPPORTED_METHOD",
                            f"steps[{idx}].method_ref",
                            f"方法不在注册表内: {step['method_ref']}",
                        )
                    )
        if check_registry is not None:
            known = {f"{e['name']}@{e['version']}" for e in check_registry["entries"]}
            for idx, ref in enumerate(plan["required_check_refs"]):
                check = ref.split(":", 1)[1]
                if check not in known:
                    issues.append(
                        ValidationIssue(
                            "UNKNOWN_REQUIRED_CHECK",
                            f"required_check_refs[{idx}]",
                            f"必需检查不在注册表内: {ref}",
                        )
                    )

        issues.extend(self._scope_issues(plan))
        issues.extend(self._reference_issues(plan))
        issues.extend(self._artifact_issues(plan))

        report = ValidationReport(issues=issues)
        now = _now()
        new_status = _STATUS_READY if report.g0_passed else _STATUS_NEEDS_INPUT
        self.store.set_status(plan["plan_id"], plan["plan_version"], new_status, now)
        return report

    def _require_registry(
        self, issues: list[ValidationIssue], plan: dict[str, Any], key: str, kind: str
    ) -> dict[str, Any] | None:
        ref = plan[key]
        registry = self._get_registry(kind, ref["id"], str(ref["version"]))
        if registry is None:
            issues.append(
                ValidationIssue(
                    "MISSING_REFERENCE",
                    key,
                    f"注册表不存在: {ref['id']}@{ref['version']}",
                )
            )
        return registry

    @staticmethod
    def _cycle_issues(steps: list[dict[str, Any]]) -> list[ValidationIssue]:
        graph = {s["step_id"]: list(s["depends_on"]) for s in steps}
        WHITE, GRAY, BLACK = 0, 1, 2
        color = dict.fromkeys(graph, WHITE)
        found: list[ValidationIssue] = []

        def visit(node: str, path: list[str]) -> None:
            color[node] = GRAY
            for dep in graph.get(node, []):
                if color.get(dep) == GRAY:
                    found.append(
                        ValidationIssue(
                            "CYCLIC_DEPENDENCY",
                            f"steps/{dep}.depends_on",
                            f"依赖成环: {' -> '.join(path + [node, dep])}",
                        )
                    )
                elif color.get(dep) == WHITE and dep in graph:
                    visit(dep, path + [node])
            color[node] = BLACK

        for node in graph:
            if color[node] == WHITE:
                visit(node, [])
        return found

    def _scope_issues(self, plan: dict[str, Any]) -> list[ValidationIssue]:
        """T03：比较范围（两期）必须显式声明，缺期间不可确认。"""
        contract = self.store.get(
            "analysis-contract", plan["contract_ref"]["id"], plan["contract_ref"]["version"]
        )
        if contract is None:
            return []  # 引用缺失已由 MISSING_REFERENCE 报告
        issues: list[ValidationIssue] = []
        scope = contract.get("comparison_scope", {})
        for period in ("base_period", "report_period"):
            value = scope.get(period)
            if not isinstance(value, dict) or not value.get("start") or not value.get("end"):
                issues.append(
                    ValidationIssue(
                        "MISSING_COMPARISON_SCOPE",
                        f"contract.comparison_scope.{period}",
                        f"比较范围未说明: {period}（T03：未确认不可执行）",
                    )
                )
            elif not _valid_period(value):
                issues.append(
                    ValidationIssue(
                        "INVALID_COMPARISON_SCOPE",
                        f"contract.comparison_scope.{period}",
                        f"比较范围非法（日期格式或顺序错误）: {value}（proposal 7.2）",
                    )
                )
        return issues

    def _reference_issues(self, plan: dict[str, Any]) -> list[ValidationIssue]:
        issues: list[ValidationIssue] = []

        def check(kind: str, ref: dict[str, Any], location: str) -> None:
            if not self.store.exists(kind, ref["id"], ref["version"]):
                issues.append(
                    ValidationIssue(
                        "MISSING_REFERENCE",
                        location,
                        f"引用对象不存在: {kind} {ref['id']}@{ref['version']}",
                    )
                )

        check(_CONTRACT_KIND, plan["contract_ref"], "contract_ref")
        check("binding-snapshot", plan["binding_ref"], "binding_ref")
        for idx, ref in enumerate(plan["dataset_refs"]):
            check("data-snapshot-manifest", ref, f"dataset_refs[{idx}]")
        for idx, ref in enumerate(plan["metric_refs"]):
            check("metric-contract", ref, f"metric_refs[{idx}]")
        return issues

    @staticmethod
    def _artifact_issues(plan: dict[str, Any]) -> list[ValidationIssue]:
        produced = {name for s in plan["steps"] for name in s["outputs"]}
        issues = []
        for idx, artifact in enumerate(plan["output_contract"]["required_artifacts"]):
            if artifact not in produced:
                issues.append(
                    ValidationIssue(
                        "ORPHAN_ARTIFACT",
                        f"output_contract.required_artifacts[{idx}]",
                        f"输出契约要求的产物无步骤生产: {artifact}",
                    )
                )
        return issues
