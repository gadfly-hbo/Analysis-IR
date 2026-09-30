"""契约注册表：加载 contracts/schemas 下的 JSON Schema（Draft 2020-12）并校验对象。

契约权威是 contracts/ 下的 Schema 文件（PRD 决策 5，schema-first）；
本模块只做加载与结构校验。proposal 7.2：业务语义/依赖/引用完整性由
后续规则检查器负责，"Schema 合法"不等于"分析正确"。
"""

from __future__ import annotations

import json
import os
from functools import lru_cache
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker
from referencing import Registry, Resource
from referencing.jsonschema import DRAFT202012

# 对象种类 → Schema 文件名（不含公共定义文件 common）
OBJECT_KINDS: dict[str, str] = {
    "analysis-contract": "analysis-contract-1.0.0.json",
    "metric-contract": "metric-contract-1.0.0.json",
    "binding-snapshot": "binding-snapshot-1.0.0.json",
    "data-snapshot-manifest": "data-snapshot-manifest-1.0.0.json",
    "analysis-plan-ir": "analysis-plan-ir-1.0.0.json",
    "execution-manifest": "execution-manifest-1.0.0.json",
    "approval": "approval-1.0.0.json",
    "run-record": "run-record-1.0.0.json",
    "evidence-bundle": "evidence-bundle-1.0.0.json",
    "finding": "finding-1.0.0.json",
    "plan-template": "plan-template-1.0.0.json",
    "change-request": "change-request-1.0.0.json",
}

_SUPPORTED_SCHEMA_VERSION = "1.0.0"


class ContractViolation(Exception):
    """对象不符合其契约 Schema。message 携带可定位的字段路径。"""

    def __init__(self, kind: str, errors: list[str]) -> None:
        self.kind = kind
        self.errors = errors
        super().__init__(f"[{kind}] " + "; ".join(errors))


def _schemas_dir() -> Path:
    override = os.environ.get("AIR_CONTRACTS_DIR")
    if override:
        return Path(override)
    return Path(__file__).resolve().parents[1] / "contracts" / "schemas"


def _load_registry() -> tuple[dict[str, dict[str, Any]], Registry]:
    schemas: dict[str, dict[str, Any]] = {}
    resources: list[Resource] = []
    for path in sorted(_schemas_dir().glob("*.json")):
        doc = json.loads(path.read_text(encoding="utf-8"))
        schemas[path.name] = doc
        resources.append(Resource.from_contents(doc, default_specification=DRAFT202012))
    registry: Registry = Registry().with_resources(
        (str(r.contents["$id"]), r) for r in resources
    )
    return schemas, registry


@lru_cache(maxsize=1)
def _validators() -> dict[str, Draft202012Validator]:
    schemas, registry = _load_registry()
    checker = FormatChecker()  # format 断言开启（proposal 7.2 要求真实校验 format）
    validators: dict[str, Draft202012Validator] = {}
    for kind, filename in OBJECT_KINDS.items():
        if filename not in schemas:
            raise FileNotFoundError(f"缺少契约 Schema: {filename}")
        validators[kind] = Draft202012Validator(
            schemas[filename], registry=registry, format_checker=checker
        )
    return validators


def validate_object(kind: str, obj: dict[str, Any]) -> None:
    """按 kind 对应 Schema 校验对象；不合规则抛 ContractViolation（含字段路径）。

    未知主版本由 schema_version 的 const 拒绝（不允许隐式升级）。
    """
    if kind not in OBJECT_KINDS:
        raise ContractViolation(kind, [f"未知对象种类: {kind}"])
    validator = _validators()[kind]
    errors = sorted(validator.iter_errors(obj), key=lambda e: list(e.absolute_path))
    if errors:
        messages = []
        for err in errors:
            location = "/".join(str(p) for p in err.absolute_path) or "<root>"
            messages.append(f"{location}: {err.message}")
        raise ContractViolation(kind, messages)
