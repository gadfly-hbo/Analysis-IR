"""S5 受限执行闭环测试（接缝 S-B：真实子进程 Worker + S-A 控制器）。"""

import json
import subprocess
import sys
import time
from pathlib import Path

from env_helper import GOLDEN_CSV, build_env


def _artifact(env, run_id: str, name: str) -> dict:
    path = env.project_dir / "runs" / run_id / "artifacts" / f"{name}.json"
    return json.loads(path.read_text(encoding="utf-8"))


class TestGoldenRun:
    def test_full_run_matches_expected_results(self, tmp_path: Path) -> None:
        # T02：总额、分组变化、对账与发现引用均匹配金标准
        env = build_env(tmp_path)
        record = env.controller.start(env.draft["plan_id"], 1, operator="analyst-a")
        assert record["status"] == "COMPLETED", record
        overall = _artifact(env, record["run_id"], "overall-delta")
        assert overall["base_amount_cents"] == 100_000_000
        assert overall["report_amount_cents"] == 87_000_000
        assert overall["delta_cents"] == -13_000_000
        assert overall["rate"] == -0.13

        store = _artifact(env, record["run_id"], "store-delta")
        store_deltas = {g["value"]: g["delta_cents"] for g in store["groups"]}
        assert store_deltas == {"S01": -5_000_000, "S02": -6_000_000, "S03": -2_000_000}

        category = _artifact(env, record["run_id"], "category-delta")
        cat_deltas = {g["value"]: g["delta_cents"] for g in category["groups"]}
        assert cat_deltas == {"C01": -12_000_000, "C02": -3_000_000, "C03": 2_000_000}

        quality = _artifact(env, record["run_id"], "quality-evidence")
        assert quality["status"] == "PASS"
        reconcile = _artifact(env, record["run_id"], "reconciliation-evidence")
        assert reconcile["status"] == "PASS"
        assert all(s["status"] == "PASS" for s in reconcile["slices"])

    def test_rerun_creates_new_run_id(self, tmp_path: Path) -> None:
        env = build_env(tmp_path)
        r1 = env.controller.start(env.draft["plan_id"], 1, operator="analyst-a")
        r2 = env.controller.start(env.draft["plan_id"], 1, operator="analyst-a")
        assert r1["run_id"] != r2["run_id"]
        assert env.store.get("run-record", r1["run_id"], 1) == r1  # 旧记录不被覆盖


class TestExecutionBlocks:
    def test_duplicate_rows_fail_and_block_aggregation(self, tmp_path: Path) -> None:
        # T05：重复 row_id → 检查失败，不静默去重、不继续汇总
        dup = tmp_path / "dup.csv"
        dup.write_text(GOLDEN_CSV.read_text() + "R001,2026-07-05,S01,C01,200000,CNY\n")
        env = build_env(tmp_path, source_file=dup)
        record = env.controller.start(env.draft["plan_id"], 1, operator="analyst-a")
        assert record["status"] == "FAILED"
        quality = _artifact(env, record["run_id"], "quality-evidence")
        assert quality["status"] == "FAIL"
        assert any(c["name"] == "row-uniqueness" and c["status"] == "FAIL"
                   for c in quality["checks"])
        # 后续聚合步骤被阻断
        steps = {s["step_id"]: s["status"] for s in record["steps"]}
        assert steps["scope"] == "SKIPPED" and steps["store"] == "SKIPPED"

    def test_unverified_coverage_yields_unknown_not_pass(self, tmp_path: Path) -> None:
        # T07：无覆盖证明 → UNKNOWN，不得宣称完整通过
        env = build_env(tmp_path, coverage={"kind": "unverified"})
        record = env.controller.start(env.draft["plan_id"], 1, operator="analyst-a")
        assert record["status"] == "COMPLETED"  # 计算完成 ≠ 验收通过
        quality = _artifact(env, record["run_id"], "quality-evidence")
        coverage = [c for c in quality["checks"] if c["name"] == "coverage"][0]
        assert coverage["status"] == "UNKNOWN"

    def test_capability_mismatch_blocks_before_run(self, tmp_path: Path) -> None:
        # T12：计划引用未安装的方法版本 → G2 阻断
        env = build_env(tmp_path)
        plan = json.loads(json.dumps(env.draft))
        plan["steps"][0]["method_ref"] = "sales-methods:quality@1.0.1"
        env.plan_service.save_draft(plan, operator="analyst-a", bump=True)
        record = env.controller.start(env.draft["plan_id"], 2, operator="analyst-a")
        assert record["status"] == "BLOCKED"
        assert record["terminated_reason"] == "capability-mismatch"

    def test_unapproved_plan_blocked(self, tmp_path: Path) -> None:
        env = build_env(tmp_path, approve=False)
        record = env.controller.start(env.draft["plan_id"], 1, operator="analyst-a")
        assert record["status"] == "BLOCKED"
        assert record["terminated_reason"] == "not-authorized"

    def test_nonpositive_base_no_rate_with_limitation(self, tmp_path: Path) -> None:
        # T06：基期为零 → 不输出普通增长率，显示绝对变化与限制
        csv_text = GOLDEN_CSV.read_text()
        # 去掉 C03 的基期记录 → C03 基期为 0
        for row in ("R007,2026-07-05,S01,C03,60000,CNY\n",
                    "R008,2026-07-15,S02,C03,60000,CNY\n",
                    "R009,2026-07-25,S03,C03,80000,CNY\n"):
            csv_text = csv_text.replace(row, "")
        zero_base = tmp_path / "zero-base.csv"
        zero_base.write_text(csv_text)
        env = build_env(
            tmp_path, source_file=zero_base,
            coverage={"kind": "manifest-ref",
                      "dates": ["2026-07-05", "2026-07-15", "2026-07-25",
                                "2026-08-05", "2026-08-15", "2026-08-25"]},
        )
        record = env.controller.start(env.draft["plan_id"], 1, operator="analyst-a")
        assert record["status"] == "COMPLETED"
        category = _artifact(env, record["run_id"], "category-delta")
        c03 = [g for g in category["groups"] if g["value"] == "C03"][0]
        assert c03["rate"] is None
        assert c03["delta_cents"] == 22_000_000  # 绝对变化仍展示
        reconcile = _artifact(env, record["run_id"], "reconciliation-evidence")
        assert any("C03" in lim or "基期为零" in lim for lim in reconcile["limitations"])

    def test_slices_never_summed_across_dimensions(self, tmp_path: Path) -> None:
        # T23：门店与品类是独立切片，产物中不存在跨维度相加的"整体贡献"
        env = build_env(tmp_path)
        record = env.controller.start(env.draft["plan_id"], 1, operator="analyst-a")
        artifacts_dir = env.project_dir / "runs" / record["run_id"] / "artifacts"
        names = {p.name for p in artifacts_dir.glob("*.json")}
        assert names == {
            "quality-evidence.json", "scope-evidence.json", "overall-delta.json",
            "store-delta.json", "category-delta.json", "reconciliation-evidence.json",
        }
        reconcile = _artifact(env, record["run_id"], "reconciliation-evidence")
        assert {s["dimension"] for s in reconcile["slices"]} == {"store_id", "category_id"}


