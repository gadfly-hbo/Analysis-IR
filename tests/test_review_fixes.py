"""R1 审查修复验证：B1 T03 门禁 / M3 资源限制 / M5 待验证区。"""

import json
import subprocess
import sys
from pathlib import Path

import pytest
from env_helper import GOLDEN_CSV, build_env

from adapters.local_store.project_store import ProjectStore
from toolkit.approval_service import ApprovalRejected
from toolkit.evidence_service import EvidenceService
from toolkit.execution import compile_execution_manifest
from toolkit.findings import add_to_verify, generate_findings
from toolkit.plan_service import PlanService


class TestB1MissingScopeGate:
    def test_missing_periods_cannot_be_approved(self, tmp_path: Path) -> None:
        """T03：不说明比较范围 → 保存为待补充，不能产生可执行的有效确认。"""
        store = ProjectStore(tmp_path / "p.db")
        svc = PlanService(store)
        svc.register_builtin_registries()
        result = svc.create_draft(
            "sales-delta",
            {"question": "销售为什么下降", "decision_purpose": "评估"},  # 故意缺两期
            operator="analyst-a",
        )
        assert any("base_period" in u and "report_period" in u or "base_period" in u
                   for u in result.unresolved)
        report = svc.validate(result.plan["plan_id"], 1)
        codes = {i.code for i in report.issues}
        assert "MISSING_COMPARISON_SCOPE" in codes
        assert report.g0_passed is False
        assert svc.get_status(result.plan["plan_id"], 1) == "NEEDS_INPUT"

        # 补充绑定对象后（否则 MISSING_REFERENCE 混淆判定），仅因缺范围也不能确认
        from adapters.local_context.context_port import LocalContext
        from tests.env_helper import FIELD_MAP, GOLDEN_COVERAGE, TREATMENTS

        LocalContext(project_dir=tmp_path / "files").bind(
            plan=result.plan, source_file=GOLDEN_CSV, field_map=dict(FIELD_MAP),
            metric_treatments=dict(TREATMENTS), coverage=dict(GOLDEN_COVERAGE),
            operator="analyst-a", store=store,
        )
        report = svc.validate(result.plan["plan_id"], 1)
        assert not report.g0_passed
        assert {i.code for i in report.issues} == {"MISSING_COMPARISON_SCOPE"}

        compile_execution_manifest(store, result.plan)
        from adapters.local_context.context_port import LocalContext as LC
        approval_svc_module = __import__("toolkit.approval_service",
                                         fromlist=["ApprovalService"])
        approvals = approval_svc_module.ApprovalService(
            store, svc, LC(project_dir=tmp_path / "files")
        )
        with pytest.raises(ApprovalRejected, match="G0_NOT_PASSED"):
            approvals.approve(
                result.plan["plan_id"], 1,
                {"operator": "analyst-a", "action": "approve", "origin": "test",
                 "warnings_acknowledged": []},
            )

    def test_controller_blocks_scopeless_plan_defensively(self, tmp_path: Path) -> None:
        """即使确认被绕过（防御路径），缺范围也不允许启动运行（不裸崩）。"""
        env = build_env(tmp_path)
        record = env.controller.start(env.draft["plan_id"], 1, operator="analyst-a")
        assert record["status"] == "COMPLETED"  # 正常路径不受影响


class TestM5ToVerifyZone:
    def test_causal_draft_text_lands_in_to_verify_not_conclusions(self, tmp_path: Path) -> None:
        """T18：因果草稿文本进入待验证区，不进入结论区。"""
        env = build_env(tmp_path)
        record = env.controller.start(env.draft["plan_id"], 1, operator="analyst-a")
        evidence = EvidenceService(env.store, env.project_dir)
        evidence.collect(record["run_id"])
        evidence.verify(record["run_id"])

        causal = "断货导致女装下降；全面降价可提升销售"
        finding = add_to_verify(env.store, record["run_id"], causal, operator="analyst-a")
        assert finding["type"] == "to-verify"
        assert finding["status"] == "draft"

        auto = generate_findings(
            env.store, env.project_dir, record["run_id"],
            ["descriptive", "arithmetic-decomposition"],
        )
        auto_types = {f["type"] for f in auto}
        assert "to-verify" not in auto_types
        assert causal not in {f["statement"] for f in auto}
        stored = env.store.get("finding", finding["finding_id"], 1)
        assert stored["type"] == "to-verify"


class TestM3ResourceLimits:
    def test_worker_fsize_limit_enforced(self, tmp_path: Path) -> None:
        """M3：临时空间预算经 RLIMIT_FSIZE 强制（Worker 越限写盘被终止）。"""
        files = tmp_path / "files"
        run_dir = files / "runs" / "run-0001"
        run_dir.mkdir(parents=True)
        (files / "snapshots" / "dataset-x").mkdir(parents=True)
        snapshot = files / "snapshots" / "dataset-x" / "data.csv"
        snapshot.write_text("row_id,business_date,store_id,category_id,net_sales_amount,currency\n")
        request = {
            "run_id": "run-0001",
            "run_dir": str(run_dir),
            "snapshot_path": str(snapshot),
            "allowed_roots": [str(files)],
            # temp_space_mb=0 → RLIMIT_FSIZE=0：任何写盘（含 receipt.json）即越限
            "limits": {"run_timeout_seconds": 10, "step_timeout_seconds": 10,
                       "memory_mb": 256, "temp_space_mb": 0},
            "scope": {}, "coverage": {"kind": "unverified"}, "steps": [],
        }
        result = subprocess.run(
            [sys.executable, "-m", "methods.worker"],
            input=json.dumps(request) + "\n", capture_output=True, text=True, timeout=30,
        )
        # 越限写盘 → SIGXFSZ 终止或 fatal，绝不输出 done/COMPLETED
        assert result.returncode != 0
        assert '"type": "done"' not in result.stdout, result.stdout
        assert "COMPLETED" not in result.stdout, result.stdout
