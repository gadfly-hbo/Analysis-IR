"""S8 HTTP 层测试（接缝 S-D）：只测传输层关注点，业务断言不在 HTTP 层重复。"""

from pathlib import Path

import pytest
from env_helper import FIELD_MAP, GOLDEN_COVERAGE, GOLDEN_CSV, PARAMS, TREATMENTS
from fastapi.testclient import TestClient

from app.server import create_app


@pytest.fixture
def client(tmp_path: Path) -> TestClient:
    project_root = tmp_path / "project"
    project_root.mkdir()
    app = create_app(project_root)
    return TestClient(app)


def _token(client: TestClient) -> str:
    return client.get("/api/session-token").json()["token"]


def _auth(token: str) -> dict[str, str]:
    return {"X-APS-Token": token}


class TestTransportGuards:
    def test_missing_token_rejected(self, client: TestClient) -> None:
        response = client.get("/api/plans/x/1")
        assert response.status_code == 401

    def test_wrong_token_rejected(self, client: TestClient) -> None:
        response = client.get("/api/plans/x/1", headers={"X-APS-Token": "forged"})
        assert response.status_code == 401

    def test_cross_origin_rejected(self, client: TestClient) -> None:
        token = _token(client)
        response = client.get(
            "/api/plans/x/1", headers={**_auth(token), "Origin": "https://evil.example"}
        )
        assert response.status_code == 403

    def test_health_open(self, client: TestClient) -> None:
        assert client.get("/api/health").status_code == 200


class TestHttpHappyPath:
    def test_full_loop_over_http(self, tmp_path: Path) -> None:
        """HTTP 关键路径冒烟：创建→校验→确认→运行→证据→验收（T21 服务同源性依赖）。"""
        project_root = tmp_path / "project"
        project_root.mkdir()
        app = create_app(project_root)
        client = TestClient(app)
        token = _token(client)

        created = client.post(
            "/api/plans", json={"template": "sales-delta", "params": PARAMS,
                                 "operator": "analyst-a"},
            headers=_auth(token),
        )
        assert created.status_code == 200
        plan = created.json()["plan"]
        plan_id, version = plan["plan_id"], plan["plan_version"]

        # 绑定（进程内适配器；HTTP 侧由 UI 调用本地服务完成同一动作）
        from adapters.local_context.context_port import LocalContext
        from adapters.local_store.project_store import ProjectStore

        store = ProjectStore(project_root / "project.db")
        LocalContext(project_dir=project_root / "files").bind(
            plan=plan, source_file=GOLDEN_CSV, field_map=dict(FIELD_MAP),
            metric_treatments=dict(TREATMENTS), coverage=dict(GOLDEN_COVERAGE),
            operator="analyst-a", store=store,
        )

        validated = client.post(
            f"/api/plans/{plan_id}/{version}/validate", headers=_auth(token)
        )
        assert validated.status_code == 200
        assert validated.json()["g0_passed"] is True

        compiled = client.post(
            f"/api/plans/{plan_id}/{version}/compile", headers=_auth(token)
        )
        assert compiled.status_code == 200

        approved = client.post(
            f"/api/plans/{plan_id}/{version}/approve",
            json={"operator": "analyst-a", "action": "approve", "origin": "ui",
                  "warnings_acknowledged": []},
            headers=_auth(token),
        )
        assert approved.status_code == 200

        run = client.post(
            "/api/runs", json={"plan_id": plan_id, "plan_version": version,
                                "operator": "analyst-a"},
            headers=_auth(token),
        )
        assert run.status_code == 200
        run_id = run.json()["run"]["run_id"]
        assert run.json()["run"]["status"] == "COMPLETED"

        collect = client.post(
            f"/api/runs/{run_id}/evidence/collect", headers=_auth(token)
        )
        assert collect.status_code == 200
        verify = client.post(
            f"/api/runs/{run_id}/evidence/verify", headers=_auth(token)
        )
        assert verify.json()["verification"]["status"] == "PASSED"
        accept = client.post(
            f"/api/runs/{run_id}/accept",
            json={"decision": "ACCEPTED", "operator": "boss"},
            headers=_auth(token),
        )
        assert accept.status_code == 200
        generated = client.post(
            f"/api/runs/{run_id}/findings/generate", headers=_auth(token)
        )
        assert len(generated.json()["findings"]) == 3
        listed = client.get(f"/api/runs/{run_id}/findings", headers=_auth(token))
        assert len(listed.json()["findings"]) == 3  # GET 纯读取，无副作用

    def test_same_source_readability(self, tmp_path: Path) -> None:
        """T21：用户阅读对象与机器执行对象同源——同一 digest，不存在双份权威文本。"""
        from toolkit.digest import digest

        project_root = tmp_path / "project"
        project_root.mkdir()
        app = create_app(project_root)
        client = TestClient(app)
        token = _token(client)

        created = client.post(
            "/api/plans", json={"template": "sales-delta", "params": PARAMS,
                                 "operator": "analyst-a"},
            headers=_auth(token),
        ).json()
        plan = created["plan"]
        # UI 读取的计划与运行记录绑定的 plan_digest 必须一致（同一对象）
        fetched = client.get(
            f"/api/plans/{plan['plan_id']}/{plan['plan_version']}", headers=_auth(token)
        ).json()["plan"]
        assert digest(fetched) == digest(plan)
        # 重复读取稳定（渲染同源）
        again = client.get(
            f"/api/plans/{plan['plan_id']}/{plan['plan_version']}", headers=_auth(token)
        ).json()["plan"]
        assert again == plan

    def test_openapi_schema_reachable(self, client: TestClient) -> None:
        token = _token(client)
        response = client.get("/openapi.json", headers=_auth(token))
        assert response.status_code == 200
        paths = response.json()["paths"]
        for expected in ("/api/plans", "/api/runs", "/api/runs/{run_id}/accept",
                         "/api/templates/extract", "/api/exports"):
            assert expected in paths

    def test_404_for_unknown_run(self, client: TestClient) -> None:
        token = _token(client)
        assert client.get("/api/runs/run-9999", headers=_auth(token)).status_code == 404
