"""发现生成器（proposal 9.4）：数值绑定证据，类型受输出契约约束。

首期自动发现仅 descriptive / arithmetic-decomposition；
有因果含义的文本只能进入待验证区，本生成器不产生任何因果结论（T18）。
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from adapters.local_store.project_store import ProjectStore
from toolkit.contracts import validate_object

_ALLOWED_TYPES = ("descriptive", "arithmetic-decomposition")


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _yuan(cents: int) -> float:
    return cents / 100.0


def add_to_verify(
    store: ProjectStore, run_id: str, text: str, operator: str
) -> dict[str, Any]:
    """T18：把因果含义的候选文本放入待验证区（to-verify，draft 状态）。

    待验证项不进入结论区、不参与自动通过；获得证据前只能停留在审查区。
    """
    if not text.strip():
        raise ValueError("待验证文本不能为空")
    existing = [f for f in store.list_objects("finding") if f["run_id"] == run_id]
    finding = {
        "schema_version": "1.0.0",
        "finding_id": f"finding-{run_id}-to-verify-{len(existing) + 1}",
        "run_id": run_id,
        "type": "to-verify",
        "statement": text.strip(),
        "evidence_item_ids": [],
        "status": "draft",
        "limitations": ["候选解释尚未获得证据；不构成结论"],
    }
    validate_object("finding", finding)
    store.put("finding", finding, created_at=_now())
    store.audit(_now(), operator, "add_to_verify", f"{finding['finding_id']}")
    return finding


def generate_findings(
    store: ProjectStore, project_dir: Path, run_id: str, allowed_types: list[str]
) -> list[dict[str, Any]]:
    run = store.get("run-record", run_id, 1)
    if run is None:
        raise KeyError(f"run not found: {run_id}")
    artifacts = project_dir / "runs" / run_id / "artifacts"
    overall = json.loads((artifacts / "overall-delta.json").read_text(encoding="utf-8"))
    store_delta = json.loads((artifacts / "store-delta.json").read_text(encoding="utf-8"))
    category_delta = json.loads((artifacts / "category-delta.json").read_text(encoding="utf-8"))

    findings: list[dict[str, Any]] = []

    if "descriptive" in allowed_types:
        delta_yuan = _yuan(overall["delta_cents"])
        rate = overall["rate"]
        rate_text = f"，变化率 {rate:.2%}" if rate is not None else "（基期 ≤ 0，不展示普通增长率）"
        findings.append({
            "schema_version": "1.0.0",
            "finding_id": f"finding-{run_id}-1",
            "run_id": run_id,
            "type": "descriptive",
            "statement": (
                f"在本任务确认的范围与口径下，净销售额变化 {delta_yuan:,.2f}{rate_text}。"
            ),
            "evidence_item_ids": ["ev-overall-delta"],
            "status": "published",
            "limitations": [],
            "numbers": [
                {
                    "label": "整体变化额",
                    "value": delta_yuan,
                    "evidence_item_id": "ev-overall-delta",
                },
            ],
        })

    if "arithmetic-decomposition" in allowed_types:
        for index, (artifact, ev_id) in enumerate(
            ((store_delta, "ev-store-delta"), (category_delta, "ev-category-delta")), start=2
        ):
            groups = artifact["groups"]
            if not groups:
                continue
            # 按 |变化额| 排名（proposal 4.4：不用 Δg/Δ 比例排名）
            ranked = sorted(groups, key=lambda g: abs(g["delta_cents"]), reverse=True)
            top_neg = next((g for g in ranked if g["delta_cents"] < 0), None)
            top_pos = next((g for g in ranked if g["delta_cents"] > 0), None)
            parts = [f"按 {artifact['dimension']} 拆解（独立切片，不相加）："]
            numbers: list[dict[str, Any]] = []
            if top_neg:
                parts.append(
                    f"最大负向贡献 {top_neg['value']} {_yuan(top_neg['delta_cents']):,.2f}"
                )
                numbers.append({
                    "label": f"{artifact['dimension']} 最大负向",
                    "value": _yuan(top_neg["delta_cents"]),
                    "evidence_item_id": ev_id,
                })
            if top_pos:
                parts.append(
                    f"最大正向贡献 {top_pos['value']} {_yuan(top_pos['delta_cents']):,.2f}"
                )
                numbers.append({
                    "label": f"{artifact['dimension']} 最大正向",
                    "value": _yuan(top_pos["delta_cents"]),
                    "evidence_item_id": ev_id,
                })
            findings.append({
                "schema_version": "1.0.0",
                "finding_id": f"finding-{run_id}-{index}",
                "run_id": run_id,
                "type": "arithmetic-decomposition",
                "statement": "；".join(parts) + "。",
                "evidence_item_ids": [ev_id],
                "status": "published",
                "limitations": [artifact["slice_note"]],
                "numbers": numbers,
            })

    for finding in findings:
        assert finding["type"] in _ALLOWED_TYPES  # 生成器永不产生因果类型（T18）
        validate_object("finding", finding)
        store.put("finding", finding, created_at=_now())
    return findings
