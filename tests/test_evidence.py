"""S6 证据与验收测试 + M1 端到端闭环（接缝 S-A/S-C）。"""

import json
from pathlib import Path

import pytest
from env_helper import build_env

from toolkit.evidence_service import EvidenceError, EvidenceService
from toolkit.findings import generate_findings


def _run_and_collect(env) -> tuple:
    record = env.controller.start(env.draft["plan_id"], 1, operator="analyst-a")
    evidence = EvidenceService(env.store, env.project_dir)
    bundle = evidence.collect(record["run_id"])
    return record, evidence, bundle


class TestCollectAndVerify:
    def test_collect_binds_items_with_digests(self, tmp_path: Path) -> None:
        env = build_env(tmp_path)
        record, evidence, bundle = _run_and_collect(env)
        assert bundle["run_id"] == record["run_id"]
        assert bundle["plan_digest"] == record["plan_digest"]
        assert {i["item_id"] for i in bundle["items"]} == {
            "ev-quality-evidence", "ev-scope-evidence", "ev-overall-delta",
            "ev-store-delta", "ev-category-delta", "ev-reconciliation-evidence",
        }

    def test_golden_run_verifies_passed(self, tmp_path: Path) -> None:
        env = build_env(tmp_path)
        record, evidence, _ = _run_and_collect(env)
        result = evidence.verify(record["run_id"])
        assert result.status == "PASSED"
        assert result.acceptance_allowed

    def test_tampered_artifact_fails_verification(self, tmp_path: Path) -> None:
        # T14：人为修改分组结果 → 验证失败，不得完整接受
        env = build_env(tmp_path)
        record = env.controller.start(env.draft["plan_id"], 1, operator="analyst-a")
        evidence = EvidenceService(env.store, env.project_dir)
        evidence.collect(record["run_id"])
        artifact = env.project_dir / "runs" / record["run_id"] / "artifacts" / "store-delta.json"
        doc = json.loads(artifact.read_text())
        doc["groups"][0]["delta_cents"] = -99_999_999
        artifact.write_text(json.dumps(doc))
        result = evidence.verify(record["run_id"])
        assert result.status == "FAILED"
        with pytest.raises(EvidenceError):
            evidence.accept(record["run_id"], "ACCEPTED", operator="boss")

    def test_cross_run_bundle_rejected(self, tmp_path: Path) -> None:
        # T17：把旧运行证据换成新运行附件 → 关联校验失败
        env = build_env(tmp_path)
        r1 = env.controller.start(env.draft["plan_id"], 1, operator="analyst-a")
        evidence = EvidenceService(env.store, env.project_dir)
        bundle_1 = evidence.collect(r1["run_id"])
        r2 = env.controller.start(env.draft["plan_id"], 1, operator="analyst-a")
        evidence.collect(r2["run_id"])
        # 篡改：把 run-0002 的 bundle 换成 run-0001 的内容
        forged = {**bundle_1, "evidence_id": f"evidence-{r2['run_id']}"}
        env.store._conn.execute(
            "UPDATE objects SET doc = ? WHERE kind = 'evidence-bundle' AND id = ?",
            (json.dumps(forged, ensure_ascii=False), f"evidence-{r2['run_id']}"),
        )
        env.store._conn.commit()
        result = evidence.verify(r2["run_id"])
        linkage = [c for c in result.checks if c["name"] == "run-linkage"][0]
        assert linkage["status"] == "FAIL"
        assert result.status == "FAILED"

    def test_unknown_required_check_blocks_all_acceptance(self, tmp_path: Path) -> None:
        # T07/G4 决议：必需检查 UNKNOWN → 阻断一切接受（含带限制）
        env = build_env(tmp_path, coverage={"kind": "unverified"})
        record = env.controller.start(env.draft["plan_id"], 1, operator="analyst-a")
        evidence = EvidenceService(env.store, env.project_dir)
        evidence.collect(record["run_id"])
        result = evidence.verify(record["run_id"])
        assert result.status == "FAILED"
        with pytest.raises(EvidenceError):
            evidence.accept(record["run_id"], "ACCEPTED", operator="boss")
        with pytest.raises(EvidenceError):
            evidence.accept(record["run_id"], "ACCEPTED_WITH_LIMITATIONS", operator="boss")
        # 退回始终允许
        rejected = evidence.accept(record["run_id"], "REJECTED", operator="boss")
        assert rejected["decision"] == "REJECTED"

    def test_partial_artifacts_fail_coverage(self, tmp_path: Path) -> None:
        # T16 后段：中断运行 → 部分证据可回收，覆盖检查失败
        env = build_env(tmp_path)
        record = env.controller.start(env.draft["plan_id"], 1, operator="analyst-a")
        # 删除一个产物模拟部分证据
        (env.project_dir / "runs" / record["run_id"] / "artifacts" / "store-delta.json").unlink()
        evidence = EvidenceService(env.store, env.project_dir)
        evidence.collect(record["run_id"])
        result = evidence.verify(record["run_id"])
        assert result.status == "FAILED"


