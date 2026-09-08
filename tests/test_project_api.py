"""Real ASGI boundary with a manager double: exact wire values and review hashes."""

import copy
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from genlayer_agent_lab.api import create_app, read_admin_token
from genlayer_agent_lab.project_bindings import load_project_binding, normalize_project_arguments
from genlayer_agent_lab.project_scenarios import (
    approve_project_scenario,
    prediction_scenario_template,
    scenario_digest,
    validate_project_scenario,
)
from genlayer_agent_lab.project_wire import (
    INTEGER_ENCODING,
    decode_project_wire,
    encode_project_wire,
)

BIG = 10**24 + 1
ROOT = Path(__file__).parents[1]


class RecordingManager:
    def __init__(self):
        self.specs, self.calls = {}, []

    def create(self, spec):
        project = spec.get("schema_version") == 2
        if project:
            spec = validate_project_scenario(spec)
        run_id = ("project-" if project else "workflow-") + str(len(self.specs) + 1)
        self.specs[run_id] = copy.deepcopy(spec)
        return {"run_id": run_id, "agent_token": "token-" + run_id, "status": "running"}

    def authenticate(self, run_id, token):
        return run_id in self.specs and token == "token-" + run_id

    def reject_invalid_action(self, run_id):
        self.calls.append(("rejected", run_id))

    def list_runs(self):
        return [{"run_id": run_id, "status": "running"} for run_id in self.specs]

    def observe(self, run_id):
        if run_id.startswith("project-"):
            return {"run_id": run_id, "profile": "project", "fee_reserved": BIG,
                    "fee_unit": "local_GEN_base_units", "numeric_string": str(BIG),
                    "literal": {"$lab_integer": "123"}}
        return {"run_id": run_id, "profile": "service_release", "amount": 40,
                "literal": {"$lab_integer": "123"}}

    get = observe

    def report(self, run_id):
        return {**self.observe(run_id), "final_balance": BIG + 3}

    def invoke(self, run_id, operation, arguments, idempotency_key, expected_decision_id=None):
        if run_id.startswith("project-"):
            arguments = normalize_project_arguments(self.specs[run_id]["project_snapshot"], operation, arguments)
        self.calls.append((run_id, operation, copy.deepcopy(arguments), idempotency_key))
        return {"run_id": run_id, "status": "queued", "arguments": arguments}

    def finish(self, run_id):
        return {"run_id": run_id, "status": "completed", "expectations": {"secret": 123}}


@pytest.fixture
def lab(tmp_path):
    manager = RecordingManager()
    app = create_app(tmp_path, engine=SimpleNamespace(workflows=manager))
    admin = {"Authorization": "Bearer " + read_admin_token(tmp_path)}
    with TestClient(app, base_url="http://127.0.0.1", raise_server_exceptions=False) as client:
        yield client, admin, manager


def reviewed_spec():
    draft = prediction_scenario_template(load_project_binding(ROOT / "examples/projects/prediction/project.yaml"))
    draft["policy"]["max_fee"] = BIG
    draft["policy"]["max_total_fee"] = BIG + 1
    draft["context"].update(numeric_string=str(BIG), literal={"$lab_integer": "123"}, exact_limit=BIG)
    return approve_project_scenario(draft, reviewer="developer", expected_sha256=scenario_digest(draft))


def create_project(lab):
    client, admin, _ = lab
    spec = reviewed_spec()
    response = client.post("/v1/workflows", headers=admin, json={"spec": encode_project_wire(spec)})
    assert response.status_code == 201, response.text
    created = response.json()
    return spec, created, {"Authorization": "Bearer " + created["agent_token"]}


def test_reviewed_project_survives_exact_http_decode_once(lab):
    _, created, _ = create_project(lab)
    client, admin, manager = lab
    stored = manager.specs[created["run_id"]]
    assert stored == reviewed_spec()
    assert stored["context"]["literal"] == {"$lab_integer": "123"}
    assert type(stored["context"]["numeric_string"]) is str
    assert type(stored["policy"]["max_fee"]) is int
    assert created["integer_encoding"] == INTEGER_ENCODING
    tampered = encode_project_wire(stored)
    tampered["policy"]["max_fee"] = {"$lab_integer": str(BIG + 2)}
    invalid = client.post("/v1/workflows", headers=admin, json={"spec": tampered})
    assert invalid.status_code == 422
    assert len(manager.specs) == 1


def test_project_observation_and_report_have_lossless_fee_tags(lab):
    _, created, agent = create_project(lab)
    client, admin, _ = lab
    path = "/v1/workflows/" + created["run_id"]
    observation = client.post(path + "/observe", headers=agent)
    assert observation.status_code == 200
    raw = observation.json()
    assert raw["fee_reserved"] == {"$lab_integer": str(BIG)}
    assert raw["literal"] == {"$lab_object": {"$lab_integer": "123"}}
    restored = decode_project_wire(raw)
    assert restored["fee_reserved"] == BIG and restored["numeric_string"] == str(BIG)
    report = client.get(path + "/report", headers=admin)
    assert report.json()["final_balance"] == {"$lab_integer": str(BIG + 3)}
    assert client.get(path + "/report", headers=agent).status_code == 401
    finished = client.post(path + "/finish", headers=agent).json()
    assert "expectations" not in finished


@pytest.mark.parametrize("representation", [{"$lab_integer": str(BIG)}, str(BIG)])
def test_exact_agent_integer_reaches_typed_operation_without_rounding(lab, representation):
    _, created, agent = create_project(lab)
    client, _, manager = lab
    path = "/v1/workflows/" + created["run_id"] + "/operations"
    response = client.post(path, headers=agent, json={"operation": "record",
        "arguments": {"expected_revision": representation, "outcome": "yes"}, "idempotency_key": "record-once"})
    assert response.status_code == 200, response.text
    assert manager.calls[-1][2]["expected_revision"] == BIG
    assert type(manager.calls[-1][2]["expected_revision"]) is int
    assert response.json()["arguments"]["expected_revision"] == {"$lab_integer": str(BIG)}


def test_legacy_http_schema_and_literal_objects_are_unchanged(lab):
    client, admin, manager = lab
    original = {"schema_version": 1, "profile": "service_release", "amount": 40,
                "literal": {"$lab_integer": "123"}}
    response = client.post("/v1/workflows", headers=admin, json={"spec": original})
    assert response.status_code == 201
    created = response.json()
    assert "integer_encoding" not in created
    assert manager.specs[created["run_id"]] == original
    observed = client.post("/v1/workflows/" + created["run_id"] + "/observe",
        headers={"Authorization": "Bearer " + created["agent_token"]}).json()
    assert observed["literal"] == {"$lab_integer": "123"}
    assert type(observed["amount"]) is int
    assert "integer_encoding" not in observed
