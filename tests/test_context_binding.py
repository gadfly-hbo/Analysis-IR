"""S3 数据快照与语义绑定测试（接缝 S-A：LocalContext 公共接口）。

覆盖：快照冻结与指纹（T11）、字段缺失定位（T04）、混合币种阻断（T09）、
同店资格（T08）、口径未确认记录（T03）、覆盖缺口警告（T07 前段）。
"""

import shutil
from pathlib import Path

import pytest

from adapters.local_context.context_port import BindingError, LocalContext
from adapters.local_store.project_store import ProjectStore
from toolkit.plan_service import PlanService

GOLDEN_CSV = Path(__file__).parent / "golden" / "sales-golden.csv"

_FULL_PARAMS = {
    "question": "最近门店销售下降，分析原因",
    "decision_purpose": "品类策略评估",
    "base_period": {"start": "2026-07-01", "end": "2026-07-31"},
    "report_period": {"start": "2026-08-01", "end": "2026-08-31"},
}

_CONFIRMED_TREATMENTS = {
    "tax": "net-of-tax",
    "discounts": "after-discount",
    "returns_attribution": "transaction-period",
}

_DEFAULT_FIELD_MAP = {
    "row_id": "row_id",
    "business_date": "business_date",
    "store_id": "store_id",
    "category_id": "category_id",
    "net_sales_amount": "net_sales_amount",
    "currency": "currency",
}


@pytest.fixture
def service(tmp_path: Path) -> PlanService:
    svc = PlanService(ProjectStore(tmp_path / "project.db"))
    svc.register_builtin_registries()
    return svc


@pytest.fixture
def draft(service: PlanService) -> dict:
    return service.create_draft("sales-delta", _FULL_PARAMS, operator="analyst-a").plan


@pytest.fixture
def context(tmp_path: Path) -> LocalContext:
    return LocalContext(project_dir=tmp_path / "project-files")


def _bind(context: LocalContext, service: PlanService, draft: dict, **overrides: object) -> object:
    kwargs: dict = {
        "plan": draft,
        "source_file": GOLDEN_CSV,
        "field_map": dict(_DEFAULT_FIELD_MAP),
        "metric_treatments": dict(_CONFIRMED_TREATMENTS),
        "coverage": {"kind": "declared-complete", "calendar_days_expected": 62},
        "operator": "analyst-a",
        "store": service.store,
    }
    kwargs.update(overrides)
    return context.bind(**kwargs)


class TestBindHappyPath:
    def test_bind_creates_manifest_and_binding(
        self,
        context: LocalContext,
        service: PlanService,
        draft: dict,
    ) -> None:
        result = _bind(context, service, draft)
        manifest = result.manifest
        binding = result.binding
        assert manifest["row_count"] == 18
        assert len(manifest["byte_digest"]) == 64
        assert binding["field_map"]["store_id"] == "store_id"
        assert result.warnings == []
        # 计划引用的对象现在都存在 → G0 通过
        report = service.validate(draft["plan_id"], draft["plan_version"])
        assert report.g0_passed, [i.code for i in report.issues]

    def test_snapshot_file_is_frozen_copy(
        self,
        context: LocalContext,
        service: PlanService,
        draft: dict,
        tmp_path: Path,
    ) -> None:
        result = _bind(context, service, draft)
        # T11 后半：只改原文件不应污染已冻结快照
        original = tmp_path / "original-source.csv"
        shutil.copy(GOLDEN_CSV, original)
        original.write_text(original.read_text() + "R999,2026-09-01,S01,C01,1,CNY\n")
        assert context.verify_snapshot(result.manifest) is True


