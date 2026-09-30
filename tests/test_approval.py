"""S4 确认与版本治理测试（接缝 S-A）。

覆盖：approval_target 六类 digest（T10 旧确认不授权新内容）、
口径未确认阻断（T03）、快照调包阻断（T11）、语义 diff、
ChangeRequest 合入、外观变更注释化。
"""

import json
from pathlib import Path

import pytest

from adapters.local_context.context_port import LocalContext
from adapters.local_store.project_store import ProjectStore
from toolkit.approval_service import ApprovalRejected, ApprovalService
from toolkit.change_service import ChangeService
from toolkit.execution import compile_execution_manifest
from toolkit.plan_service import PlanService

GOLDEN_CSV = Path(__file__).parent / "golden" / "sales-golden.csv"

_PARAMS = {
    "question": "最近门店销售下降，分析原因",
    "decision_purpose": "品类策略评估",
    "base_period": {"start": "2026-07-01", "end": "2026-07-31"},
    "report_period": {"start": "2026-08-01", "end": "2026-08-31"},
}
_TREATMENTS = {
    "tax": "net-of-tax",
    "discounts": "after-discount",
    "returns_attribution": "transaction-period",
}
_FIELD_MAP = {
    "row_id": "row_id",
    "business_date": "business_date",
    "store_id": "store_id",
    "category_id": "category_id",
    "net_sales_amount": "net_sales_amount",
    "currency": "currency",
}


class TestEnv:
    """组装一个走到 READY_FOR_REVIEW 的完整环境。"""

    def __init__(self, tmp_path: Path, treatments: dict | None = None,
                 coverage: dict | None = None) -> None:
        self.tmp = tmp_path
        self.store = ProjectStore(tmp_path / "project.db")
        self.plan_service = PlanService(self.store)
        self.plan_service.register_builtin_registries()
        self.draft = self.plan_service.create_draft(
            "sales-delta", _PARAMS, operator="analyst-a"
        ).plan
        self.context = LocalContext(project_dir=tmp_path / "files")
        self.context.bind(
            plan=self.draft,
            source_file=GOLDEN_CSV,
            field_map=dict(_FIELD_MAP),
            metric_treatments=treatments or dict(_TREATMENTS),
            coverage=coverage or {"kind": "declared-complete", "calendar_days_expected": 62},
            operator="analyst-a",
            store=self.store,
        )
        report = self.plan_service.validate(self.draft["plan_id"], self.draft["plan_version"])
        assert report.g0_passed
        self.exec_manifest = compile_execution_manifest(self.store, self.draft)
        self.snapshot_manifest = self.store.get(
            "data-snapshot-manifest",
            self.draft["dataset_refs"][0]["id"],
            self.draft["dataset_refs"][0]["version"],
        )
        self.approval_service = ApprovalService(
            self.store, self.plan_service, self.context
        )

    def approve(self, **overrides: object) -> object:
        action: dict = {
            "operator": "analyst-a",
            "action": "approve",
            "origin": "test",
            "warnings_acknowledged": [],
        }
        action.update(overrides)
        return self.approval_service.approve(
            self.draft["plan_id"], self.draft["plan_version"], action
        )


class TestApproveHappyPath:
    def test_approval_contains_six_digests_and_marks_approved(self, tmp_path: Path) -> None:
        env = TestEnv(tmp_path)
        approval = env.approve()
        target = approval["approval_target"]
        assert set(target) == {
            "analysis_contract_digest", "analysis_plan_digest",
            "metric_and_binding_digest", "data_snapshot_manifest_digest",
            "method_and_check_registry_digest", "execution_manifest_digest",
        }
        for digest_value in target.values():
            assert len(digest_value) == 64
        assert approval["result"] == "valid"
        assert env.plan_service.get_status(env.draft["plan_id"], 1) == "APPROVED"
        assert env.approval_service.is_authorized(env.draft["plan_id"], 1) is not None


class TestApprovalBlockers:
    def test_unconfirmed_treatment_blocks_approval(self, tmp_path: Path) -> None:
        # T03：口径未说明 → 不可产生有效确认
        env = TestEnv(tmp_path, treatments={"tax": "net-of-tax"})
        with pytest.raises(ApprovalRejected, match="UNCONFIRMED_TREATMENT"):
            env.approve()

    def test_unverified_coverage_requires_acknowledgement(self, tmp_path: Path) -> None:
        env = TestEnv(tmp_path, coverage={"kind": "unverified"})
        with pytest.raises(ApprovalRejected, match="NEEDS_ACKNOWLEDGEMENT"):
            env.approve()
        approval = env.approve(
            warnings_acknowledged=[{"code": "coverage-unverified", "note": "知晓无覆盖证明"}]
        )
        assert approval["user_action"]["warnings_acknowledged"]

    def test_tampered_snapshot_blocks_approval(self, tmp_path: Path) -> None:
        # T11：修改实际绑定的快照 → 指纹不符，拒绝确认
        env = TestEnv(tmp_path)
        snapshot = Path(env.snapshot_manifest["snapshot_path"])
        snapshot.write_text(snapshot.read_text() + "R997,2026-09-03,S01,C03,9,CNY\n")
        with pytest.raises(ApprovalRejected, match="SNAPSHOT_MISMATCH"):
            env.approve()

    def test_service_refuses_action_without_operator(self, tmp_path: Path) -> None:
        # 服务自身永不生成确认（G2 决议）
        env = TestEnv(tmp_path)
        with pytest.raises(ValueError, match="operator"):
            env.approval_service.approve(
                env.draft["plan_id"], 1, {"action": "approve", "origin": "test"}
            )


