import json
import socket
import threading
import time
from pathlib import Path
from xml.etree import ElementTree as ET

import httpx
import pytest
import uvicorn
from fastapi.testclient import TestClient

from genlayer_agent_lab.api import MAX_BODY_BYTES, create_app, read_admin_token
from genlayer_agent_lab.bindings import extract_verdict, resolve_arguments, resolve_template
from genlayer_agent_lab.engine import Engine
from genlayer_agent_lab.reports import to_html, to_junit


@pytest.fixture
def lab(tmp_path):
    engine = Engine(tmp_path)
    app = create_app(tmp_path, engine=engine)
    admin = {"Authorization": f"Bearer {read_admin_token(tmp_path)}"}
    with TestClient(app, base_url="http://127.0.0.1", raise_server_exceptions=False) as client:
        yield client, admin, engine
    engine.close()


def new_run(lab, agent="external", scenario_id=None):
    client, admin, engine = lab
    scenario = scenario_id or client.get("/v1/scenarios", headers=admin).json()[0]["id"]
    response = client.post("/v1/runs", headers=admin, json={
        "scenario_id": scenario, "agent": agent, "backend": "fixture",
    })
    assert response.status_code == 201, response.text
    run = response.json()
    deadline = time.monotonic() + 5
    while client.get(f"/v1/runs/{run['run_id']}", headers=admin).json()["status"] in {
        "queued", "preparing"
    }:
        assert time.monotonic() < deadline
        time.sleep(0.01)
    return run, {"Authorization": f"Bearer {run['agent_token']}"}


def test_health_is_public_but_admin_endpoints_are_private(lab):
    client, admin, _ = lab
    assert client.get("/health").json()["status"] == "ok"
    assert client.get("/v1/scenarios").status_code == 401
    assert client.get("/v1/runs").status_code == 401
    response = client.get("/v1/scenarios", headers=admin)
    assert response.status_code == 200
    assert len(response.json()) == 18
    assert "expected_decision" not in response.text
    assert response.headers["cache-control"] == "no-store"
    assert "frame-ancestors 'none'" in response.headers["content-security-policy"]


def test_credentials_are_scoped_to_role_and_run(lab):
    client, admin, _ = lab
    run, agent = new_run(lab)
    base = f"/v1/runs/{run['run_id']}"
    assert client.get("/v1/runs", headers=agent).status_code == 401
    assert client.get(base + "/report", headers=agent).status_code == 401
    assert client.post(base + "/cancel", headers=agent).status_code == 401
    assert client.post(base + "/observe", headers=admin).status_code == 401
    assert client.post(base + "/observe", headers=agent).status_code == 200
    assert client.post("/v1/runs/nonexistent/observe", headers=agent).status_code == 401
    assert "agent_token" not in client.get(base, headers=admin).text
    assert "agent_token" not in client.get("/v1/runs", headers=admin).text


def test_missing_decision_fields_are_recorded_as_behavior_failure(lab):
    client, admin, _ = lab
    run, agent = new_run(lab)
    base = f"/v1/runs/{run['run_id']}"
    observation = client.post(base + "/observe", headers=agent).json()
    response = client.post(base + "/actions", headers=agent, json={
        "operation": observation["task"]["operation"], "idempotency_key": "premature",
    })
    assert response.status_code == 200
    assert response.json()["status"] == "rejected"
    finished = client.post(base + "/finish", headers=agent)
    assert finished.status_code == 200
    assert "manifest" not in finished.json()
    report = client.get(base + "/report", headers=admin).json()
    assert report["grades"]["behavior"]["status"] == "fail"
    assert report["grades"]["outcome"]["status"] == "pass"


def test_external_agent_completes_a_real_api_cycle(lab):
    client, admin, _ = lab
    run, agent = new_run(lab)
    base = f"/v1/runs/{run['run_id']}"
    observation = client.post(base + "/observe", headers=agent).json()
    decision = client.post(base + "/decision", headers=agent,
                           json={"idempotency_key": "request-1"}).json()
    assert client.get(base + "/decision", headers=agent).json() == decision
    action = {**observation["task"], "decision_id": decision["decision_id"],
              "revision": decision["revision"], "idempotency_key": "payment-1"}
    action.pop("description")
    first = client.post(base + "/actions", headers=agent, json=action)
    assert first.status_code == 200
    assert first.json()["status"] == "applied"
    retry = client.post(base + "/actions", headers=agent, json=action)
    assert retry.json() == first.json()
    assert client.post(base + "/finish", headers=agent).status_code == 200
    report = client.get(base + "/report", headers=admin).json()
    assert report["verdict"] == "pass"
    assert len(report["world"]["effects"]) == 1
    assert report["manifest"]["runtime"]["contract_executed"] is False
    html = client.get(base + "/report?format=html", headers=admin)
    assert html.status_code == 200 and "text/html" in html.headers["content-type"]
    assert "scripted" in html.text
    junit = client.get(base + "/report?format=junit", headers=admin)
    assert ET.fromstring(junit.text).attrib["failures"] == "0"


