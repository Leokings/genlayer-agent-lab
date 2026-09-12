import shutil
import threading
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from genlayer_agent_lab.api import create_app, read_admin_token
from genlayer_agent_lab.engine import Engine
from genlayer_agent_lab.quick_tests import QUICK_SCENARIOS, runtime_availability

EXAMPLES = Path(__file__).resolve().parents[1] / "examples" / "contracts"
SELECTION = {"scenario_id": "escrow-normal", "agent": "external", "binding_id": None}


@pytest.fixture
def quick_lab(tmp_path):
    calls = []

    def evaluator(evidence, verdict, **kwargs):
        calls.append("glsim")
        return {"verdict": verdict, "provenance": {"backend": "test-glsim"}}

    def binding_evaluator(binding, context, **kwargs):
        calls.append("container-glsim")
        return {"verdict": context["fixture_verdict"], "provenance": {"backend": "test-container"}}

    engine = Engine(tmp_path / "data", evaluator=evaluator, binding_evaluator=binding_evaluator)
    app = create_app(tmp_path / "data", engine=engine)
    headers = {"Authorization": f"Bearer {read_admin_token(tmp_path / 'data')}"}
    with TestClient(app, base_url="http://127.0.0.1", raise_server_exceptions=False) as client:
        yield client, headers, engine, calls
    engine.close()


def preview(client, headers, selection=None):
    response = client.post("/v1/quick-tests/preview", headers=headers, json=selection or SELECTION)
    assert response.status_code == 200, response.text
    return response.json()


def create(client, headers, selection=None, digest=None):
    selection = selection or SELECTION
    digest = digest or preview(client, headers, selection)["digest"]
    return client.post("/v1/quick-tests/runs", headers=headers, json={
        **selection, "expected_sha256": digest, "reviewer": "Test owner",
    })


def test_catalog_and_preview_are_read_only_and_exclude_revised_decisions(quick_lab):
    client, headers, engine, calls = quick_lab
    catalog = client.get("/v1/quick-tests/catalog", headers=headers)
    assert catalog.status_code == 200
    value = catalog.json()
    assert {item["id"] for item in value["scenarios"]} == set(QUICK_SCENARIOS)
    assert len(value["scenarios"]) == 15
    assert all(item["family"] != "revised" and item["timeout_seconds"] == 600
               for item in value["scenarios"])
    assert value["runtime_verified"] is False
    assert value["runtime_availability"]["glsim"]["mode"] == "injected_evaluator"
    assert all("studio" not in check["id"] for check in value["checks"])
    first = preview(client, headers)
    assert first == preview(client, headers)
    assert first["selection"] == {**SELECTION, "backend": "glsim", "timeout_seconds": 600}
    assert any("scripted test inputs" in warning for warning in first["warnings"])
    assert any("do not submit or simulate appeals" in warning for warning in first["warnings"])
    assert engine.list_runs() == [] and calls == []
    assert catalog.headers["cache-control"] == "no-store"


def test_all_new_routes_require_owner_access_and_run_keys_cannot_create(quick_lab):
    client, headers, engine, _calls = quick_lab
    body = {**SELECTION, "expected_sha256": "0" * 64, "reviewer": "owner"}
    for path, payload in [("catalog", None), ("preview", SELECTION), ("runs", body)]:
        response = (client.get(f"/v1/quick-tests/{path}") if payload is None else
                    client.post(f"/v1/quick-tests/{path}", json=payload))
        assert response.status_code == 401
    assert engine.list_runs() == []
    run = create(client, headers).json()
    agent = {"Authorization": f"Bearer {run['agent_token']}"}
    assert client.get("/v1/quick-tests/catalog", headers=agent).status_code == 401
    assert client.post("/v1/quick-tests/preview", headers=agent, json=SELECTION).status_code == 401
    assert client.post("/v1/quick-tests/runs", headers=agent, json=body).status_code == 401


@pytest.mark.parametrize("identifier", ["escrow-revised", "treasury-revised", "generic-revised",
                                         "prediction-appeal-changed", "custom-appeal"])
def test_appeal_or_unknown_scenarios_cannot_be_previewed_or_created(quick_lab, identifier):
    client, headers, engine, _calls = quick_lab
    selection = {**SELECTION, "scenario_id": identifier}
    assert client.post("/v1/quick-tests/preview", headers=headers, json=selection).status_code == 422
    assert create(client, headers, selection, "0" * 64).status_code == 422
    assert engine.list_runs() == []


def test_allowed_id_cannot_hide_a_revised_timeline(quick_lab):
    client, headers, engine, _calls = quick_lab
    case = engine._scenarios[SELECTION["scenario_id"]]
    changed = case.model_dump()
    changed["timeline"][0]["revision"] = 2
    engine._scenarios[case.id] = type(case).model_validate(changed)
    assert client.post("/v1/quick-tests/preview", headers=headers, json=SELECTION).status_code == 422
    catalog = client.get("/v1/quick-tests/catalog", headers=headers).json()
    assert case.id not in {item["id"] for item in catalog["scenarios"]}
    assert engine.list_runs() == []


