"""本地 HTTP 服务（S8）：回环绑定 + 会话令牌 + 来源检查（proposal 10.5）。

薄壳原则：只做传输层关注点（认证、来源、状态码），业务规则全部在
toolkit 服务层，不在 HTTP 层重复（测试接缝 S-D）。
"""

from __future__ import annotations

import secrets
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse

from adapters.local_context.context_port import LocalContext
from adapters.local_store.project_store import ProjectStore
from toolkit.approval_service import ApprovalService
from toolkit.evidence_service import EvidenceError, EvidenceService
from toolkit.execution import compile_execution_manifest
from toolkit.export_service import ExportService
from toolkit.findings import generate_findings
from toolkit.plan_service import PlanService
from toolkit.run_controller import RunController
from toolkit.template_service import TemplateService

_ALLOWED_ORIGIN_HOSTS = {"127.0.0.1", "localhost"}


def create_app(project_root: Path) -> FastAPI:
    """project_root: 项目目录（含 project.db 与 files/）。"""
    app = FastAPI(title="Analysis Plan Studio", version="0.1.0")
    store = ProjectStore(project_root / "project.db")
    files_dir = project_root / "files"
    plan_service = PlanService(store)
    plan_service.register_builtin_registries()
    context = LocalContext(project_dir=files_dir)
    approval_service = ApprovalService(store, plan_service, context)
    controller = RunController(store, plan_service, approval_service, context, files_dir)
    evidence_service = EvidenceService(store, files_dir)
    template_service = TemplateService(store, plan_service)
    export_service = ExportService(store, files_dir)
    session_token = secrets.token_urlsafe(32)

    @app.middleware("http")
    async def guard(request: Request, call_next: Any) -> Any:
        # 来源检查：携带 Origin 的请求（浏览器跨站场景）只允许本机来源（proposal 10.5）
        origin = request.headers.get("origin")
        if origin:
            origin_host = origin.split("//", 1)[-1].split(":")[0].rstrip("/")
            if origin_host not in _ALLOWED_ORIGIN_HOSTS:
                return JSONResponse({"error": "origin-not-allowed"}, status_code=403)
        auth = request.headers.get("x-aps-token", "")
        open_paths = {"/api/health", "/api/session-token"}
        if request.url.path not in open_paths and not secrets.compare_digest(
            auth, session_token
        ):
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        return await call_next(request)

    @app.get("/api/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/api/session-token")
    def get_token(request: Request) -> dict[str, str]:
        # 本地启动器通过回环读取令牌后注入前端；仅本机可访问（uvicorn 绑定 127.0.0.1）
        if request.client and request.client.host not in ("127.0.0.1", "::1", "testclient"):
            raise HTTPException(403, "local-only")
        return {"token": session_token}

    @app.post("/api/plans")
    def create_plan(body: dict[str, Any]) -> dict[str, Any]:
        result = plan_service.create_draft(
            body.get("template", "sales-delta"), body.get("params", {}), body["operator"]
        )
        return {
            "plan": result.plan,
            "contract": result.contract,
            "unresolved": result.unresolved,
            "status": plan_service.get_status(result.plan["plan_id"], 1),
        }

    @app.get("/api/plans")
    def list_plans() -> dict[str, Any]:
        plans = []
        for plan in store.list_objects("analysis-plan-ir"):
            plans.append({
                "plan_id": plan["plan_id"],
                "plan_version": plan["plan_version"],
                "status": store.get_status(plan["plan_id"], plan["plan_version"]) or "DRAFT",
            })
        return {"plans": plans}

    @app.post("/api/bindings")
    def create_binding(body: dict[str, Any]) -> dict[str, Any]:
        plan = plan_service.get_plan(body["plan_id"], body["plan_version"])
        source = Path(body["source_file"]).expanduser()
        if not source.exists():
            raise HTTPException(422, f"数据文件不存在: {source}")
        eligibility = body.get("store_eligibility")
        if eligibility:
            eligibility = {
                "list_file": Path(eligibility["list_file"]).expanduser(),
                "rule_note": eligibility.get("rule_note", ""),
            }
        result = context.bind(
            plan=plan,
            source_file=source,
            field_map=body.get("field_map") or {
                "row_id": "row_id", "business_date": "business_date",
                "store_id": "store_id", "category_id": "category_id",
                "net_sales_amount": "net_sales_amount", "currency": "currency",
            },
            metric_treatments=body.get("metric_treatments") or {},
            coverage=body.get("coverage") or {"kind": "unverified"},
            operator=body["operator"],
            store=store,
            store_eligibility=eligibility,
        )
        return {"manifest": result.manifest, "binding": result.binding,
                "warnings": result.warnings}

    @app.get("/api/plans/{plan_id}/{version}")
    def get_plan(plan_id: str, version: int) -> dict[str, Any]:
        try:
            return {"plan": plan_service.get_plan(plan_id, version)}
        except KeyError as exc:
            raise HTTPException(404, str(exc)) from exc

    @app.post("/api/plans/{plan_id}/{version}/validate")
    def validate_plan(plan_id: str, version: int) -> dict[str, Any]:
        report = plan_service.validate(plan_id, version)
        return {
            "g0_passed": report.g0_passed,
            "status": plan_service.get_status(plan_id, version),
            "issues": [i.__dict__ for i in report.issues],
        }

    @app.post("/api/plans/{plan_id}/{version}/approve")
    def approve_plan(plan_id: str, version: int, body: dict[str, Any]) -> dict[str, Any]:
        try:
            approval = approval_service.approve(plan_id, version, body)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        except Exception as exc:  # noqa: BLE001 - 门禁拒绝 → 409
            raise HTTPException(409, str(exc)) from exc
        return {"approval": approval}

    @app.get("/api/plans/{plan_id}/diff/{va}/{vb}")
    def diff_plans(plan_id: str, va: int, vb: int) -> dict[str, Any]:
        entries = plan_service.diff(plan_id, va, vb)
        return {"entries": [e.__dict__ for e in entries]}

    @app.post("/api/plans/{plan_id}/{version}/compile")
    def compile_plan(plan_id: str, version: int) -> dict[str, Any]:
        try:
            manifest = compile_execution_manifest(
                store, plan_service.get_plan(plan_id, version)
            )
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc
        return {"execution_manifest": {k: v for k, v in manifest.items()}}

    @app.post("/api/runs")
    def start_run(body: dict[str, Any]) -> dict[str, Any]:
        record = controller.start(body["plan_id"], body["plan_version"], body["operator"])
        return {"run": record}

    @app.get("/api/runs/{run_id}")
    def get_run(run_id: str) -> dict[str, Any]:
        record = store.get("run-record", run_id, 1)
        if record is None:
            raise HTTPException(404, f"run not found: {run_id}")
        return {"run": record}

    @app.post("/api/runs/{run_id}/evidence/collect")
    def collect_evidence(run_id: str) -> dict[str, Any]:
        try:
            bundle = evidence_service.collect(run_id)
        except EvidenceError as exc:
            raise HTTPException(404, str(exc)) from exc
        return {"bundle": bundle}

    @app.post("/api/runs/{run_id}/evidence/verify")
    def verify_evidence(run_id: str) -> dict[str, Any]:
        try:
            result = evidence_service.verify(run_id)
        except EvidenceError as exc:
            raise HTTPException(404, str(exc)) from exc
        return {
            "verification": {"status": result.status, "checks": result.checks,
                             "limitations": result.limitations},
        }

    @app.post("/api/runs/{run_id}/accept")
    def accept_run(run_id: str, body: dict[str, Any]) -> dict[str, Any]:
        try:
            record = evidence_service.accept(
                run_id, body["decision"], body["operator"], body.get("note", "")
            )
        except EvidenceError as exc:
            raise HTTPException(409, str(exc)) from exc
        return {"acceptance": record}

    @app.get("/api/runs/{run_id}/findings")
    def run_findings(run_id: str) -> dict[str, Any]:
        # 纯读取：生成走显式 POST，避免 GET 副作用
        findings = [f for f in store.list_objects("finding") if f["run_id"] == run_id]
        return {"findings": findings}

    @app.post("/api/runs/{run_id}/findings/generate")
    def generate_run_findings(run_id: str) -> dict[str, Any]:
        record = store.get("run-record", run_id, 1)
        if record is None:
            raise HTTPException(404, f"run not found: {run_id}")
        plan = store.get("analysis-plan-ir", record["plan_id"], record["plan_version"])
        if plan is None:
            raise HTTPException(409, "计划对象缺失")
        try:
            findings = generate_findings(
                store, files_dir, run_id,
                list(plan["output_contract"]["allowed_finding_types"]),
            )
        except FileNotFoundError as exc:
            raise HTTPException(409, f"结果产物缺失（运行未完成）: {exc}") from exc
        return {"findings": findings}

    @app.post("/api/runs/{run_id}/to-verify")
    def add_to_verify_finding(run_id: str, body: dict[str, Any]) -> dict[str, Any]:
        from toolkit.findings import add_to_verify

        if store.get("run-record", run_id, 1) is None:
            raise HTTPException(404, f"run not found: {run_id}")
        finding = add_to_verify(store, run_id, body["text"], body.get("operator", "analyst-a"))
        return {"finding": finding}

    @app.post("/api/changes")
    def propose_change(body: dict[str, Any]) -> dict[str, Any]:
        from toolkit.change_service import ChangeService

        cr = ChangeService(store, plan_service).propose(
            plan_id=body["plan_id"], plan_version=body["plan_version"],
            reason=body["reason"], proposed_by=body.get("proposed_by", "user"),
            changes=body["changes"],
        )
        return {"change_request": cr}

    @app.post("/api/changes/{cr_id}/merge")
    def merge_change(cr_id: str, body: dict[str, Any]) -> dict[str, Any]:
        from toolkit.change_service import ChangeService

        try:
            merged = ChangeService(store, plan_service).merge(cr_id, body["operator"])
        except (KeyError, ValueError) as exc:
            raise HTTPException(409, str(exc)) from exc
        return {"merged": merged}

    @app.post("/api/templates/extract")
    def extract_template(body: dict[str, Any]) -> dict[str, Any]:
        template = template_service.extract(
            body["plan_id"], body["plan_version"], body["name"], body["operator"]
        )
        return {"template": template}

    @app.post("/api/templates/{template_id}/instantiate")
    def instantiate_template(template_id: str, body: dict[str, Any]) -> dict[str, Any]:
        try:
            result = template_service.instantiate(
                template_id, body["params"], body["operator"]
            )
        except KeyError as exc:
            raise HTTPException(404, str(exc)) from exc
        return {"plan": result.plan, "unresolved": result.unresolved}

    @app.post("/api/exports")
    def export_package(body: dict[str, Any]) -> dict[str, Any]:
        try:
            path = export_service.build_package(
                body["plan_id"], body["plan_version"], body["mode"], body["operator"],
                include_results=body.get("include_results", False),
                include_data=body.get("include_data", False),
            )
        except Exception as exc:  # noqa: BLE001 - 导出拒绝 → 409
            raise HTTPException(409, str(exc)) from exc
        return {"package": str(path)}

    # 本地界面：托管已构建的前端产物（app/web/dist）
    web_dist = Path(__file__).parent / "web" / "dist"
    if web_dist.exists():
        from fastapi.staticfiles import StaticFiles

        app.mount("/", StaticFiles(directory=web_dist, html=True), name="web")

    return app
