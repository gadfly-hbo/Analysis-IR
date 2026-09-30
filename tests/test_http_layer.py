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

class TestChangeRequestHttp:
    def _approved_env(self, tmp_path: Path):
        from adapters.local_context.context_port import LocalContext
        from adapters.local_store.project_store import ProjectStore

        project_root = tmp_path / "project"
        project_root.mkdir(exist_ok=True)
        app = create_app(project_root)
        client = TestClient(app)
        token = _token(client)
        created = client.post(
            "/api/plans", json={"template": "sales-delta", "params": PARAMS,
                                 "operator": "analyst-a"},
            headers=_auth(token),
        ).json()
        plan = created["plan"]
        store = ProjectStore(project_root / "project.db")
        LocalContext(project_dir=project_root / "files").bind(
            plan=plan, source_file=GOLDEN_CSV, field_map=dict(FIELD_MAP),
            metric_treatments=dict(TREATMENTS), coverage=dict(GOLDEN_COVERAGE),
            operator="analyst-a", store=store,
        )
        client.post(f"/api/plans/{plan['plan_id']}/1/validate", headers=_auth(token))
        client.post(f"/api/plans/{plan['plan_id']}/1/compile", headers=_auth(token))
        client.post(
            f"/api/plans/{plan['plan_id']}/1/approve",
            json={"operator": "analyst-a", "action": "approve", "origin": "ui",
                  "warnings_acknowledged": []},
            headers=_auth(token),
        )
        return client, token, plan

    def test_cr_valid_value_merges_to_new_version(self, tmp_path: Path) -> None:
        client, token, plan = self._approved_env(tmp_path)
        proposed = client.post(
            "/api/changes",
            json={"plan_id": plan["plan_id"], "plan_version": 1, "reason": "基期提前",
                  "changes": [{"path": "contract.comparison_scope.base_period.start",
                               "old": "2026-07-01", "new": "2026-07-08",
                               "impact": "scope"}]},
            headers=_auth(token),
        )
        assert proposed.status_code == 200
        cr_id = proposed.json()["change_request"]["cr_id"]
        merged = client.post(f"/api/changes/{cr_id}/merge",
                             json={"operator": "analyst-a"}, headers=_auth(token))
        assert merged.status_code == 200
        assert merged.json()["merged"]["merged_plan_version"] == 2
        diffs = client.get(f"/api/plans/{plan['plan_id']}/diff/1/2", headers=_auth(token))
        assert any("base_period.start" in e["path"] for e in diffs.json()["entries"])

    def test_cr_invalid_value_rejected_422(self, tmp_path: Path) -> None:
        client, token, plan = self._approved_env(tmp_path)
        proposed = client.post(
            "/api/changes",
            json={"plan_id": plan["plan_id"], "plan_version": 1, "reason": "坏值",
                  "changes": [{"path": "contract.comparison_scope.base_period.start",
                               "old": "2026-07-01", "new": "not-a-date",
                               "impact": "scope"}]},
            headers=_auth(token),
        )
        cr_id = proposed.json()["change_request"]["cr_id"]
        merged = client.post(f"/api/changes/{cr_id}/merge",
                             json={"operator": "analyst-a"}, headers=_auth(token))
        assert merged.status_code == 422  # 不再是 500

    def test_to_verify_empty_text_422(self, tmp_path: Path) -> None:
        client, token, plan = self._approved_env(tmp_path)
        run = client.post("/api/runs",
                          json={"plan_id": plan["plan_id"], "plan_version": 1,
                                "operator": "analyst-a"},
                          headers=_auth(token)).json()["run"]
        response = client.post(f"/api/runs/{run['run_id']}/to-verify",
                               json={"text": "  "}, headers=_auth(token))
        assert response.status_code == 422

