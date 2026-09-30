"""导出/导入（proposal 10.3 G4）：三种交付包；压缩包按不可信输入处理。"""

from __future__ import annotations

import json
import zipfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any

from adapters.local_store.project_store import ProjectStore
from toolkit.digest import digest, digest_bytes

_ALLOWED_EXTENSIONS = {".json", ".csv", ".parquet", ".md"}


class ExportError(Exception):
    pass


def _now() -> str:
    return datetime.now(UTC).isoformat()


@dataclass(frozen=True)
class ImportReport:
    imported_kinds: list[str]
    plan_status: str
    note: str


class ExportService:
    def __init__(self, store: ProjectStore, project_dir: Path) -> None:
        self.store = store
        self.project_dir = project_dir

    # ---------- 导出 ----------

    def build_package(
        self,
        plan_id: str,
        plan_version: int,
        mode: str,
        operator: str,
        include_results: bool = False,
        include_data: bool = False,
    ) -> Path:
        """handover（默认无数据）/ delivery（+结果与证据）/ reproduction（+快照数据）。

        include_results / include_data 是显式确认位（proposal 10.3：导出前明确预览与确认）。
        """
        if mode not in ("handover", "delivery", "reproduction"):
            raise ExportError(f"未知导出模式: {mode}")
        if mode == "handover" and (include_results or include_data):
            raise ExportError("交接包不携带结果或原始数据")
        if mode == "reproduction" and not include_data:
            raise ExportError("复现包必须显式确认包含数据快照（include_data=True）")

        plan = self.store.get("analysis-plan-ir", plan_id, plan_version)
        if plan is None:
            raise ExportError(f"计划不存在: {plan_id}@{plan_version}")
        contract = self.store.get(
            "analysis-contract", plan["contract_ref"]["id"], plan["contract_ref"]["version"]
        )
        metric = self.store.get(
            "metric-contract", plan["metric_refs"][0]["id"], plan["metric_refs"][0]["version"]
        )
        binding = self.store.get(
            "binding-snapshot", plan["binding_ref"]["id"], plan["binding_ref"]["version"]
        )
        if contract is None or metric is None or binding is None:
            raise ExportError("计划引用对象缺失，无法导出")
        manifest = self.store.get(
            "data-snapshot-manifest",
            plan["dataset_refs"][0]["id"], plan["dataset_refs"][0]["version"],
        )
        assert manifest is not None

        files: dict[str, bytes] = {}
        files["objects/analysis-contract.json"] = _dump(contract)
        files["objects/analysis-plan-ir.json"] = _dump(plan)
        files["objects/metric-contract.json"] = _dump(metric)
        files["objects/binding-snapshot.json"] = _dump(binding)
        # 路径治理：交接内容不带本机绝对路径（proposal 10.3）
        files["objects/data-snapshot-manifest.json"] = _dump(_strip_paths(manifest))
        files["objects/method-registry.json"] = _dump(
            self._latest("method-registry", "sales-methods")
        )
        files["objects/check-registry.json"] = _dump(
            self._latest("check-registry", "sales-checks")
        )

        if include_results:
            for run in self.store.list_objects("run-record"):
                if run["plan_id"] == plan_id and run["plan_version"] == plan_version:
                    files[f"runs/{run['run_id']}/run-record.json"] = _dump(run)
                    bundle = self.store.get("evidence-bundle", f"evidence-{run['run_id']}", 1)
                    if bundle:
                        files[f"runs/{run['run_id']}/evidence-bundle.json"] = _dump(bundle)
                        for item in bundle["items"]:
                            artifact_path = self.project_dir / item["artifact"]
                            if artifact_path.exists():
                                files[f"runs/{run['run_id']}/artifacts/"
                                      f"{artifact_path.name}"] = artifact_path.read_bytes()
                    verification = self.store.get_kv(f"verification:{run['run_id']}")
                    acceptance = self.store.get_kv(f"acceptance:{run['run_id']}")
                    if verification:
                        files[f"runs/{run['run_id']}/verification.json"] = verification.encode()
                    if acceptance:
                        files[f"runs/{run['run_id']}/acceptance.json"] = acceptance.encode()
                    for finding in self.store.list_objects("finding"):
                        if finding["run_id"] == run["run_id"]:
                            files[f"runs/{run['run_id']}/findings/"
                                  f"{finding['finding_id']}.json"] = _dump(finding)

        if include_data:
            snapshot_path = Path(manifest["snapshot_path"])
            if not snapshot_path.exists():
                raise ExportError("快照文件缺失，无法构建复现包")
            files["data/snapshot.csv"] = snapshot_path.read_bytes()

        package_manifest = {
            "format_version": "1.0.0",
            "mode": mode,
            "plan": f"{plan_id}@{plan_version}",
            "plan_digest": digest(plan),
            "created_at": _now(),
            "includes": {"results": include_results, "data": include_data},
            "entries": [
                {"path": name, "sha256": digest_bytes(payload)}
                for name, payload in sorted(files.items())
            ],
        }
        files["manifest.json"] = _dump(package_manifest)

        export_dir = self.project_dir / "exports"
        export_dir.mkdir(parents=True, exist_ok=True)
        package_path = export_dir / f"{plan_id}@{plan_version}-{mode}.zip"
        with zipfile.ZipFile(package_path, "w", zipfile.ZIP_DEFLATED) as zf:
            for name, payload in sorted(files.items()):
                zf.writestr(name, payload)
        self.store.audit(_now(), operator, f"export:{mode}",
                         f"{plan_id}@{plan_version} -> {package_path.name}")
        return package_path

    def _latest(self, kind: str, obj_id: str) -> dict[str, Any]:
        version = self.store.latest_version(kind, obj_id)
        doc = self.store.get(kind, obj_id, version) if version else None
        if doc is None:
            raise ExportError(f"注册表缺失: {kind}/{obj_id}")
        return doc

    # ---------- 导入 ----------

    def import_package(self, package_path: Path, operator: str) -> ImportReport:
        """导入核验：版本、摘要、路径穿越、可执行内容。压缩包是不可信输入（T20）。"""
        if not package_path.exists():
            raise ExportError(f"包不存在: {package_path}")
        try:
            zf = zipfile.ZipFile(package_path)
        except zipfile.BadZipFile as exc:
            raise ExportError(f"非法压缩包: {exc}") from exc

        names = zf.namelist()
        for name in names:
            pure = PurePosixPath(name)
            if pure.is_absolute() or ".." in pure.parts or name.startswith("/"):
                raise ExportError(f"路径越界条目被拒绝: {name}")
            if pure.suffix.lower() not in _ALLOWED_EXTENSIONS:
                raise ExportError(f"不允许的文件类型（防携带代码）: {name}")

        if "manifest.json" not in names:
            raise ExportError("缺少 manifest.json")
        package_manifest = json.loads(zf.read("manifest.json"))
        if package_manifest["format_version"] != "1.0.0":
            raise ExportError(f"不支持的包版本: {package_manifest['format_version']}")

        for entry in package_manifest["entries"]:
            payload = zf.read(entry["path"])
            if digest_bytes(payload) != entry["sha256"]:
                raise ExportError(f"摘要不符: {entry['path']}")

        imported_kinds: list[str] = []
        plan_id, plan_status = "", "NEEDS_INPUT"
        for name in sorted(names):
            if not name.startswith("objects/") or not name.endswith(".json"):
                continue
            kind = PurePosixPath(name).stem
            doc = json.loads(zf.read(name))
            existing = self.store.get(
                kind, doc.get("id") or doc.get("plan_id"), doc.get("version", 1)
            )
            if existing is None:
                self.store.put(kind, doc, created_at=_now())
                imported_kinds.append(kind)
                if kind == "analysis-plan-ir":
                    plan_id = doc["plan_id"]
        if plan_id:
            self.store.set_status(plan_id, 1, "NEEDS_INPUT", _now())
        self.store.audit(_now(), operator, "import_package", package_path.name)
        return ImportReport(
            imported_kinds=imported_kinds,
            plan_status=plan_status,
            note="导入对象需重新绑定与确认；原批准记录不自动授权新执行（T20）",
        )


def _dump(obj: Any) -> bytes:
    return json.dumps(obj, ensure_ascii=False, indent=2).encode("utf-8")


def _strip_paths(manifest: dict[str, Any]) -> dict[str, Any]:
    stripped = dict(manifest)
    stripped.pop("snapshot_path", None)
    return stripped
