"""S2 计划服务测试（接缝 S-A：进程内服务接口）。

覆盖：模板实例化草稿、G0 校验规则、状态轴流转、版本化历史不覆盖。
"""

import json
from pathlib import Path

import pytest

from adapters.local_store.project_store import ProjectStore
from toolkit.plan_service import PlanService

BUILTIN_DIR = Path(__file__).parents[1] / "methods"

_NOW = "2026-09-30T10:00:00+00:00"


def _register_stubs(service: PlanService, plan: dict) -> None:
    """注入 S3 将正式产生的引用对象（G0 只查存在性，内容校验属 S3/S4）。"""
    binding_id = plan["binding_ref"]["id"]
    dataset_id = plan["dataset_refs"][0]["id"]
    service.store.put(
        "binding-snapshot",
        {
            "schema_version": "1.0.0",
            "id": binding_id,
            "version": 1,
            "dataset_ref": {"id": dataset_id, "version": 1},
            "metric_refs": [{"id": "net-sales", "version": 1}],
            "field_map": {
                "row_id": "row_id",
                "business_date": "business_date",
                "store_id": "store_id",
                "category_id": "category_id",
                "net_sales_amount": "net_sales_amount",
            },
            "dimensions": [
                {"name": "store_id", "source_field": "store_id"},
                {"name": "category_id", "source_field": "category_id"},
            ],
        },
        created_at=_NOW,
    )
    service.store.put(
        "data-snapshot-manifest",
        {
            "schema_version": "1.0.0",
            "id": dataset_id,
            "version": 1,
            "source_file": "sales.csv",
            "byte_digest": "a" * 64,
            "row_count": 18,
            "parse_config": {"delimiter": ",", "encoding": "utf-8", "date_format": "%Y-%m-%d"},
            "coverage": {"kind": "declared-complete", "calendar_days_expected": 62},
            "snapshot_date": "2026-09-01",
        },
        created_at=_NOW,
    )
    service.store.put(
        "metric-contract",
        {
            "schema_version": "1.0.0",
            "id": "net-sales",
            "version": 1,
            "name": "净销售额",
            "formula": "sum(net_sales_amount)",
            "unit": "currency",
            "grain": "row",
            "treatments": {
                "tax": "net-of-tax",
                "discounts": "after-discount",
                "returns_attribution": "transaction-period",
            },
            "currency": "CNY",
        },
        created_at=_NOW,
    )


@pytest.fixture
def service(tmp_path: Path) -> PlanService:
    store = ProjectStore(tmp_path / "project.db")
    return PlanService(store)


def _full_params() -> dict:
    return {
        "question": "最近门店销售下降，分析原因",
        "decision_purpose": "判断是否需要调整品类策略（不自动决策）",
        "base_period": {"start": "2026-07-01", "end": "2026-07-31"},
        "report_period": {"start": "2026-08-01", "end": "2026-08-31"},
    }


class TestCreateDraft:
    def test_template_instantiation_creates_contract_and_plan(self, service: PlanService) -> None:
        result = service.create_draft("sales-delta", _full_params(), operator="analyst-a")
        assert result.unresolved == []
        contract = result.contract
        plan = result.plan
        assert contract["comparison_scope"]["base_period"]["start"] == "2026-07-01"
        assert plan["schema_version"] == "1.0.0"
        assert plan["plan_version"] == 1
        assert len(plan["steps"]) == 6

    def test_draft_persisted_and_reloadable(self, service: PlanService) -> None:
        result = service.create_draft("sales-delta", _full_params(), operator="analyst-a")
        loaded = service.get_plan(result.plan["plan_id"], 1)
        assert loaded == result.plan

    def test_missing_required_param_becomes_unresolved_not_default(
        self, service: PlanService
    ) -> None:
        # proposal 5.2：没有确认来源的内容进入"待确认"，不得静默补默认值
        params = _full_params()
        del params["report_period"]
        result = service.create_draft("sales-delta", params, operator="analyst-a")
        assert any("report_period" in u for u in result.unresolved)
        contract = service.get_contract(result.contract["id"], result.contract["version"])
        assert contract["open_questions"], "未解决项应记录在 open_questions"

    def test_unknown_template_rejected(self, service: PlanService) -> None:
        with pytest.raises(KeyError, match="no-such-template"):
            service.create_draft("no-such-template", _full_params(), operator="analyst-a")


