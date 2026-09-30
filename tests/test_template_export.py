"""S7 模板与导出测试（T19 复用清污 / T20 包治理）。"""

import json
import zipfile
from pathlib import Path

import pytest
from env_helper import build_env

from toolkit.evidence_service import EvidenceService
from toolkit.export_service import ExportError, ExportService
from toolkit.template_service import TemplateService

_NEXT_PARAMS = {
    "question": "9-10 月销售变化复盘",
    "decision_purpose": "品类策略评估（下一周期）",
    "base_period": {"start": "2026-09-01", "end": "2026-09-30"},
    "report_period": {"start": "2026-10-01", "end": "2026-10-31"},
}


def _next_cycle_csv(tmp_path: Path) -> Path:
    """下一周期数据：整体 +40k，各品类/门店等比放大，可手工核对。"""
    rows = [
        ("R101", "2026-09-05", "S01", "C01", 200000),
        ("R102", "2026-09-15", "S02", "C01", 200000),
        ("R103", "2026-09-25", "S03", "C01", 100000),
        ("R104", "2026-09-05", "S01", "C02", 100000),
        ("R105", "2026-09-15", "S02", "C02", 100000),
        ("R106", "2026-09-25", "S03", "C02", 100000),
        ("R107", "2026-09-05", "S01", "C03", 60000),
        ("R108", "2026-09-15", "S02", "C03", 60000),
        ("R109", "2026-09-25", "S03", "C03", 80000),
        ("R110", "2026-10-05", "S01", "C01", 220000),
        ("R111", "2026-10-15", "S02", "C01", 210000),
        ("R112", "2026-10-25", "S03", "C01", 110000),
        ("R113", "2026-10-05", "S01", "C02", 105000),
        ("R114", "2026-10-15", "S02", "C02", 100000),
        ("R115", "2026-10-25", "S03", "C02", 95000),
        ("R116", "2026-10-05", "S01", "C03", 66000),
        ("R117", "2026-10-15", "S02", "C03", 64000),
        ("R118", "2026-10-25", "S03", "C03", 90000),
    ]
    lines = ["row_id,business_date,store_id,category_id,net_sales_amount,currency"]
    lines += [f"{r[0]},{r[1]},{r[2]},{r[3]},{r[4]},CNY" for r in rows]
    path = tmp_path / "next-cycle.csv"
    path.write_text("\n".join(lines) + "\n")
    return path


class TestTemplateReuse:
    def test_extract_clears_old_state(self, tmp_path: Path) -> None:
        # T19：模板只保存方法与适用条件
        env = build_env(tmp_path)
        record = env.controller.start(env.draft["plan_id"], 1, operator="analyst-a")
        evidence = EvidenceService(env.store, env.project_dir)
        evidence.collect(record["run_id"])
        evidence.verify(record["run_id"])
        evidence.accept(record["run_id"], "ACCEPTED", operator="boss")

        svc = TemplateService(env.store, env.plan_service)
        template = svc.extract(env.draft["plan_id"], 1, "销售差异模板", operator="analyst-a")
        text = json.dumps(template, ensure_ascii=False)
        for forbidden in ("run-0001", "approval-", "-130000", env.snapshot_manifest["byte_digest"]):
            assert forbidden not in text

    def test_next_cycle_instantiate_rebind_rerun(self, tmp_path: Path) -> None:
        # T19：换下一周期数据实例化 → 清除旧批准/结果 → 重新绑定确认 → 独立运行
        env = build_env(tmp_path)
        old_run = env.controller.start(env.draft["plan_id"], 1, operator="analyst-a")

        svc = TemplateService(env.store, env.plan_service)
        template = svc.extract(env.draft["plan_id"], 1, "销售差异模板", operator="analyst-a")
        result = svc.instantiate(template["template_id"], _NEXT_PARAMS, operator="analyst-a")
        new_plan = result.plan
        assert new_plan["plan_id"] != env.draft["plan_id"]
        assert new_plan["plan_version"] == 1
        assert env.approval_service.is_authorized(new_plan["plan_id"], 1) is None  # 无旧批准可沿用

        # 重新绑定下一周期数据
        from env_helper import FIELD_MAP, TREATMENTS
        next_csv = _next_cycle_csv(tmp_path)
        env.context.bind(
            plan=new_plan, source_file=next_csv, field_map=dict(FIELD_MAP),
            metric_treatments=dict(TREATMENTS),
            coverage={"kind": "manifest-ref",
                      "dates": ["2026-09-05", "2026-09-15", "2026-09-25",
                                "2026-10-05", "2026-10-15", "2026-10-25"]},
            operator="analyst-a", store=env.store,
        )
        assert env.plan_service.validate(new_plan["plan_id"], 1).g0_passed
        from toolkit.execution import compile_execution_manifest
        compile_execution_manifest(env.store, new_plan)
        env.approval_service.approve(
            new_plan["plan_id"], 1,
            {"operator": "analyst-a", "action": "approve", "origin": "test",
             "warnings_acknowledged": []},
        )
        new_run = env.controller.start(new_plan["plan_id"], 1, operator="analyst-a")
        assert new_run["status"] == "COMPLETED"
        assert new_run["run_id"] != old_run["run_id"]

        overall = json.loads(
            (env.project_dir / "runs" / new_run["run_id"] / "artifacts" / "overall-delta.json")
            .read_text()
        )
        # 基期 1,000,000 → 报告期 1,060,000：Δ = +60,000（手算：
        # 报告期 = 540,000 + 300,000 + 220,000）
        assert overall["base_amount_cents"] == 100_000_000
        assert overall["report_amount_cents"] == 106_000_000
        assert overall["delta_cents"] == 6_000_000
        # 旧计划运行记录不受影响
        assert env.store.get("run-record", old_run["run_id"], 1) == old_run