class TestBindFailures:
    def test_missing_required_column_located(
        self,
        context: LocalContext,
        service: PlanService,
        draft: dict,
        tmp_path: Path,
    ) -> None:
        # T04：移除 store_id 列
        broken = tmp_path / "no-store.csv"
        lines = GOLDEN_CSV.read_text().splitlines()
        header = [c for c in lines[0].split(",") if c != "store_id"]
        body = [
            ",".join([v for i, v in enumerate(line.split(",")) if i != 2])
            for line in lines[1:]
        ]
        broken.write_text("\n".join([",".join(header)] + body) + "\n")
        with pytest.raises(BindingError) as exc_info:
            _bind(context, service, draft, source_file=broken)
        assert exc_info.value.code == "MISSING_FIELD"
        assert "store_id" in exc_info.value.location

    def test_mixed_currency_blocked(
        self,
        context: LocalContext,
        service: PlanService,
        draft: dict,
        tmp_path: Path,
    ) -> None:
        # T09：混合货币且无换算契约 → 阻断
        mixed = tmp_path / "mixed.csv"
        text = GOLDEN_CSV.read_text()
        text = text.replace(
            "R002,2026-07-15,S02,C01,200000,CNY",
            "R002,2026-07-15,S02,C01,200000,USD",
        )
        mixed.write_text(text)
        with pytest.raises(BindingError) as exc_info:
            _bind(context, service, draft, source_file=mixed)
        assert exc_info.value.code == "MIXED_CURRENCY"

    def test_unparseable_date_blocked(
        self,
        context: LocalContext,
        service: PlanService,
        draft: dict,
        tmp_path: Path,
    ) -> None:
        broken = tmp_path / "bad-date.csv"
        text = GOLDEN_CSV.read_text().replace("R003,2026-07-25", "R003,2026/07/25")
        broken.write_text(text)
        with pytest.raises(BindingError) as exc_info:
            _bind(context, service, draft, source_file=broken)
        assert exc_info.value.code == "DATE_PARSE"


class TestScopeEligibility:
    def test_no_eligibility_list_means_full_range_label(
        self,
        context: LocalContext,
        service: PlanService,
        draft: dict,
    ) -> None:
        # T08：无门店资格清单 → 全量观察范围，不得称同店
        result = _bind(context, service, draft)
        assert result.binding["scope"]["mode"] == "all-stores"
        assert result.binding["scope"]["label"] == "全量观察范围"
        assert result.binding["scope"].get("same_store") is not True

    def test_declared_list_sets_same_store_scope(
        self,
        context: LocalContext,
        service: PlanService,
        draft: dict,
        tmp_path: Path,
    ) -> None:
        list_file = tmp_path / "eligible-stores.csv"
        list_file.write_text("store_id\nS01\nS02\n")
        result = _bind(
            context, service, draft,
            store_eligibility={"list_file": list_file, "rule_note": "两期均完整营业"},
        )
        assert result.binding["scope"]["mode"] == "declared-list"
        assert result.binding["scope"]["same_store"] is True
        assert result.binding["scope"]["eligible_stores"] == ["S01", "S02"]


class TestOpenItems:
    def test_unconfirmed_treatments_recorded_as_warnings(
        self,
        context: LocalContext,
        service: PlanService,
        draft: dict,
    ) -> None:
        # T03 前段：不说明退款口径 → 未确认记录，不静默定值
        result = _bind(
            context, service, draft,
            metric_treatments={"tax": "net-of-tax", "discounts": "after-discount"},
        )
        assert any("returns_attribution" in w for w in result.warnings)
        metric = service.store.get("metric-contract", "net-sales", 1)
        assert metric["treatments"]["returns_attribution"] == "unconfirmed"

    def test_unverified_coverage_produces_warning(
        self,
        context: LocalContext,
        service: PlanService,
        draft: dict,
    ) -> None:
        # T07 前段：无覆盖证明 → 警告待确认，不得默认完整
        result = _bind(context, service, draft, coverage={"kind": "unverified"})
        assert any("coverage" in w for w in result.warnings)
        assert result.manifest["coverage"]["kind"] == "unverified"


class TestSnapshotIntegrity:
    def test_tampered_snapshot_fails_verification(
        self,
        context: LocalContext,
        service: PlanService,
        draft: dict,
    ) -> None:
        # T11 前段：修改实际绑定的项目快照 → 指纹不符
        result = _bind(context, service, draft)
        snapshot_path = Path(result.manifest["snapshot_path"])
        snapshot_path.write_text(snapshot_path.read_text() + "R998,2026-09-02,S01,C02,5,CNY\n")
        assert context.verify_snapshot(result.manifest) is False