def test_request_validation_does_not_echo_private_input(lab):
    client, admin, _ = lab
    response = client.post("/v1/runs", headers=admin, json={
        "scenario_id": "private-prompt-value", "agent": "api-secret-123",
    })
    assert response.status_code == 422
    assert "api-secret-123" not in response.text
    assert "private-prompt-value" not in response.text
    run, agent = new_run(lab)
    response = client.post(f"/v1/runs/{run['run_id']}/actions", headers=agent, json={
        "operation": "transfer", "idempotency_key": "one", "amount": True,
    })
    assert response.status_code == 422
    assert client.post("/v1/runs", headers=admin, json={
        "scenario_id": "something", "path": "arbitrary.py",
    }).status_code == 422
    assert client.post("/v1/import-scenario", headers=admin).status_code == 404


@pytest.mark.parametrize("invalid", [
    {"amount": -1}, {"amount": True}, {"unexpected": "private-prompt-value"},
])
def test_rejected_action_schema_is_still_behavior_evidence(lab, invalid):
    client, admin, _ = lab
    run, agent = new_run(lab, scenario_id="escrow-revised")
    base = f"/v1/runs/{run['run_id']}"
    for _ in range(3):
        observation = client.post(base + "/observe", headers=agent).json()
    decision = client.post(base + "/decision", headers=agent,
                           json={"idempotency_key": "decision"}).json()
    assert decision["status"] == "final" and decision["verdict"] == "deny"
    response = client.post(base + "/actions", headers=agent, json={
        "operation": observation["task"]["operation"], "idempotency_key": "invalid-action",
        **invalid,
    })
    assert response.status_code == 422
    assert "private-prompt-value" not in response.text
    client.post(base + "/finish", headers=agent)
    report = client.get(base + "/report", headers=admin).json()
    assert report["grades"]["behavior"]["status"] == "fail"
    assert report["grades"]["completion"]["status"] == "pass"
    assert report["grades"]["outcome"]["status"] == "pass"
    assert "private-prompt-value" not in json.dumps(report)


def test_unauthenticated_invalid_actions_cannot_taint_agent_grade(lab):
    client, admin, _ = lab
    run, agent = new_run(lab, scenario_id="escrow-revised")
    base = f"/v1/runs/{run['run_id']}"
    for _ in range(3):
        client.post(base + "/observe", headers=agent)
    client.post(base + "/decision", headers=agent, json={"idempotency_key": "decision"})
    assert client.post(base + "/actions", json={"amount": -1}).status_code == 401
    # Malformed JSON is parsed before authentication, so the validation handler also checks auth.
    assert client.post(base + "/actions", content=b"not-json",
                       headers={"Content-Type": "application/json"}).status_code == 422
    client.post(base + "/finish", headers=agent)
    report = client.get(base + "/report", headers=admin).json()
    assert report["verdict"] == "pass"


def test_large_bodies_and_cross_origin_requests_are_rejected(lab):
    client, admin, _ = lab
    response = client.post("/v1/runs", headers=admin, content=b"x" * (MAX_BODY_BYTES + 1))
    assert response.status_code == 413
    response = client.post("/v1/runs", headers={**admin, "Transfer-Encoding": "chunked"},
                           content=iter([b"x" * 40_000, b"x" * 40_000]))
    assert response.status_code == 413
    assert client.get("/v1/scenarios", headers={**admin, "Origin": "https://evil.example"}).status_code == 403
    assert client.get("/v1/scenarios", headers={**admin, "Origin": "http://127.0.0.1"}).status_code == 200
    assert client.get("/health", headers={"Host": "attacker.example"}).status_code == 400


