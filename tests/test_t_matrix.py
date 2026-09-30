"""S10 验收矩阵收口：T01 独立启动 / T13 缺检查实现 / T24 断网 / 三类交付样例。

其余 T02—T12、T14—T23 已在各切片测试中覆盖（见 tests/failures/T-MAP.md）。
"""

import json
import socket
from pathlib import Path

import pytest
from env_helper import GOLDEN_CSV, build_env

from toolkit.approval_service import ApprovalRejected
from toolkit.evidence_service import EvidenceError, EvidenceService
from toolkit.execution import compile_execution_manifest


class TestOfflineStandalone:
    def test_t01_t24_full_loop_with_network_blocked(self, tmp_path: Path, monkeypatch) -> None:
        """T01/T24：不安装任何平台/模型服务、禁网状态下完成全部首期流程。"""

        def blocked_socket(*args, **kwargs):  # type: ignore[no-untyped-def]
            raise AssertionError("网络被禁用（T24）：工具不应发起任何网络连接")

        monkeypatch.setattr(socket, "socket", blocked_socket)
        monkeypatch.setattr(socket, "create_connection", blocked_socket)

        env = build_env(tmp_path)  # 创建→绑定→校验→确认
        record = env.controller.start(env.draft["plan_id"], 1, operator="analyst-a")
        assert record["status"] == "COMPLETED"
        evidence = EvidenceService(env.store, env.project_dir)
        evidence.collect(record["run_id"])
        result = evidence.verify(record["run_id"])
        assert result.status == "PASSED"
        accepted = evidence.accept(record["run_id"], "ACCEPTED", operator="boss")
        assert accepted["decision"] == "ACCEPTED"
        # 导出仍可用（本地文件操作）
        from toolkit.export_service import ExportService

        package = ExportService(env.store, env.project_dir).build_package(
            env.draft["plan_id"], 1, "handover", operator="analyst-a"
        )
        assert package.exists()


class TestMissingCheckImplementation:
    def test_t13_unknown_required_check_blocks(self, tmp_path: Path) -> None:
        """T13：必需检查无实现/不在注册表 → 定位报告，不可确认，不显示 PASS。"""
        env = build_env(tmp_path, approve=False)
        plan = json.loads(json.dumps(env.draft))
        plan["required_check_refs"].append("sales-checks:nonexistent-check@1.0.0")
        env.plan_service.save_draft(plan, operator="analyst-a", bump=True)
        report = env.plan_service.validate(env.draft["plan_id"], 2)
        assert any(i.code == "UNKNOWN_REQUIRED_CHECK" for i in report.issues)
        compile_execution_manifest(env.store, env.plan_service.get_plan(env.draft["plan_id"], 2))
        with pytest.raises(ApprovalRejected, match="G0_NOT_PASSED"):
            env.approval_service.approve(
                env.draft["plan_id"], 2,
                {"operator": "analyst-a", "action": "approve", "origin": "test",
                 "warnings_acknowledged": []},
            )


class TestThreeDeliverySamples:
    def test_success_failure_limited_samples(self, tmp_path: Path) -> None:
        """交付样例同时包含成功、失败、受限三类状态（proposal 14.2）。"""
        # ① 成功样例
        ok_env = build_env(tmp_path / "ok")
        ok_run = ok_env.controller.start(ok_env.draft["plan_id"], 1, operator="analyst-a")
        ok_ev = EvidenceService(ok_env.store, ok_env.project_dir)
        ok_ev.collect(ok_run["run_id"])
        ok_verification = ok_ev.verify(ok_run["run_id"])
        ok_accept = ok_ev.accept(ok_run["run_id"], "ACCEPTED", operator="boss")
        assert (ok_run["status"], ok_verification.status, ok_accept["decision"]) == (
            "COMPLETED", "PASSED", "ACCEPTED"
        )

        # ② 失败样例：重复 row_id → 运行失败
        dup = tmp_path / "dup.csv"
        dup.write_text(GOLDEN_CSV.read_text() + "R001,2026-07-05,S01,C01,200000,CNY\n")
        fail_env = build_env(tmp_path / "fail", source_file=dup)
        fail_run = fail_env.controller.start(fail_env.draft["plan_id"], 1, operator="analyst-a")
        assert fail_run["status"] == "FAILED"

        # ③ 受限样例：覆盖未验证 → 计算完成、验证失败、只能退回或降级重计划
        limited_env = build_env(tmp_path / "limited", coverage={"kind": "unverified"})
        limited_run = limited_env.controller.start(
            limited_env.draft["plan_id"], 1, operator="analyst-a"
        )
        limited_ev = EvidenceService(limited_env.store, limited_env.project_dir)
        limited_ev.collect(limited_run["run_id"])
        limited_verification = limited_ev.verify(limited_run["run_id"])
        with pytest.raises(EvidenceError):
            limited_ev.accept(limited_run["run_id"], "ACCEPTED", operator="boss")
        rejected = limited_ev.accept(limited_run["run_id"], "REJECTED", operator="boss")
        assert (limited_run["status"], limited_verification.status, rejected["decision"]) == (
            "COMPLETED", "FAILED", "REJECTED"
        )