class TestFindings:
    def test_findings_numbers_bound_to_evidence(self, tmp_path: Path) -> None:
        env = build_env(tmp_path)
        record, evidence, bundle = _run_and_collect(env)
        evidence.verify(record["run_id"])
        findings = generate_findings(
            env.store, env.project_dir, record["run_id"],
            ["descriptive", "arithmetic-decomposition"],
        )
        item_ids = {i["item_id"] for i in bundle["items"]}
        for finding in findings:
            assert set(finding["evidence_item_ids"]) <= item_ids  # T17 追溯
            assert finding["type"] in ("descriptive", "arithmetic-decomposition")  # T18
        overall = [f for f in findings if f["type"] == "descriptive"][0]
        assert overall["numbers"][0]["value"] == -130000.0

    def test_causal_text_never_auto_published(self, tmp_path: Path) -> None:
        # T18：因果候选文本不能进入自动通过发现
        env = build_env(tmp_path)
        record, evidence, _ = _run_and_collect(env)
        findings = generate_findings(
            env.store, env.project_dir, record["run_id"],
            ["descriptive", "arithmetic-decomposition"],
        )
        banned = ("导致", "因为", "降价将", "断货")
        for finding in findings:
            assert not any(word in finding["statement"] for word in banned)


class TestM1EndToEnd:
    def test_full_closed_loop_and_rerun(self, tmp_path: Path) -> None:
        """M1 收口：草稿→绑定→校验→确认→执行→证据→验收→发现→复跑（S-C 接缝）。"""
        env = build_env(tmp_path)
        evidence = EvidenceService(env.store, env.project_dir)

        record = env.controller.start(env.draft["plan_id"], 1, operator="analyst-a")
        assert record["status"] == "COMPLETED"
        bundle = evidence.collect(record["run_id"])
        assert bundle["run_id"] == record["run_id"]
        result = evidence.verify(record["run_id"])
        assert result.status == "PASSED"
        accepted = evidence.accept(record["run_id"], "ACCEPTED", operator="boss")
        assert accepted["verification_status"] == "PASSED"
        findings = generate_findings(
            env.store, env.project_dir, record["run_id"],
            ["descriptive", "arithmetic-decomposition"],
        )
        assert len(findings) == 3

        # 复跑：新 run_id、独立验收，旧记录不覆盖
        rerun = env.controller.start(env.draft["plan_id"], 1, operator="analyst-a")
        assert rerun["run_id"] != record["run_id"]
        evidence.collect(rerun["run_id"])
        result2 = evidence.verify(rerun["run_id"])
        assert result2.status == "PASSED"
        assert env.store.get("run-record", record["run_id"], 1) == record

    def test_three_status_axes_separated(self, tmp_path: Path) -> None:
        # proposal 8.3/5.4：运行完成 ≠ 验证通过 ≠ 用户接受，三者分轴
        env = build_env(tmp_path, coverage={"kind": "unverified"})
        record = env.controller.start(env.draft["plan_id"], 1, operator="analyst-a")
        assert record["status"] == "COMPLETED"  # 运行轴：完成
        evidence = EvidenceService(env.store, env.project_dir)
        evidence.collect(record["run_id"])
        verification = evidence.verify(record["run_id"])
        assert verification.status == "FAILED"  # 验证轴：失败
        with pytest.raises(EvidenceError):
            evidence.accept(record["run_id"], "ACCEPTED", operator="boss")  # 人审轴：被阻断