def test_errors_do_not_disclose_exception_text(lab, monkeypatch):
    client, admin, engine = lab

    def broken():
        raise RuntimeError("provider-secret-should-never-be-in-the-response")

    monkeypatch.setattr(engine, "list_scenarios", broken)
    response = client.get("/v1/scenarios", headers=admin)
    assert response.status_code == 503
    assert "provider-secret" not in response.text
    assert client.get("/v1/runs/unknown", headers=admin).status_code == 404


def test_exports_escape_model_output_and_mark_inconclusive_as_errors():
    report = {"run_id": "one", "scenario": "<script>alert(1)</script>",
              "status": "interrupted", "findings": ["<img src=x onerror=alert(1)>"],
              "events": [{"text": "unsafe\x01<&>"}], "grades": {
                  "decision": {"status": "inconclusive", "detail": "runtime\x01down"},
                  "behavior": {"status": "fail", "detail": "unsafe <action>"},
                  "outcome": {"status": "pass", "detail": "conserved"},
                  "completion": {"status": "inconclusive", "detail": "unknown"},
              }}
    html = to_html(report)
    assert "<script>" not in html
    assert "&lt;script&gt;" in html and "&lt;img" in html
    suite = ET.fromstring(to_junit(report))
    assert suite.attrib["failures"] == "1"
    assert suite.attrib["errors"] == "2"
    assert len(suite.findall("testcase/error")) == 2
    json.dumps(report)  # Exporters must not mutate caller data.


def test_ipv6_loopback_host_is_accepted(lab):
    client, _, _ = lab
    assert client.get("/health", headers={"Host": "[::1]:8765"}).status_code == 200


def test_real_listener_startup_shutdown_releases_state_and_retains_history(tmp_path):
    app = create_app(tmp_path)
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.bind(("127.0.0.1", 0))
    listener.listen(16)
    port = listener.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port,
                                         log_level="error", access_log=False))
    thread = threading.Thread(target=server.run, kwargs={"sockets": [listener]}, daemon=True)
    thread.start()
    run_id = None
    try:
        deadline = time.monotonic() + 10
        while not server.started:
            assert thread.is_alive(), "Service exited before startup"
            assert time.monotonic() < deadline, "Service did not start"
            time.sleep(0.02)
        with httpx.Client(base_url=f"http://127.0.0.1:{port}", timeout=5, trust_env=False) as client:
            assert client.get("/health").json()["status"] == "ok"
            headers = {"Authorization": f"Bearer {read_admin_token(tmp_path)}"}
            scenario_id = client.get("/v1/scenarios", headers=headers).json()[0]["id"]
            created = client.post("/v1/runs", headers=headers, json={
                "scenario_id": scenario_id, "agent": "safe", "backend": "fixture",
            })
            assert created.status_code == 201
            run_id = created.json()["run_id"]
            while client.get(f"/v1/runs/{run_id}", headers=headers).json()["status"] != "completed":
                assert time.monotonic() < deadline
                time.sleep(0.02)
    finally:
        server.should_exit = True
        thread.join(timeout=10)
        listener.close()
    assert not thread.is_alive(), "Listener did not shut down"
    reopened = Engine(tmp_path)
    try:
        assert reopened.report(run_id)["verdict"] == "pass"
    finally:
        reopened.close()


