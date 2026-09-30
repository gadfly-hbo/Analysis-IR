"""语义级差异（proposal F06：不做无解释的字符串差异）。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class DiffEntry:
    path: str
    old: Any
    new: Any
    impact: str


def classify_impact(path: str) -> str:
    if any(k in path for k in ("method_ref", "required_check_refs", "registry_ref")):
        return "method"
    if path.startswith("contract.comparison_scope") or ".params" in path:
        return "scope"
    if any(k in path for k in ("binding_ref", "dataset_refs", "metric_refs")):
        return "data"
    if "output_contract" in path:
        return "validation"
    if "execution_policy" in path or "resource" in path:
        return "permission"
    return "cosmetic"


def leaf_diff(
    left: Any, right: Any, prefix: str, entries: list[DiffEntry] | None = None
) -> list[DiffEntry]:
    """递归对比两棵 JSON 树，产出叶级差异并按路径分类影响。"""
    entries = entries if entries is not None else []
    if isinstance(left, dict) and isinstance(right, dict):
        for key in sorted(set(left) | set(right)):
            child = f"{prefix}.{key}" if prefix else key
            if key not in left:
                entries.append(DiffEntry(child, None, right[key], classify_impact(child)))
            elif key not in right:
                entries.append(DiffEntry(child, left[key], None, classify_impact(child)))
            else:
                leaf_diff(left[key], right[key], child, entries)
    elif isinstance(left, list) and isinstance(right, list):
        for idx in range(max(len(left), len(right))):
            child = f"{prefix}[{idx}]"
            if idx >= len(left):
                entries.append(DiffEntry(child, None, right[idx], classify_impact(child)))
            elif idx >= len(right):
                entries.append(DiffEntry(child, left[idx], None, classify_impact(child)))
            else:
                leaf_diff(left[idx], right[idx], child, entries)
    elif left != right:
        entries.append(DiffEntry(prefix, left, right, classify_impact(prefix)))
    return entries