class TestWorkerProtocol:
    def test_worker_rejects_out_of_bounds_path(self, tmp_path: Path) -> None:
        # T15：允许清单外路径（如 /etc/passwd）被拒绝并定位
        request = {
            "run_id": "run-9999",
            "run_dir": str(tmp_path / "runs"),
            "snapshot_path": "/etc/passwd",
            "allowed_roots": [str(tmp_path / "files")],
            "limits": {"run_timeout_seconds": 10, "step_timeout_seconds": 10,
                       "memory_mb": 256, "temp_space_mb": 64},
            "scope": {}, "coverage": {"kind": "unverified"}, "steps": [],
        }
        result = subprocess.run(
            [sys.executable, "-m", "methods.worker"],
            input=json.dumps(request) + "\n", capture_output=True, text=True, timeout=30,
        )
        assert result.returncode == 3
        fatal = json.loads(result.stdout.strip().splitlines()[-1])
        assert fatal["type"] == "fatal" and fatal["code"] == "PATH_NOT_ALLOWED"

    def test_timeout_kills_worker_and_preserves_partial_artifacts(self, tmp_path: Path) -> None:
        # T16：超时 → 终止进程、保留已完成步骤的部分证据、不给成功标记
        fake = tmp_path / "fake_worker.py"
        fake.write_text(
            "import json, sys, time\n"
            "req = json.loads(sys.stdin.readline())\n"
            "from pathlib import Path\n"
            "d = Path(req['run_dir']) / 'artifacts'; d.mkdir(parents=True)\n"
            "(d / 'quality-evidence.json').write_text('{\"status\": \"PASS\"}')\n"
            "print(json.dumps({'type': 'step', 'step_id': 'quality',"
            " 'status': 'DONE'}), flush=True)\n"
            "time.sleep(30)\n"
        )
        env = build_env(tmp_path, worker_cmd=[sys.executable, str(fake)])
        # 压缩预算：直接改执行清单限制不可（已确认），这里用受限 manifest 的副本
        exec_doc = env.store.list_objects("execution-manifest")[-1]
        exec_doc["resource_limits"]["run_timeout_seconds"] = 1
        # 通过新版本执行清单 + 重新确认授权来生效（内容变化 → 必须重新确认，T10 语义）
        from toolkit.execution import compile_execution_manifest
        plan = env.plan_service.get_plan(env.draft["plan_id"], 1)
        compile_execution_manifest(env.store, plan, resource_limits={"run_timeout_seconds": 1})
        env.approval_service.approve(
            env.draft["plan_id"], 1,
            {"operator": "analyst-a", "action": "approve", "origin": "test",
             "warnings_acknowledged": []},
        )
        started = time.monotonic()
        record = env.controller.start(env.draft["plan_id"], 1, operator="analyst-a")
        elapsed = time.monotonic() - started
        assert elapsed < 20, "超时未及时终止"
        assert record["status"] == "TIMED_OUT"
        assert record["terminated_reason"] == "timeout"
        run_dir = env.project_dir / "runs" / record["run_id"]
        assert (run_dir / "artifacts" / "quality-evidence.json").exists(), "部分证据应保留"
