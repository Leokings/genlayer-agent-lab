"""HTTP boundary tests use a manager double, never a contract/LLM runtime."""

import json
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from genlayer_agent_lab.api import MAX_BODY_BYTES, create_app, read_admin_token


class FakeWorkflowManager:
    def __init__(self):
        self.specs = {}
        self.calls = []
        self.invalid_actions = []

    def reject_invalid_action(self, run_id):
        self.invalid_actions.append(run_id)

    def create(self, spec):
        if spec.get("invalid"):
            raise ValueError("private validation details")
        run_id = f"workflow-{len(self.specs) + 1}"
        self.specs[run_id] = spec
        return {"run_id": run_id, "agent_token": f"token-{run_id}", "status": "running"}

    def authenticate(self, run_id, token):
        return run_id in self.specs and token == f"token-{run_id}"

    def list_runs(self):
        return [{"run_id": run_id, "status": "running"} for run_id in self.specs]

    def get(self, run_id):
        return {"run_id": run_id, "status": "running", "spec": self.specs[run_id]}

    def observe(self, run_id):
        self.calls.append(("observe", run_id))
        return {"run_id": run_id, "status": "running", "task": "Investigate this claim",
                "operations": ["read_evidence", "submit_assessment"]}

    def invoke(self, run_id, operation, arguments, idempotency_key, expected_decision_id=None):
        self.calls.append(("invoke", run_id, operation, arguments, idempotency_key,
                           expected_decision_id))
        if expected_decision_id == "stale":
            raise ValueError("secret stale decision detail")
        return {"run_id": run_id, "operation": operation, "result": {"decision_id": "d1"}}

    def appeal(self, run_id, idempotency_key, expected_decision_id):
        self.calls.append(("appeal", run_id, idempotency_key, expected_decision_id))
        return {"run_id": run_id, "status": "appealed", "decision_id": expected_decision_id}

    def finish(self, run_id):
        self.calls.append(("finish", run_id))
        return {"run_id": run_id, "status": "completed", "grades": {"expected": "secret"},
                "fixtures": "secret fixture", "source": "secret source"}

    def cancel(self, run_id):
        self.calls.append(("cancel", run_id))
        return {"run_id": run_id, "status": "canceled"}

    def report(self, run_id):
        return {"run_id": run_id, "spec": self.specs[run_id], "grades": {"expected": "secret"}}


@pytest.fixture
def workflow_lab(tmp_path):
    manager = FakeWorkflowManager()
    app = create_app(tmp_path, engine=SimpleNamespace(workflows=manager))
    admin = {"Authorization": f"Bearer {read_admin_token(tmp_path)}"}
    with TestClient(app, base_url="http://127.0.0.1") as client:
        yield client, admin, manager


def create_workflow(lab):
    client, admin, _ = lab
    response = client.post("/v1/workflows", headers=admin, json={"spec": {
        "title": "Evidence investigation", "source": "private source",
        "fixtures": ["private evidence"], "expectations": {"correct": "private grade"},
    }})
    assert response.status_code == 201, response.text
    run = response.json()
    return run, {"Authorization": f"Bearer {run['agent_token']}"}


def test_workflow_credentials_are_scoped_and_administrator_data_stays_private(workflow_lab):
    client, admin, manager = workflow_lab
    assert client.get("/v1/workflows").status_code == 401
    run, agent = create_workflow(workflow_lab)
    another, other_agent = create_workflow(workflow_lab)
    base = f"/v1/workflows/{run['run_id']}"
    assert client.get("/v1/workflows", headers=admin).json() == manager.list_runs()
    assert client.get(base, headers=admin).json()["spec"]["source"] == "private source"
    assert client.get(base + "/report", headers=admin).json()["grades"] == {"expected": "secret"}
    for path, method in [("/v1/workflows", "GET"), ("/v1/workflows", "POST"),
                         (base, "GET"), (base + "/report", "GET"), (base + "/cancel", "POST")]:
        assert client.request(method, path, headers=agent, json={"spec": {}}).status_code == 401
    for credentials in ({}, admin, other_agent):
        assert client.post(base + "/observe", headers=credentials).status_code == 401
    assert client.post("/v1/workflows/unknown/observe", headers=agent).status_code == 401
    observation = client.post(base + "/observe", headers=agent)
    assert observation.status_code == 200
    assert not any(word in observation.text for word in ("private", "fixtures", "expectations"))
    assert another["run_id"] != run["run_id"]