class TestVersionGovernance:
    def test_old_approval_does_not_authorize_new_version(self, tmp_path: Path) -> None:
        # T10：确认后修改比较范围 → 新版本不能沿用旧确认
        env = TestEnv(tmp_path)
        env.approve()
        changes = ChangeService(env.store, env.plan_service).propose(
            plan_id=env.draft["plan_id"],
            plan_version=1,
            reason="比较范围调整：基期提前一周",
            proposed_by="user",
            changes=[{
                "path": "contract.comparison_scope.base_period.start",
                "old": "2026-07-01",
                "new": "2026-07-08",
                "impact": "scope",
            }],
        )
        merged = ChangeService(env.store, env.plan_service).merge(
            changes["cr_id"], operator="analyst-a"
        )
        new_version = merged["merged_plan_version"]
        assert new_version == 2
        assert env.approval_service.is_authorized(env.draft["plan_id"], 2) is None
        # proposal 8.3：旧版在「新版被确认后」才标记 SUPERSEDED；此时 v2 未确认，v1 仍 APPROVED
        assert env.plan_service.get_status(env.draft["plan_id"], 1) == "APPROVED"

    def test_reapproval_of_new_version_restores_authorization(self, tmp_path: Path) -> None:
        env = TestEnv(tmp_path)
        env.approve()
        cs = ChangeService(env.store, env.plan_service)
        cr = cs.propose(
            plan_id=env.draft["plan_id"], plan_version=1, reason="r", proposed_by="user",
            changes=[{"path": "contract.comparison_scope.base_period.start",
                      "old": "2026-07-01", "new": "2026-07-08", "impact": "scope"}],
        )
        cs.merge(cr["cr_id"], operator="analyst-a")
        # 重新编译 + 重新确认后恢复授权
        plan_v2 = env.plan_service.get_plan(env.draft["plan_id"], 2)
        assert env.plan_service.validate(plan_v2["plan_id"], 2).g0_passed
        compile_execution_manifest(env.store, plan_v2)
        env.approval_service.approve(
            env.draft["plan_id"], 2,
            {"operator": "analyst-a", "action": "approve", "origin": "test",
             "warnings_acknowledged": []},
        )
        assert env.approval_service.is_authorized(env.draft["plan_id"], 2) is not None
        assert env.plan_service.get_status(env.draft["plan_id"], 1) == "SUPERSEDED"

    def test_object_tampering_invalidates_authorization(self, tmp_path: Path) -> None:
        # 直接改库中对象内容 → digest 复算不匹配 → 不再授权
        env = TestEnv(tmp_path)
        env.approve()
        row = env.store._conn.execute(
            "SELECT doc FROM objects WHERE kind = 'metric-contract'"
        ).fetchone()
        doc = json.loads(row["doc"])
        doc["treatments"]["tax"] = "gross"
        env.store._conn.execute(
            "UPDATE objects SET doc = ? WHERE kind = 'metric-contract'",
            (json.dumps(doc, ensure_ascii=False),),
        )
        env.store._conn.commit()
        assert env.approval_service.is_authorized(env.draft["plan_id"], 1) is None


class TestSemanticDiff:
    def test_scope_change_classified_as_scope(self, tmp_path: Path) -> None:
        env = TestEnv(tmp_path)
        cs = ChangeService(env.store, env.plan_service)
        cr = cs.propose(
            plan_id=env.draft["plan_id"], plan_version=1, reason="r", proposed_by="user",
            changes=[{"path": "contract.comparison_scope.base_period.start",
                      "old": "2026-07-01", "new": "2026-07-08", "impact": "scope"}],
        )
        cs.merge(cr["cr_id"], operator="analyst-a")
        diffs = env.plan_service.diff(env.draft["plan_id"], 1, 2)
        entries = [d for d in diffs if "base_period.start" in d.path]
        assert entries and entries[0].old == "2026-07-01" and entries[0].new == "2026-07-08"
        assert entries[0].impact == "scope"

    def test_method_change_classified_as_method(self, tmp_path: Path) -> None:
        env = TestEnv(tmp_path)
        plan = json.loads(json.dumps(env.draft))
        plan["steps"][0]["method_ref"] = "sales-methods:quality@1.0.1"
        # 未注册的方法版本直接保存会被 schema 放过、被 G0 拦下；此处只测 diff 分类
        plan["method_registry_ref"] = {"id": "sales-methods", "version": "1.0.1"}
        env.plan_service.save_draft(plan, operator="analyst-a", bump=True)
        diffs = env.plan_service.diff(env.draft["plan_id"], 1, 2)
        method_entries = [d for d in diffs if d.impact == "method"]
        assert any(
            "method_ref" in d.path or "method_registry_ref" in d.path
            for d in method_entries
        )

    def test_cosmetic_change_stays_annotation_without_new_version(self, tmp_path: Path) -> None:
        # proposal 5.3：仅标题/备注 → 注释记录，不改执行对象、不产生新版本
        env = TestEnv(tmp_path)
        cs = ChangeService(env.store, env.plan_service)
        cr = cs.propose(
            plan_id=env.draft["plan_id"], plan_version=1, reason="笔误", proposed_by="user",
            changes=[{"path": "contract.decision_purpose", "old": "品类策略评估",
                      "new": "品类策略评估（草稿）", "impact": "cosmetic"}],
        )
        merged = cs.merge(cr["cr_id"], operator="analyst-a")
        assert "merged_plan_version" not in merged
        assert env.store.latest_version("analysis-plan-ir", env.draft["plan_id"]) == 1