def test_custom_binding_discovery_scope_and_create_run_mapping(tmp_path):
    calls = []

    def fake_binding_evaluator(snapshot, context, **options):
        definition = snapshot["definition"]
        arguments = resolve_arguments(definition, context)
        fixture = resolve_template(definition["llm_response"], context)
        raw_result = {"assessment": {"outcome": fixture["decision"]}}
        calls.append({"arguments": arguments, "options": options, "snapshot": snapshot})
        return {"verdict": extract_verdict(raw_result, definition), "provenance": {
            "backend": "container-glsim", "contract_executed": False, "test_double": True,
            "binding_sha256": snapshot["binding_sha256"], "source_sha256": snapshot["source_sha256"],
        }}

    engine = Engine(tmp_path, binding_evaluator=fake_binding_evaluator)
    example = Path(__file__).parents[1] / "examples/contracts/delivery-binding.yaml"
    imported = engine.import_binding(example)
    app = create_app(tmp_path, engine=engine)
    admin = {"Authorization": f"Bearer {read_admin_token(tmp_path)}"}
    try:
        with TestClient(app, base_url="http://127.0.0.1") as client:
            assert client.get("/v1/bindings").status_code == 401
            bindings = client.get("/v1/bindings", headers=admin).json()
            assert bindings == [imported]
            assert set(bindings[0]) == {"id", "title", "method", "source_sha256", "binding_sha256"}
            created = client.post("/v1/runs", headers=admin, json={
                "scenario_id": "escrow-normal", "agent": "safe", "backend": "container-glsim",
                "binding_id": imported["id"],
            })
            assert created.status_code == 201
            run = created.json()
            agent = {"Authorization": f"Bearer {run['agent_token']}"}
            assert client.get("/v1/bindings", headers=agent).status_code == 401
            base = f"/v1/runs/{run['run_id']}"
            deadline = time.monotonic() + 5
            while client.get(base, headers=admin).json()["status"] != "completed":
                assert time.monotonic() < deadline
                time.sleep(0.01)
            report = client.get(base + "/report", headers=admin).json()
            assert report["verdict"] == "pass"
            assert report["manifest"]["backend"] == "container-glsim"
            assert report["manifest"]["binding"]["id"] == imported["id"]
            assert report["manifest"]["binding"]["binding_sha256"] == imported["binding_sha256"]
            assert "source" not in report["manifest"]["binding"]
            assert client.get(base, headers=admin).json()["binding_id"] == imported["id"]
            assert client.get("/v1/runs", headers=admin).json()[0]["binding_id"] == imported["id"]
            finished = client.post(base + "/finish", headers=agent).json()
            assert "manifest" not in finished and "source" not in finished
            assert client.post("/v1/bindings", headers=admin, json={"source": "print('bad')"}).status_code == 405
            assert client.post("/v1/import-binding", headers=admin, json={"path": str(example)}).status_code == 404
    finally:
        engine.close()
    assert len(calls) == 1
    assert calls[0]["arguments"][1:] == ["escrow-001", "v1"]
    assert calls[0]["options"]["timeout"] == 60


@pytest.mark.parametrize("payload", [
    {"backend": "glsim", "binding_id": "delivery-assessment"},
    {"backend": "fixture", "binding_id": "delivery-assessment"},
    {"backend": "container-glsim"},
    {"backend": "container-glsim", "binding_id": "../contract.py"},
    {"backend": "container-glsim", "binding_id": "delivery-assessment", "source": "print('bad')"},
])
def test_api_rejects_conflicting_backends_and_source_upload(lab, payload):
    client, admin, engine = lab
    before = engine.list_runs()
    response = client.post("/v1/runs", headers=admin,
                           json={"scenario_id": "escrow-normal", **payload})
    assert response.status_code == 422
    assert engine.list_runs() == before


@pytest.mark.parametrize("custom", [False, True])
def test_api_dispatches_studio_with_optional_binding_to_separate_evaluator(tmp_path, custom):
    calls = []

    def evaluator(data_dir, snapshot, context, **options):
        calls.append((snapshot, options))
        return {"verdict": "approve", "provenance": {"backend": "studio", "test_double": True,
                                                     "contract_executed": False}}

    engine = Engine(tmp_path, studio_evaluator=evaluator)
    try:
        if custom:
            engine.import_binding(Path(__file__).parents[1] / "examples/contracts/delivery-binding.yaml")
        app = create_app(tmp_path, engine=engine)
        headers = {"Authorization": f"Bearer {read_admin_token(tmp_path)}"}
        with TestClient(app, base_url="http://127.0.0.1") as client:
            payload = {"scenario_id": "escrow-normal", "agent": "safe", "backend": "studio"}
            if custom:
                payload["binding_id"] = "delivery-assessment"
            response = client.post("/v1/runs", json=payload, headers=headers)
            assert response.status_code == 201
            run_id = response.json()["run_id"]
            deadline = time.monotonic() + 5
            while client.get(f"/v1/runs/{run_id}", headers=headers).json()["status"] != "completed":
                assert time.monotonic() < deadline
                time.sleep(0.01)
            report = client.get(f"/v1/runs/{run_id}/report", headers=headers).json()
            assert report["verdict"] == "pass"
            assert report["manifest"]["runtime"]["backend"] == "studio"
            assert "scripted" in report["manifest"]["lifecycle"]
    finally:
        engine.close()
    assert len(calls) == 1 and calls[0][1]["timeout"] == 180
    assert (calls[0][0] is not None) is custom
