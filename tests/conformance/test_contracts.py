"""契约层测试：Schema 注册表加载、正例校验、负例拒绝（含 format 断言）。

proposal 7.2：Schema 合法 ≠ 分析正确——本层只测结构约束；
负例覆盖错误日期、未知枚举、额外字段、坏引用四类（proposal 明确要求真实测试）。
"""

import copy
import json
from pathlib import Path

import pytest

from toolkit.contracts import OBJECT_KINDS, ContractViolation, validate_object

GOLDEN_PLAN = Path(__file__).parents[1] / "golden" / "plan-sales-delta.json"


@pytest.fixture
def golden_plan() -> dict:
    return json.loads(GOLDEN_PLAN.read_text(encoding="utf-8"))


class TestSchemaRegistry:
    def test_all_twelve_object_kinds_have_schemas(self) -> None:
        assert set(OBJECT_KINDS) == {
            "analysis-contract", "metric-contract", "binding-snapshot",
            "data-snapshot-manifest", "analysis-plan-ir", "execution-manifest",
            "approval", "run-record", "evidence-bundle", "finding",
            "plan-template", "change-request",
        }

    def test_golden_plan_passes_schema(self, golden_plan: dict) -> None:
        validate_object("analysis-plan-ir", golden_plan)


class TestNegativeCases:
    def test_unknown_major_schema_version_rejected(self, golden_plan: dict) -> None:
        plan = copy.deepcopy(golden_plan)
        plan["schema_version"] = "2.0.0"
        with pytest.raises(ContractViolation, match="schema_version"):
            validate_object("analysis-plan-ir", plan)

    def test_extra_field_rejected(self, golden_plan: dict) -> None:
        plan = copy.deepcopy(golden_plan)
        plan["free_sql"] = "SELECT * FROM sales"
        with pytest.raises(ContractViolation):
            validate_object("analysis-plan-ir", plan)

    def test_bad_object_ref_rejected(self, golden_plan: dict) -> None:
        plan = copy.deepcopy(golden_plan)
        plan["metric_refs"] = [{"id": "net-sales"}]  # 缺 version
        with pytest.raises(ContractViolation):
            validate_object("analysis-plan-ir", plan)

    def test_latest_ref_rejected_by_pattern(self, golden_plan: dict) -> None:
        plan = copy.deepcopy(golden_plan)
        plan["steps"][0]["method_ref"] = "sales-methods:quality@latest"
        with pytest.raises(ContractViolation):
            validate_object("analysis-plan-ir", plan)

    def test_invalid_date_rejected_by_format_assertion(self) -> None:
        manifest = {
            "schema_version": "1.0.0",
            "id": "snap-1",
            "version": 1,
            "source_file": "sales.csv",
            "byte_digest": "a" * 64,
            "row_count": 18,
            "parse_config": {"delimiter": ",", "encoding": "utf-8", "date_format": "%Y-%m-%d"},
            "coverage": {"kind": "declared-complete", "calendar_days_expected": 62},
            "snapshot_date": "2026-13-45",
        }
        with pytest.raises(ContractViolation, match="snapshot_date"):
            validate_object("data-snapshot-manifest", manifest)

    def test_unknown_enum_rejected(self) -> None:
        record = {
            "schema_version": "1.0.0",
            "run_id": "run-0001",
            "plan_id": "sales-delta-demo",
            "plan_version": 1,
            "plan_digest": "b" * 64,
            "status": "SUCCEEDED",
            "steps": [],
            "started_at": "2026-09-30T10:00:00+08:00",
        }
        with pytest.raises(ContractViolation):
            validate_object("run-record", record)
