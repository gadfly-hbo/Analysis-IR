"""本地语义绑定适配器（ContextPort 参考实现，proposal 10.1）。

职责：导入本地数据并冻结为项目快照（内容指纹）、校验字段/币种/日期、
产生 BindingSnapshot 与 MetricContract（口径未确认项记录为警告，不静默定值）。
"""

from __future__ import annotations

import csv
import shutil
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from adapters.local_store.project_store import ProjectStore
from toolkit.contracts import validate_object
from toolkit.digest import digest_bytes


class BindingError(Exception):
    """绑定失败（可定位）。code/location/message 三元组对应 G0/G1 呈现。"""

    def __init__(self, code: str, location: str, message: str) -> None:
        self.code = code
        self.location = location
        super().__init__(f"[{code}] {location}: {message}")


@dataclass(frozen=True)
class BindResult:
    manifest: dict[str, Any]
    binding: dict[str, Any]
    metric: dict[str, Any]
    warnings: list[str] = field(default_factory=list)


class LocalContext:
    def __init__(self, project_dir: Path) -> None:
        self.project_dir = project_dir

    # ---------- 快照 ----------

    def _snapshot_dir(self, dataset_id: str) -> Path:
        d = self.project_dir / "snapshots" / dataset_id
        d.mkdir(parents=True, exist_ok=True)
        return d

    def import_snapshot(
        self, source_file: Path, dataset_id: str, parse_config: dict[str, Any]
    ) -> tuple[Path, str, int]:
        """复制源文件为项目快照，返回 (快照路径, 字节指纹, 行数)。"""
        snapshot_path = self._snapshot_dir(dataset_id) / "data.csv"
        shutil.copyfile(source_file, snapshot_path)
        byte_digest = digest_bytes(snapshot_path.read_bytes())
        with snapshot_path.open(encoding=parse_config["encoding"], newline="") as f:
            row_count = sum(1 for _ in csv.DictReader(f))
        return snapshot_path, byte_digest, row_count

    def verify_snapshot(self, manifest: dict[str, Any]) -> bool:
        """核验快照文件当前字节指纹与清单一致（T11）。"""
        path = Path(manifest["snapshot_path"])
        if not path.exists():
            return False
        return digest_bytes(path.read_bytes()) == manifest["byte_digest"]

    # ---------- 绑定 ----------

    def bind(
        self,
        plan: dict[str, Any],
        source_file: Path,
        field_map: dict[str, str],
        metric_treatments: dict[str, str],
        coverage: dict[str, Any],
        operator: str,
        store: ProjectStore,
        store_eligibility: dict[str, Any] | None = None,
    ) -> BindResult:
        warnings: list[str] = []
        dataset_ref = plan["dataset_refs"][0]
        dataset_id = dataset_ref["id"]
        binding_id = plan["binding_ref"]["id"]
        parse_config = {
            "delimiter": ",",
            "encoding": "utf-8",
            "date_format": "%Y-%m-%d",
            "decimal_places": 2,
        }

        snapshot_path, byte_digest, row_count = self.import_snapshot(
            source_file, dataset_id, parse_config
        )
        header, rows = self._read_rows(snapshot_path, parse_config)

        self._check_fields(header, field_map)
        self._check_dates(rows, field_map, parse_config)
        currency = self._check_currency(rows, field_map)

        # 指标口径：未提供的维度记为 unconfirmed，不静默定值（T03）
        treatments = {
            "tax": metric_treatments.get("tax", "unconfirmed"),
            "discounts": metric_treatments.get("discounts", "unconfirmed"),
            "returns_attribution": metric_treatments.get(
                "returns_attribution", "unconfirmed"
            ),
        }
        for key, value in treatments.items():
            if value == "unconfirmed":
                warnings.append(f"metric treatment unconfirmed: {key}")

        if coverage.get("kind") == "unverified":
            warnings.append("coverage unverified: 无覆盖证明，不得把缺记录当零销售")

        scope = self._build_scope(store_eligibility)

        manifest = {
            "schema_version": "1.0.0",
            "id": dataset_id,
            "version": dataset_ref["version"],
            "source_file": source_file.name,
            "snapshot_path": str(snapshot_path),
            "byte_digest": byte_digest,
            "row_count": row_count,
            "parse_config": parse_config,
            "coverage": coverage,
            "snapshot_date": datetime.now().strftime("%Y-%m-%d"),
        }
        binding = {
            "schema_version": "1.0.0",
            "id": binding_id,
            "version": plan["binding_ref"]["version"],
            "dataset_ref": {"id": dataset_id, "version": dataset_ref["version"]},
            "metric_refs": [{"id": m["id"], "version": m["version"]} for m in plan["metric_refs"]],
            "field_map": {k: v for k, v in field_map.items() if k != "currency"},
            "dimensions": [
                {"name": "store_id", "source_field": field_map["store_id"]},
                {"name": "category_id", "source_field": field_map["category_id"]},
            ],
            "scope": scope,
        }
        metric = {
            "schema_version": "1.0.0",
            "id": plan["metric_refs"][0]["id"],
            "version": plan["metric_refs"][0]["version"],
            "name": "净销售额",
            "formula": f"sum({field_map['net_sales_amount']})",
            "unit": "currency",
            "grain": "row",
            "treatments": treatments,
            "currency": currency,
        }

        validate_object("data-snapshot-manifest", manifest)
        validate_object("binding-snapshot", binding)
        validate_object("metric-contract", metric)

        now = datetime.now().astimezone().isoformat()
        store.put("data-snapshot-manifest", manifest, created_at=now)
        store.put("binding-snapshot", binding, created_at=now)
        store.put("metric-contract", metric, created_at=now)
        store.audit(now, operator, "bind", f"{binding_id}@{binding['version']}")
        return BindResult(manifest, binding, metric, warnings)

    # ---------- 内部校验 ----------

    @staticmethod
    def _read_rows(
        path: Path, parse_config: dict[str, Any]
    ) -> tuple[list[str], list[dict[str, str]]]:
        with path.open(encoding=parse_config["encoding"], newline="") as f:
            reader = csv.DictReader(f)
            header = list(reader.fieldnames or [])
            rows = list(reader)
        return header, rows

    @staticmethod
    def _check_fields(header: list[str], field_map: dict[str, str]) -> None:
        for logical in ("row_id", "business_date", "store_id", "category_id", "net_sales_amount"):
            physical = field_map.get(logical)
            if not physical or physical not in header:
                raise BindingError(
                    "MISSING_FIELD",
                    f"field_map.{logical}",
                    f"必需字段缺失或未映射: {logical} -> {physical}",
                )

    @staticmethod
    def _check_dates(
        rows: list[dict[str, str]], field_map: dict[str, str], parse_config: dict[str, Any]
    ) -> None:
        date_field = field_map["business_date"]
        fmt = parse_config["date_format"]
        for idx, row in enumerate(rows):
            try:
                datetime.strptime(row[date_field], fmt)
            except (ValueError, KeyError) as exc:
                raise BindingError(
                    "DATE_PARSE",
                    f"row[{idx}].{date_field}",
                    f"日期无法按 {fmt} 解析: {row.get(date_field)!r}",
                ) from exc

    @staticmethod
    def _check_currency(rows: list[dict[str, str]], field_map: dict[str, str]) -> str:
        currency_field = field_map.get("currency")
        if currency_field is None:
            return "CNY"  # 无币种列时使用项目配置（G8：单一币种项目）
        values = {row[currency_field] for row in rows if row.get(currency_field)}
        if len(values) > 1:
            raise BindingError(
                "MIXED_CURRENCY",
                f"field_map.{currency_field}",
                f"混合货币且无换算契约: {sorted(values)}",
            )
        return values.pop() if values else "CNY"

    @staticmethod
    def _build_scope(store_eligibility: dict[str, Any] | None) -> dict[str, Any]:
        # T08：无资格清单 → 全量观察范围；有清单 → 同店口径必须带规则说明
        if not store_eligibility:
            return {"mode": "all-stores", "label": "全量观察范围", "same_store": False}
        list_file = Path(store_eligibility["list_file"])
        with list_file.open(encoding="utf-8", newline="") as f:
            stores = [row["store_id"] for row in csv.DictReader(f) if row.get("store_id")]
        if not stores:
            raise BindingError("EMPTY_ELIGIBILITY", "store_eligibility", "门店资格清单为空")
        return {
            "mode": "declared-list",
            "label": "同店范围（用户提供资格清单）",
            "same_store": True,
            "eligible_stores": stores,
            "rule_note": store_eligibility.get("rule_note", ""),
        }