class TestValidate:
    def _ready_service(self, tmp_path: Path) -> PlanService:
        """带全部引用对象（契约/注册表）的服务，用于构造可通过 G0 的计划。"""
        svc = PlanService(ProjectStore(tmp_path / "project.db"))
        svc.register_builtin_registries()
        svc.create_draft("sales-delta", _full_params(), operator="analyst-a")
        return svc

    def test_complete_plan_passes_g0(self, tmp_path: Path) -> None:
        svc = self._ready_service(tmp_path)
        result = svc.create_draft("sales-delta", _full_params(), operator="analyst-a")
        report = svc.validate(result.plan["plan_id"], 1)
        # 引用的 binding/dataset/metric 对象尚不存在 → 这些是合法的 NEEDS_INPUT 项
        codes = {i.code for i in report.issues}
        assert "MISSING_REFERENCE" in codes
        assert report.g0_passed is False

    def test_all_refs_present_plan_ready_for_review(self, tmp_path: Path) -> None:
        svc = self._ready_service(tmp_path)
        result = svc.create_draft("sales-delta", _full_params(), operator="analyst-a")
        _register_stubs(svc, result.plan)
        report = svc.validate(result.plan["plan_id"], 1)
        assert report.issues == []
        assert report.g0_passed is True
        assert svc.get_status(result.plan["plan_id"], 1) == "READY_FOR_REVIEW"

    def test_duplicate_step_id_reported_with_location(self, tmp_path: Path) -> None:
        svc = self._ready_service(tmp_path)
        result = svc.create_draft("sales-delta", _full_params(), operator="analyst-a")
        plan = json.loads(json.dumps(result.plan))
        plan["steps"][1]["step_id"] = plan["steps"][0]["step_id"]
        svc.save_draft(plan, operator="analyst-a", bump=True)
        report = svc.validate(plan["plan_id"], 2)
        assert any(i.code == "DUPLICATE_STEP_ID" for i in report.issues)

    def test_cyclic_dependency_reported(self, tmp_path: Path) -> None:
        svc = self._ready_service(tmp_path)
        result = svc.create_draft("sales-delta", _full_params(), operator="analyst-a")
        plan = json.loads(json.dumps(result.plan))
        plan["steps"][0]["depends_on"] = ["validate"]
        svc.save_draft(plan, operator="analyst-a", bump=True)
        report = svc.validate(plan["plan_id"], 2)
        assert any(i.code == "CYCLIC_DEPENDENCY" for i in report.issues)

    def test_unknown_step_in_depends_on(self, tmp_path: Path) -> None:
        svc = self._ready_service(tmp_path)
        result = svc.create_draft("sales-delta", _full_params(), operator="analyst-a")
        plan = json.loads(json.dumps(result.plan))
        plan["steps"][0]["depends_on"] = ["nonexistent-step"]
        svc.save_draft(plan, operator="analyst-a", bump=True)
        report = svc.validate(plan["plan_id"], 2)
        assert any(i.code == "UNKNOWN_DEPENDENCY" for i in report.issues)

    def test_unsupported_method_reported(self, tmp_path: Path) -> None:
        # T22：未知方法 → UNSUPPORTED_METHOD，留在草稿但不可确认
        svc = self._ready_service(tmp_path)
        result = svc.create_draft("sales-delta", _full_params(), operator="analyst-a")
        plan = json.loads(json.dumps(result.plan))
        plan["steps"][0]["method_ref"] = "sales-methods:free-sql@1.0.0"
        svc.save_draft(plan, operator="analyst-a", bump=True)
        report = svc.validate(plan["plan_id"], 2)
        assert any(i.code == "UNSUPPORTED_METHOD" for i in report.issues)

    def test_output_artifact_without_producer_reported(self, tmp_path: Path) -> None:
        svc = self._ready_service(tmp_path)
        result = svc.create_draft("sales-delta", _full_params(), operator="analyst-a")
        plan = json.loads(json.dumps(result.plan))
        plan["output_contract"]["required_artifacts"].append("phantom-table")
        svc.save_draft(plan, operator="analyst-a", bump=True)
        report = svc.validate(plan["plan_id"], 2)
        assert any(i.code == "ORPHAN_ARTIFACT" for i in report.issues)

    def test_status_flows_to_needs_input_when_issues(self, tmp_path: Path) -> None:
        svc = self._ready_service(tmp_path)
        result = svc.create_draft("sales-delta", _full_params(), operator="analyst-a")
        svc.validate(result.plan["plan_id"], 1)
        assert svc.get_status(result.plan["plan_id"], 1) == "NEEDS_INPUT"


class TestVersioning:
    def test_saving_modified_plan_creates_new_version(self, service: PlanService) -> None:
        result = service.create_draft("sales-delta", _full_params(), operator="analyst-a")
        plan = json.loads(json.dumps(result.plan))
        new = service.save_draft(plan, operator="analyst-a", bump=True)
        assert new["plan_version"] == 2
        assert service.get_plan(plan["plan_id"], 1) == result.plan  # v1 未被覆盖
        assert service.get_plan(plan["plan_id"], 2) == new

    def test_template_instantiation_new_plan_id(self, tmp_path: Path) -> None:
        svc = PlanService(ProjectStore(tmp_path / "project.db"))
        r1 = svc.create_draft("sales-delta", _full_params(), operator="analyst-a")
        r2 = svc.create_draft("sales-delta", _full_params(), operator="analyst-a")
        assert r1.plan["plan_id"] != r2.plan["plan_id"]  # G3 决议：实例化 → 新 plan_id
