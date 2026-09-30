"""性能与资源测试点（proposal 14.4）：生成 N 行固定结构数据走完整闭环，记录耗时。

用法：uv run python scripts/perf_check.py [rows]（默认 100000）
结果写入 docs/performance.md（追加），含实测环境说明。
"""

from __future__ import annotations

import platform
import resource
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "tests"))

from env_helper import FIELD_MAP, TREATMENTS  # noqa: E402


def generate_csv(path: Path, rows: int) -> None:
    import random

    rng = random.Random(42)
    stores = [f"S{i:03d}" for i in range(1, 31)]
    categories = ["C01", "C02", "C03"]
    with path.open("w", encoding="utf-8", newline="") as f:
        f.write("row_id,business_date,store_id,category_id,net_sales_amount,currency\n")
        for i in range(rows):
            month, day = ("07", 1 + (i // 2) % 28) if i % 2 == 0 else ("08", 1 + (i // 2) % 28)
            amount = rng.randint(1000, 50000)
            f.write(
                f"R{i:07d},2026-{month}-{day:02d},{stores[i % 30]},"
                f"{categories[i % 3]},{amount},CNY\n"
            )


def main(rows: int) -> None:
    import tempfile

    from adapters.local_context.context_port import LocalContext
    from adapters.local_store.project_store import ProjectStore
    from toolkit.approval_service import ApprovalService
    from toolkit.evidence_service import EvidenceService
    from toolkit.execution import compile_execution_manifest
    from toolkit.plan_service import PlanService
    from toolkit.run_controller import RunController

    tmp = Path(tempfile.mkdtemp(prefix="aps-perf-"))
    csv_path = tmp / "perf.csv"
    t0 = time.perf_counter()
    generate_csv(csv_path, rows)
    gen_seconds = time.perf_counter() - t0

    marks: dict[str, float] = {}
    t0 = time.perf_counter()
    store = ProjectStore(tmp / "project.db")
    plan_service = PlanService(store)
    plan_service.register_builtin_registries()
    draft = plan_service.create_draft("sales-delta", {
        "question": "性能测试", "decision_purpose": "性能测试",
        "base_period": {"start": "2026-07-01", "end": "2026-07-31"},
        "report_period": {"start": "2026-08-01", "end": "2026-08-31"},
    }, operator="perf").plan
    marks["create_draft_s"] = time.perf_counter() - t0

    files_dir = tmp / "files"
    context = LocalContext(project_dir=files_dir)
    t0 = time.perf_counter()
    context.bind(
        plan=draft, source_file=csv_path, field_map=dict(FIELD_MAP),
        metric_treatments=dict(TREATMENTS),
        coverage={"kind": "manifest-ref", "dates": [f"2026-07-{d:02d}" for d in range(1, 29)]
                  + [f"2026-08-{d:02d}" for d in range(1, 29)]},
        operator="perf", store=store,
    )
    marks["bind_import_digest_s"] = time.perf_counter() - t0

    plan_service.validate(draft["plan_id"], 1)
    compile_execution_manifest(store, draft)
    approval_service = ApprovalService(store, plan_service, context)
    approval_service.approve(draft["plan_id"], 1, {
        "operator": "perf", "action": "approve", "origin": "cli",
        "warnings_acknowledged": [],
    })

    controller = RunController(store, plan_service, approval_service, context, files_dir)
    t0 = time.perf_counter()
    record = controller.start(draft["plan_id"], 1, operator="perf")
    marks["run_total_s"] = time.perf_counter() - t0

    t0 = time.perf_counter()
    evidence = EvidenceService(store, files_dir)
    evidence.collect(record["run_id"])
    evidence.verify(record["run_id"])
    marks["evidence_collect_verify_s"] = time.perf_counter() - t0

    # macOS 的 ru_maxrss 单位是字节，Linux 是 KiB
    rss_divisor = 1024 * 1024 if sys.platform == "darwin" else 1024
    peak_kb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    peak_child_kb = resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss

    result = {
        "rows": rows,
        "run_status": record["status"],
        "gen_csv_s": round(gen_seconds, 2),
        **{k: round(v, 2) for k, v in marks.items()},
        "peak_rss_parent_mb": round(peak_kb / rss_divisor, 1),
        "peak_rss_child_mb": round(peak_child_kb / rss_divisor, 1),
        "python": sys.version.split()[0],
        "machine": f"{platform.machine()} / {platform.platform()}",
    }
    print(result)

    doc = REPO / "docs" / "performance.md"
    doc.parent.mkdir(exist_ok=True)
    with doc.open("a", encoding="utf-8") as f:
        f.write(f"\n- `{result}`\n")
    print(f"appended -> {doc}")


if __name__ == "__main__":
    main(int(sys.argv[1]) if len(sys.argv) > 1 else 100_000)