class TestR3InputValidation:
    def test_create_plan_bad_date_returns_422(self, tmp_path: Path) -> None:
        project_root = tmp_path / "project"
        project_root.mkdir()
        client = TestClient(create_app(project_root))
        token = _token(client)
        bad_params = dict(PARAMS)
        bad_params["base_period"] = {"start": "not-a-date", "end": "2026-07-31"}
        response = client.post(
            "/api/plans", json={"template": "sales-delta", "params": bad_params,
                                 "operator": "analyst-a"},
            headers=_auth(token),
        )
        assert response.status_code == 422
        assert "base_period" in response.text

    def test_create_plan_start_after_end_returns_422(self, tmp_path: Path) -> None:
        project_root = tmp_path / "project"
        project_root.mkdir()
        client = TestClient(create_app(project_root))
        token = _token(client)
        bad_params = dict(PARAMS)
        bad_params["base_period"] = {"start": "2026-08-31", "end": "2026-07-01"}
        assert client.post(
            "/api/plans", json={"template": "sales-delta", "params": bad_params,
                                 "operator": "analyst-a"},
            headers=_auth(token),
        ).status_code == 422

    def test_binding_error_maps_to_422(self, tmp_path: Path) -> None:
        project_root = tmp_path / "project"
        project_root.mkdir()
        client = TestClient(create_app(project_root))
        token = _token(client)
        created = client.post(
            "/api/plans", json={"template": "sales-delta", "params": PARAMS,
                                 "operator": "analyst-a"},
            headers=_auth(token),
        ).json()
        # 数据文件存在但缺 store_id 列 → BindingError → 422（不再是 500）
        broken = tmp_path / "broken.csv"
        lines = GOLDEN_CSV.read_text().splitlines()
        header = [c for c in lines[0].split(",") if c != "store_id"]
        body_lines = [
            ",".join([v for i, v in enumerate(line.split(",")) if i != 2])
            for line in lines[1:]
        ]
        broken.write_text("\n".join([",".join(header)] + body_lines) + "\n")
        response = client.post(
            "/api/bindings",
            json={"plan_id": created["plan"]["plan_id"], "plan_version": 1,
                  "source_file": str(broken), "metric_treatments": dict(TREATMENTS),
                  "coverage": {"kind": "unverified"}, "operator": "analyst-a"},
            headers=_auth(token),
        )
        assert response.status_code == 422
        assert "store_id" in response.text

    def test_change_withdraw_records_state(self, tmp_path: Path) -> None:
        env = TestChangeRequestHttp()
        client, token, plan = env._approved_env(tmp_path)
        proposed = client.post(
            "/api/changes",
            json={"plan_id": plan["plan_id"], "plan_version": 1, "reason": "r",
                  "changes": [{"path": "contract.comparison_scope.base_period.start",
                               "old": "2026-07-01", "new": "2026-07-08",
                               "impact": "scope"}]},
            headers=_auth(token),
        ).json()["change_request"]["cr_id"]
        withdrawn = client.post(f"/api/changes/{proposed}/withdraw",
                                json={"operator": "analyst-a"}, headers=_auth(token))
        assert withdrawn.status_code == 200
        assert withdrawn.json()["change_request"]["status"] == "withdrawn"
        # 撤回后不可再合入
        merged = client.post(f"/api/changes/{proposed}/merge",
                             json={"operator": "analyst-a"}, headers=_auth(token))
        assert merged.status_code == 409

class TestR4Regression:
    def test_binding_unknown_plan_404_not_500(self, tmp_path: Path) -> None:
        project_root = tmp_path / "project"
        project_root.mkdir()
        client = TestClient(create_app(project_root))
        token = _token(client)
        response = client.post(
            "/api/bindings",
            json={"plan_id": "no-such-plan", "plan_version": 1,
                  "source_file": str(GOLDEN_CSV), "operator": "analyst-a"},
            headers=_auth(token),
        )
        assert response.status_code == 404

    def test_binding_bad_eligibility_path_422_not_500(self, tmp_path: Path) -> None:
        project_root = tmp_path / "project"
        project_root.mkdir()
        client = TestClient(create_app(project_root))
        token = _token(client)
        created = client.post(
            "/api/plans", json={"template": "sales-delta", "params": PARAMS,
                                 "operator": "analyst-a"},
            headers=_auth(token),
        ).json()
        response = client.post(
            "/api/bindings",
            json={"plan_id": created["plan"]["plan_id"], "plan_version": 1,
                  "source_file": str(GOLDEN_CSV),
                  "store_eligibility": {"list_file": "/no/such/list.csv",
                                         "rule_note": "x"},
                  "operator": "analyst-a"},
            headers=_auth(token),
        )
        assert response.status_code == 422
        assert "资格清单" in response.text

    def test_create_plan_missing_field_422(self, tmp_path: Path) -> None:
        project_root = tmp_path / "project"
        project_root.mkdir()
        client = TestClient(create_app(project_root))
        token = _token(client)
        response = client.post("/api/plans", json={"template": "sales-delta"},
                               headers=_auth(token))
        assert response.status_code == 422

class TestStaticUIGuard:
    def test_ui_home_loads_without_token(self, tmp_path: Path) -> None:
        project_root = tmp_path / "project"
        project_root.mkdir()
        client = TestClient(create_app(project_root))
        response = client.get("/")
        assert response.status_code == 200
        assert "Analysis Plan Studio" in response.text
        # API 仍然需要令牌
        assert client.get("/api/plans/x/1").status_code == 401