def test_workflow_operation_appeal_and_finish_preserve_protocol_arguments(workflow_lab):
    client, admin, manager = workflow_lab
    run, agent = create_workflow(workflow_lab)
    run_id = run["run_id"]
    base = f"/v1/workflows/{run_id}"
    payload = {"operation": "submit_assessment", "arguments": {"claims": [{"weight": 0.7}]},
               "idempotency_key": "submit-1", "expected_decision_id": "d1"}
    for _ in range(2):
        assert client.post(base + "/operations", headers=agent, json=payload).status_code == 200
    assert manager.calls[-2:] == [("invoke", run_id, "submit_assessment", payload["arguments"],
                                   "submit-1", "d1")] * 2
    # The manager enforces idempotency; the transport preserves the original request.
    appeal = client.post(base + "/appeals", headers=agent, json={
        "idempotency_key": "appeal-1", "expected_decision_id": "d1",
    })
    assert appeal.status_code == 200
    assert manager.calls[-1] == ("appeal", run_id, "appeal-1", "d1")
    finished = client.post(base + "/finish", headers=agent)
    assert finished.json() == {"run_id": run_id, "status": "completed"}
    assert client.post(base + "/cancel", headers=admin).json()["status"] == "canceled"


@pytest.mark.parametrize("payload", [
    {"operation": "", "arguments": {}, "idempotency_key": "one"},
    {"operation": "x", "arguments": [], "idempotency_key": "one"},
    {"operation": "x", "arguments": {}, "idempotency_key": True},
    {"operation": "x", "arguments": {}, "idempotency_key": "x" * 129},
    {"operation": "x", "arguments": {}, "idempotency_key": "one", "source": "private-value"},
    {"operation": "x", "arguments": {}, "idempotency_key": "one", "expected_decision_id": ""},
])
def test_workflow_operation_validation_is_strict_and_does_not_echo_input(workflow_lab, payload):
    client, _, manager = workflow_lab
    run, agent = create_workflow(workflow_lab)
    response = client.post(f"/v1/workflows/{run['run_id']}/operations", headers=agent, json=payload)
    assert response.status_code == 422
    assert "private-value" not in response.text
    assert not manager.calls
    assert manager.invalid_actions == [run["run_id"]]


@pytest.mark.parametrize("payload", [
    {"idempotency_key": "one"},
    {"idempotency_key": "one", "expected_decision_id": None},
    {"idempotency_key": "one", "expected_decision_id": "d1", "evidence": "private-value"},
])
def test_workflow_appeal_is_decision_bound_without_an_invented_evidence_field(workflow_lab, payload):
    client, _, manager = workflow_lab
    run, agent = create_workflow(workflow_lab)
    response = client.post(f"/v1/workflows/{run['run_id']}/appeals", headers=agent, json=payload)
    assert response.status_code == 422
    assert "private-value" not in response.text
    assert not manager.calls
    assert manager.invalid_actions == [run["run_id"]]


def test_invalid_workflow_payload_cannot_mark_another_run(workflow_lab):
    client, _, manager = workflow_lab
    run, _ = create_workflow(workflow_lab)
    for suffix in ("operations", "appeals"):
        response = client.post(f"/v1/workflows/{run['run_id']}/{suffix}",
                               headers={"Authorization": "Bearer wrong-run-token"}, json={})
        assert response.status_code in {401, 422}
    assert manager.invalid_actions == []


def test_workflow_json_and_transport_bounds_and_sanitized_manager_errors(workflow_lab):
    client, admin, manager = workflow_lab
    deep = {}
    for _ in range(20):
        deep = {"next": deep}
    for spec in (deep, {"items": [0] * 5000}, {"text": "x" * 33_000},
                 {"text": "\u20ac" * 20_000}):
        assert client.post("/v1/workflows", headers=admin, json={"spec": spec}).status_code == 422
    assert client.post("/v1/workflows", headers=admin, json={"spec": {}, "unknown": "private-value"}).status_code == 422
    assert client.post("/v1/workflows", headers=admin,
                       content=b"x" * (MAX_BODY_BYTES + 1)).status_code == 413
    for raw in (b'{"spec":{"number":NaN}}', b'{"spec":{"text":"\\ud800"}}'):
        response = client.post("/v1/workflows", headers={**admin, "Content-Type": "application/json"},
                               content=raw)
        assert response.status_code == 422
    response = client.post("/v1/workflows", headers=admin, json={"spec": {"invalid": True}})
    assert response.status_code == 409 and "private" not in response.text
    assert manager.specs == {}
    run, agent = create_workflow(workflow_lab)
    response = client.post(f"/v1/workflows/{run['run_id']}/operations", headers=agent, json={
        "operation": "commit", "idempotency_key": "one", "expected_decision_id": "stale",
    })
    assert response.status_code == 409 and "secret" not in response.text
    assert client.get("/v1/workflows/unknown", headers=admin).status_code == 404
    assert "token-workflow" not in json.dumps(manager.list_runs())
