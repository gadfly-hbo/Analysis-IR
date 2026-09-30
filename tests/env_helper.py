"""测试环境组装：走到「计划已确认、可启动运行」的完整状态。"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from adapters.local_context.context_port import LocalContext
from adapters.local_store.project_store import ProjectStore
from toolkit.approval_service import ApprovalService
from toolkit.execution import compile_execution_manifest
from toolkit.plan_service import PlanService
from toolkit.run_controller import RunController

GOLDEN_CSV = Path(__file__).parent / "golden" / "sales-golden.csv"

GOLDEN_COVERAGE = {
    "kind": "manifest-ref",
    "dates": [
        "2026-07-05", "2026-07-15", "2026-07-25",
        "2026-08-05", "2026-08-15", "2026-08-25",
    ],
}

PARAMS = {
    "question": "最近门店销售下降，分析原因",
    "decision_purpose": "品类策略评估",
    "base_period": {"start": "2026-07-01", "end": "2026-07-31"},
    "report_period": {"start": "2026-08-01", "end": "2026-08-31"},
}

TREATMENTS = {
    "tax": "net-of-tax",
    "discounts": "after-discount",
    "returns_attribution": "transaction-period",
}

FIELD_MAP = {
    "row_id": "row_id",
    "business_date": "business_date",
    "store_id": "store_id",
    "category_id": "category_id",
    "net_sales_amount": "net_sales_amount",
    "currency": "currency",
}


def build_env(
    tmp_path: Path,
    source_file: Path | None = None,
    coverage: dict[str, Any] | None = None,
    treatments: dict[str, str] | None = None,
    approve: bool = True,
    worker_cmd: list[str] | None = None,
) -> SimpleNamespace:
    store = ProjectStore(tmp_path / "project.db")
    plan_service = PlanService(store)
    plan_service.register_builtin_registries()
    draft = plan_service.create_draft("sales-delta", PARAMS, operator="analyst-a").plan

    project_dir = tmp_path / "files"
    context = LocalContext(project_dir=project_dir)
    context.bind(
        plan=draft,
        source_file=source_file or GOLDEN_CSV,
        field_map=dict(FIELD_MAP),
        metric_treatments=treatments or dict(TREATMENTS),
        coverage=coverage or GOLDEN_COVERAGE,
        operator="analyst-a",
        store=store,
    )
    report = plan_service.validate(draft["plan_id"], draft["plan_version"])
    assert report.g0_passed, [i.code for i in report.issues]

    snapshot_manifest = store.get(
        "data-snapshot-manifest",
        draft["dataset_refs"][0]["id"], draft["dataset_refs"][0]["version"],
    )
    assert snapshot_manifest is not None
    approval_service = ApprovalService(store, plan_service, context)
    approval = None
    if approve:
        acks: list[dict[str, str]] = []
        if (coverage or GOLDEN_COVERAGE).get("kind") == "unverified":
            acks.append({"code": "coverage-unverified", "note": "测试环境知晓无覆盖证明"})
        compile_execution_manifest(store, draft)
        approval = approval_service.approve(
            draft["plan_id"], draft["plan_version"],
            {"operator": "analyst-a", "action": "approve", "origin": "test",
             "warnings_acknowledged": acks},
        )
    controller = RunController(
        store, plan_service, approval_service, context, project_dir,
        worker_cmd=worker_cmd or [sys.executable, "-m", "methods.worker"],
    )
    return SimpleNamespace(
        tmp=tmp_path, store=store, plan_service=plan_service, draft=draft,
        context=context, project_dir=project_dir, snapshot_manifest=snapshot_manifest,
        approval_service=approval_service, approval=approval, controller=controller,
    )