class TestExportImport:
    def test_handover_package_has_no_raw_data_or_absolute_paths(self, tmp_path: Path) -> None:
        # T20：正常包保留状态且默认无原始数据、无绝对路径
        env = build_env(tmp_path)
        exporter = ExportService(env.store, env.project_dir)
        package = exporter.build_package(
            env.draft["plan_id"], 1, "handover", operator="analyst-a"
        )
        with zipfile.ZipFile(package) as zf:
            names = zf.namelist()
            assert not any(n.endswith(".csv") for n in names), "交接包默认不含原始数据"
            for name in names:
                content = zf.read(name).decode("utf-8", errors="ignore")
                assert "/Users/" not in content, f"{name} 泄漏本机绝对路径"
            manifest = json.loads(zf.read("manifest.json"))
            assert manifest["includes"] == {"results": False, "data": False}

    def test_delivery_package_carries_results_and_status(self, tmp_path: Path) -> None:
        env = build_env(tmp_path)
        record = env.controller.start(env.draft["plan_id"], 1, operator="analyst-a")
        evidence = EvidenceService(env.store, env.project_dir)
        evidence.collect(record["run_id"])
        evidence.verify(record["run_id"])
        exporter = ExportService(env.store, env.project_dir)
        package = exporter.build_package(
            env.draft["plan_id"], 1, "delivery", operator="analyst-a",
            include_results=True,
        )
        with zipfile.ZipFile(package) as zf:
            names = zf.namelist()
            assert f"runs/{record['run_id']}/run-record.json" in names
            assert f"runs/{record['run_id']}/artifacts/overall-delta.json" in names
            assert f"runs/{record['run_id']}/verification.json" in names

    def test_import_restores_objects_without_authorization(self, tmp_path: Path) -> None:
        # T20：导入后对象可读，但原批准不自动授权（需重新绑定确认）
        env = build_env(tmp_path)
        record = env.controller.start(env.draft["plan_id"], 1, operator="analyst-a")
        evidence = EvidenceService(env.store, env.project_dir)
        evidence.collect(record["run_id"])
        exporter = ExportService(env.store, env.project_dir)
        package = exporter.build_package(
            env.draft["plan_id"], 1, "delivery", operator="analyst-a", include_results=True
        )

        from adapters.local_store.project_store import ProjectStore
        from toolkit.approval_service import ApprovalService
        from toolkit.plan_service import PlanService
        fresh_dir = tmp_path / "fresh"
        fresh_dir.mkdir()
        fresh_store = ProjectStore(fresh_dir / "fresh.db")
        fresh_exporter = ExportService(fresh_store, fresh_dir / "files")
        report = fresh_exporter.import_package(package, operator="analyst-b")
        assert "analysis-plan-ir" in report.imported_kinds
        assert report.plan_status == "NEEDS_INPUT"

        fresh_plans = PlanService(fresh_store)
        fresh_plans.register_builtin_registries()
        from adapters.local_context.context_port import LocalContext
        fresh_approval = ApprovalService(
            fresh_store, fresh_plans, LocalContext(project_dir=fresh_dir / "files")
        )
        assert fresh_approval.is_authorized(env.draft["plan_id"], 1) is None

    def test_import_rejects_path_traversal(self, tmp_path: Path) -> None:
        # T20：含越界路径的异常包被拒绝
        env = build_env(tmp_path)
        exporter = ExportService(env.store, env.project_dir)
        package = exporter.build_package(
            env.draft["plan_id"], 1, "handover", operator="analyst-a"
        )
        evil = tmp_path / "evil.zip"
        with zipfile.ZipFile(package) as src, zipfile.ZipFile(evil, "w") as dst:
            for name in src.namelist():
                dst.writestr(name, src.read(name))
            dst.writestr("../escape.json", "{}")
        with pytest.raises(ExportError, match="路径越界"):
            exporter.import_package(evil, operator="attacker")

    def test_import_rejects_executable_payload(self, tmp_path: Path) -> None:
        env = build_env(tmp_path)
        exporter = ExportService(env.store, env.project_dir)
        package = exporter.build_package(
            env.draft["plan_id"], 1, "handover", operator="analyst-a"
        )
        evil = tmp_path / "evil2.zip"
        with zipfile.ZipFile(package) as src, zipfile.ZipFile(evil, "w") as dst:
            for name in src.namelist():
                dst.writestr(name, src.read(name))
            dst.writestr("setup.py", "print('pwned')")
        with pytest.raises(ExportError, match="不允许的文件类型"):
            exporter.import_package(evil, operator="attacker")

    def test_import_rejects_tampered_digest(self, tmp_path: Path) -> None:
        env = build_env(tmp_path)
        exporter = ExportService(env.store, env.project_dir)
        package = exporter.build_package(
            env.draft["plan_id"], 1, "handover", operator="analyst-a"
        )
        evil = tmp_path / "evil3.zip"
        with zipfile.ZipFile(package) as src, zipfile.ZipFile(evil, "w") as dst:
            for name in src.namelist():
                payload = src.read(name)
                if name == "objects/analysis-contract.json":
                    doc = json.loads(payload)
                    doc["question"] = "被篡改的问题"
                    payload = json.dumps(doc, ensure_ascii=False).encode()
                dst.writestr(name, payload)
        with pytest.raises(ExportError, match="摘要不符"):
            exporter.import_package(evil, operator="attacker")