def test_review_binds_agent_current_scenario_and_run_stores_review(quick_lab):
    client, headers, engine, _calls = quick_lab
    digest = preview(client, headers)["digest"]
    assert create(client, headers, {**SELECTION, "agent": "safe"}, digest).status_code == 409
    with engine._condition:
        case = engine._scenarios["escrow-normal"]
        engine._scenarios[case.id] = case.model_copy(update={"title": "Updated review conditions"})
    assert create(client, headers, digest=digest).status_code == 409
    assert engine.list_runs() == []
    current = preview(client, headers)
    response = create(client, headers, digest=current["digest"])
    assert response.status_code == 201, response.text
    run = response.json()
    assert run["selection"]["backend"] == "glsim"
    report = client.get(f"/v1/runs/{run['run_id']}/report", headers=headers).json()
    assert report["manifest"]["quick_review"]["content_sha256"] == current["digest"]
    assert report["manifest"]["quick_review"]["reviewer"] == "Test owner"
    assert report["manifest"]["backend"] == "glsim"
    assert "agent_token" not in str(report)
    assert client.post(f"/v1/runs/{run['run_id']}/cancel", headers=headers).status_code == 200


def test_legacy_binding_selects_container_and_source_changes_invalidate_review(quick_lab, tmp_path):
    client, headers, engine, _calls = quick_lab
    project = tmp_path / "contract"
    project.mkdir()
    for filename in ("delivery-binding.yaml", "delivery.py"):
        shutil.copyfile(EXAMPLES / filename, project / filename)
    engine.import_binding(project / "delivery-binding.yaml")
    selection = {**SELECTION, "binding_id": "delivery-assessment"}
    first = preview(client, headers, selection)
    assert first["selection"]["backend"] == "container-glsim"
    assert any("project-v2" in warning for warning in first["warnings"])
    source = project / "delivery.py"
    source.write_text(source.read_text(encoding="utf-8") + "\n# Changed source snapshot\n", encoding="utf-8")
    engine.import_binding(project / "delivery-binding.yaml")
    assert create(client, headers, selection, first["digest"]).status_code == 409
    current = preview(client, headers, selection)
    run = create(client, headers, selection, current["digest"]).json()
    assert run["selection"]["backend"] == "container-glsim"
    listing = client.get("/v1/runs", headers=headers).json()
    assert next(item for item in listing if item["run_id"] == run["run_id"])["binding_id"] == "delivery-assessment"


@pytest.mark.parametrize("extra", [{"backend": "studio"}, {"backend": "fixture"},
                                   {"spec": {"profile": "project"}}, {"timeout_seconds": 3600}])
def test_new_route_cannot_select_other_backends_or_import_project_json(quick_lab, extra):
    client, headers, engine, _calls = quick_lab
    assert client.post("/v1/quick-tests/preview", headers=headers,
                       json={**SELECTION, **extra}).status_code == 422
    response = client.post("/v1/quick-tests/runs", headers=headers, json={
        **SELECTION, **extra, "expected_sha256": "0" * 64, "reviewer": "owner",
    })
    assert response.status_code == 422
    assert engine.list_runs() == []


def test_review_name_and_digest_are_required_without_echoing_private_input(quick_lab):
    client, headers, engine, _calls = quick_lab
    for changes in ({"reviewer": " "}, {"expected_sha256": "private-value"}):
        response = client.post("/v1/quick-tests/runs", headers=headers, json={
            **SELECTION, "expected_sha256": "0" * 64, "reviewer": "Owner", **changes,
        })
        assert response.status_code == 422
        assert "private-value" not in response.text
    assert engine.list_runs() == []


def test_creation_keeps_review_and_source_capture_under_engine_lock(quick_lab, monkeypatch):
    client, headers, engine, _calls = quick_lab
    digest = preview(client, headers)["digest"]
    original = engine.create_run
    attempted = threading.Event()
    acquired = threading.Event()

    def competing_edit():
        attempted.set()
        with engine._condition:
            acquired.set()

    def checked_create(*args, **kwargs):
        thread = threading.Thread(target=competing_edit)
        thread.start()
        assert attempted.wait(1)
        assert not acquired.wait(0.05), "Review lock was released before source capture"
        result = original(*args, **kwargs)
        return result

    monkeypatch.setattr(engine, "create_run", checked_create)
    assert create(client, headers, digest=digest).status_code == 201
    assert acquired.wait(1)


def test_local_availability_does_not_invoke_evaluators_or_studio(quick_lab, monkeypatch):
    _client, _headers, engine, calls = quick_lab
    monkeypatch.setattr(engine, "quick_test_evaluator_overrides", lambda: {
        "glsim": False, "container-glsim": False,
    })
    monkeypatch.setattr("genlayer_agent_lab.quick_tests.version", lambda _name: "not-the-pin")
    info = runtime_availability(engine)
    assert info["glsim"]["available"] is False
    assert info["glsim"]["runtime_verified"] is False
    assert info["container-glsim"]["available"] is None
    assert calls == []
