"""Guided authoring preserves review integrity, runtime boundaries and agent scope."""

import copy
import hashlib
import hmac
import shutil
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import Request
from fastapi.testclient import TestClient

from genlayer_agent_lab import onboarding, project_verification
from genlayer_agent_lab.api import (
    MAX_BODY_BYTES,
    MAX_PROJECT_BODY_BYTES,
    create_app,
    read_admin_token,
)
from genlayer_agent_lab.project_scenarios import (
    agent_scenario_view,
    project_scenario_document,
    validate_project_scenario,
)
from genlayer_agent_lab.project_wire import decode_project_wire, encode_project_wire

ROOT = Path(__file__).parents[1]


class Manager:
    def __init__(self):
        self.specs = {}

    def create(self, spec):
        identifier = "project-" + str(len(self.specs) + 1)
        self.specs[identifier] = validate_project_scenario(spec)
        return {"run_id": identifier, "agent_token": "agent-" + identifier}

    def get(self, run_id):
        return {"run_id": run_id, "spec": self.specs[run_id]}

    def authenticate(self, run_id, token):
        return run_id in self.specs and token == "agent-" + run_id

    def observe(self, run_id):
        return {"run_id": run_id, **agent_scenario_view(self.specs[run_id])}

    def invoke(self, run_id, *args, **kwargs):
        return {"run_id": run_id, "status": "queued"}

    def appeal(self, run_id, *args):
        return {"run_id": run_id, "status": "queued"}

    def finish(self, run_id):
        return {"run_id": run_id, "status": "completed"}

    def reject_invalid_action(self, run_id):
        pass


@pytest.fixture
def lab(tmp_path):
    manager = Manager()
    app = create_app(tmp_path, engine=SimpleNamespace(workflows=manager))
    admin = {"Authorization": "Bearer " + read_admin_token(tmp_path)}
    with TestClient(app, base_url="http://127.0.0.1:8765", raise_server_exceptions=False) as client:
        yield client, admin, manager


def post(lab, endpoint, payload):
    client, admin, _ = lab
    return client.post("/v1/onboarding/" + endpoint, json=payload, headers=admin)


def draft(lab, identifier="prediction-finalize", values=None):
    result = post(lab, "draft", {"template_id": identifier, "values": values or {}})
    assert result.status_code == 200, result.text
    return result.json()


def approved(lab):
    value = draft(lab)
    result = post(lab, "review", {"spec": value["spec"], "expected_sha256": value["digest"],
                                  "reviewer": "Developer"})
    assert result.status_code == 200, result.text
    return result.json()


def create_run(lab):
    client, admin, _ = lab
    response = client.post("/v1/workflows", headers=admin, json={"spec": approved(lab)["spec"]})
    assert response.status_code == 201, response.text
    result = response.json()
    return result["run_id"], {"Authorization": "Bearer " + result["agent_token"]}


def test_all_supported_templates_produce_unapproved_inspectable_drafts(lab):
    client, admin, _ = lab
    templates = client.get("/v1/onboarding/templates", headers=admin).json()["templates"]
    assert len(templates) == 11
    for template in templates:
        result = draft(lab, template["id"])
        spec = decode_project_wire(result["spec"])
        assert spec["review"] == {"status": "draft", "reviewer": None, "content_sha256": None}
        assert result["rules"] and any("model response" in item for item in result["summary"])
        assert "project_snapshot" in spec
        assert client.post("/v1/workflows", headers=admin, json={"spec": result["spec"]}).status_code == 422


@pytest.mark.parametrize("endpoint,method", [
    ("templates", "GET"), ("status", "GET"), ("connection/project-1", "GET"),
    ("draft", "POST"), ("preview", "POST"), ("review", "POST"),
])
def test_onboarding_requires_admin_even_for_run_credentials(lab, endpoint, method):
    client, _, _ = lab
    _, agent = create_run(lab)
    for headers in ({}, agent):
        response = client.request(method, "/v1/onboarding/" + endpoint, headers=headers,
                                  **({"json": {}} if method == "POST" else {}))
        assert response.status_code == 401


def test_exact_review_rejects_changed_content_and_preserves_integer_tags(lab):
    original = draft(lab)
    spec = decode_project_wire(original["spec"])
    spec["policy"]["max_fee"] = 10**24 + 3
    spec["context"]["literal"] = {"$lab_integer": "123"}
    spec["context"]["ordinary_string"] = "1000000000000000000000003"
    preview = post(lab, "preview", {"spec": encode_project_wire(spec)}).json()
    assert preview["spec"]["policy"]["max_fee"] == {"$lab_integer": str(10**24 + 3)}
    altered = copy.deepcopy(preview["spec"])
    altered["task"] += " Changed."
    assert post(lab, "review", {"spec": altered, "expected_sha256": preview["digest"],
                                 "reviewer": "Developer"}).status_code == 409
    result = post(lab, "review", {"spec": preview["spec"], "expected_sha256": preview["digest"],
                                   "reviewer": "Developer"}).json()
    restored = decode_project_wire(result["spec"])
    assert restored["context"]["literal"] == {"$lab_integer": "123"}
    assert isinstance(restored["context"]["ordinary_string"], str)
    assert result["digest"] == preview["digest"]
    assert validate_project_scenario(restored)["review"]["status"] == "approved"
    result["spec"]["title"] += " tampered"
    assert post(lab, "preview", {"spec": result["spec"]}).status_code == 422
    assert post(lab, "review", {"spec": preview["spec"], "expected_sha256": preview["digest"],
                                 "reviewer": " "}).status_code == 422


def test_exported_scenario_documents_can_be_previewed_and_reviewed(lab):
    value = draft(lab)
    spec = decode_project_wire(value["spec"])
    spec["policy"]["max_total_fee"] = 2**180 + 1
    document = project_scenario_document(spec)
    result = post(lab, "preview", {"spec": document})
    assert result.status_code == 200, result.text
    preview = result.json()
    result = post(lab, "review", {"spec": document, "expected_sha256": preview["digest"],
                                   "reviewer": "Developer"})
    assert result.status_code == 200, result.text
    assert decode_project_wire(result.json()["spec"])["policy"]["max_total_fee"] == 2**180 + 1
    document["integer_encoding"] = "unsupported"
    assert post(lab, "preview", {"spec": document}).status_code == 422


@pytest.mark.parametrize("values", [
    {"timeout_seconds": True}, {"timeout_seconds": "900"}, {"timeout_seconds": 1801},
    {"task": " "}, {"title": "a" * 161}, {"market": "arbitrary"},
    {"final_outcome": "void"}, {"initial_confidence_bps": False},
    {"initial_confidence_bps": 10001},
])
def test_customization_is_strict_and_bounded(lab, values):
    assert post(lab, "draft", {"template_id": "prediction-finalize", "values": values}).status_code == 422


@pytest.mark.parametrize("mode", ["finalize", "appeal-changed", "appeal-upheld",
                                  "messages-delivered", "messages-repair"])
@pytest.mark.parametrize("outcome", ["yes", "no"])
def test_outcome_customization_keeps_evidence_fixture_and_expectation_coherent(lab, mode, outcome):
    spec = decode_project_wire(draft(lab, "prediction-" + mode, {
        "final_outcome": outcome, "initial_confidence_bps": 4321,
    })["spec"])
    initial = spec["fixtures"]["initial"][0]["response"]
    opposite = "no" if outcome == "yes" else "yes"
    assert initial == {"outcome": opposite if mode == "appeal-changed" else outcome,
                       "confidence_bps": 4321}
    expected = next(rule for rule in spec["expectations"]["rules"] if rule["id"] == "oracle_outcome")
    assert expected["right"]["literal"] == outcome
    evidence = opposite if mode == "appeal-upheld" else outcome
    assert f"reports {evidence}" in spec["context"]["evidence"]
    assert f"reports {evidence}" in spec["evidence"][0]["content"]
    if mode.startswith("appeal-"):
        after = spec["fixtures"]["after_appeal"][0]["response"]
        assert after["outcome"] == outcome
        if mode == "appeal-upheld":
            assert after == initial
    assert post(lab, "draft", {"template_id": "investigation-appeal",
                               "values": {"final_outcome": outcome}}).status_code == 422


def test_templates_load_from_installed_kit_without_source_checkout(tmp_path, monkeypatch):
    package = tmp_path / "installed" / "genlayer_agent_lab"
    shutil.copytree(ROOT / "examples" / "projects", package / "_kit" / "examples" / "projects")
    monkeypatch.setattr(project_verification, "__file__", str(package / "project_verification.py"))
    for identifier in onboarding.TEMPLATES:
        spec = onboarding.template_spec(identifier)
        assert validate_project_scenario(spec, require_review=False)["project_snapshot"]


@pytest.mark.parametrize("action,payload", [
    ("observe", None), ("operations", {"operation": "read_oracle", "idempotency_key": "read"}),
    ("appeals", {"idempotency_key": "appeal", "expected_decision_id": "decision-1"}),
    ("finish", None),
])
def test_connection_requires_actual_authenticated_agent_call(lab, action, payload):
    client, admin, _ = lab
    run_id, agent = create_run(lab)
    connection = "/v1/onboarding/connection/" + run_id
    assert not client.get(connection, headers=admin).json()["connected"]
    assert client.get("/v1/workflows/" + run_id, headers=admin).status_code == 200
    route = "/v1/workflows/" + run_id + "/" + action
    assert client.post(route, headers=admin, json=payload).status_code == 401
    assert not client.get(connection, headers=admin).json()["connected"]
    response = client.post(route, headers=agent, json=payload)
    assert response.status_code == 200, response.text
    status = client.get(connection, headers=admin).json()
    assert status["connected"] and status["last_seen"] and "since this service started" in status["detail"]
    if action == "observe":
        assert not {"fixtures", "expectations", "project_snapshot", "review"} & response.json().keys()
    assert client.get("/v1/onboarding/connection/missing", headers=admin).status_code == 404


def test_large_import_routes_are_bounded_before_parsing(lab):
    import json
    client, admin, _ = lab
    value = draft(lab)
    payload = json.dumps({"spec": value["spec"]}) + " " * MAX_BODY_BYTES
    headers = {**admin, "Content-Type": "application/json"}
    assert client.post("/v1/onboarding/preview", content=payload, headers=headers).status_code == 200
    for endpoint in ("preview", "review"):
        assert client.post("/v1/onboarding/" + endpoint,
                           content=" " * (MAX_PROJECT_BODY_BYTES + 1), headers=headers).status_code == 413
    assert client.post("/v1/onboarding/draft", content=payload, headers=headers).status_code == 413


def test_status_is_read_only_cached_and_uses_listener_not_tunnel_host(lab, monkeypatch):
    from genlayer_agent_lab.runtime import studio_profiles
    calls = []
    monkeypatch.setattr(studio_profiles, "modern_profile_status", lambda path: calls.append(path) or {
        "ready": True, "installed": True, "runtime_verified": True,
        "fixture_ready": True, "bond_accounting": True, "network_internal": True, "validator_count": 12,
    })
    monkeypatch.setattr(studio_profiles, "build_profile", lambda *args: pytest.fail("GET must not build"))
    client, admin, _ = lab
    for _ in range(2):
        response = client.get("/v1/onboarding/status", headers={**admin, "Host": "localhost:8875"})
        assert response.status_code == 200
        result = response.json()
        assert result["ready"] and result["server_url"] == "http://127.0.0.1:8765"
        assert Path(result["mcp_command"]).is_absolute()
    assert len(calls) == 1
    request = Request({"type": "http", "scheme": "http", "server": ("testserver", 80),
                       "headers": [(b"host", b"127.0.0.1:8875")]})
    assert onboarding.server_origin(request) == "http://127.0.0.1:8765"


def test_slow_status_probe_has_bounded_wait_and_no_repeated_worker(tmp_path, monkeypatch):
    from genlayer_agent_lab.runtime import studio_profiles
    release, calls = threading.Event(), []

    def inspect(path):
        calls.append(path)
        release.wait(2)
        return {"installed": False, "ready": False}

    monkeypatch.setattr(studio_profiles, "modern_profile_status", inspect)
    monkeypatch.setattr(onboarding, "STATUS_WAIT_SECONDS", 0.01)
    probe = onboarding.StatusProbe(tmp_path)
    try:
        started = time.monotonic()
        assert probe.read() is None
        assert probe.read() is None
        assert time.monotonic() - started < 0.5
        assert len(calls) == 1
    finally:
        release.set()
    assert probe.event.wait(1)
    assert probe.read()["installed"] is False


def test_health_proof_is_bound_to_a_fresh_nonce_without_disclosing_credentials(lab):
    client, admin, _ = lab
    token = admin["Authorization"].removeprefix("Bearer ")
    baseline = client.get("/health").json()
    assert "setup_proof" not in baseline
    first = client.get("/health", params={"setup_nonce": "a" * 64})
    second = client.get("/health", params={"setup_nonce": "b" * 64})
    assert first.status_code == second.status_code == 200
    proof = hmac.new(token.encode(), b"genlayer-agent-lab/setup/v1\0" + b"a" * 64,
                     hashlib.sha256).hexdigest()
    assert first.json()["setup_proof"] == proof
    assert second.json()["setup_proof"] != proof
    assert first.json()["installation_id"] == baseline["installation_id"]
    assert token not in first.text and token not in second.text


@pytest.mark.parametrize("nonce", ["", "a" * 63, "a" * 65, "A" * 64, "z" * 64, "a" * 63 + "\n"])
def test_health_rejects_invalid_setup_challenges(lab, nonce):
    client, _, _ = lab
    response = client.get("/health", params={"setup_nonce": nonce})
    assert response.status_code == 422
    assert "setup_proof" not in response.text


def test_status_repair_command_targets_current_installation(lab, monkeypatch):
    from genlayer_agent_lab.runtime import studio_profiles
    calls = []
    monkeypatch.setattr(studio_profiles, "modern_profile_status", lambda path: calls.append(path) or {
        "installed": False, "ready": False,
    })
    client, admin, _ = lab
    response = client.get("/v1/onboarding/status", headers=admin)
    assert response.status_code == 200
    command = next(check["command"] for check in response.json()["checks"] if check["id"] == "studio")
    assert "studio-build" in command and "--data-dir" in command and str(calls[0]) in command


@pytest.mark.parametrize("changes", [{"validator_count": 0}, {"validator_count": True},
                                     {"network_internal": False}, {"fixture_ready": False}])
def test_status_does_not_call_an_incomplete_or_unisolated_runtime_ready(lab, monkeypatch, changes):
    from genlayer_agent_lab.runtime import studio_profiles
    monkeypatch.setattr(studio_profiles, "modern_profile_status", lambda path: {
        "ready": True, "installed": True, "runtime_verified": True,
        "fixture_ready": True, "bond_accounting": True, "network_internal": True,
        "validator_count": 12, **changes,
    })
    client, admin, _ = lab
    response = client.get("/v1/onboarding/status", headers=admin)
    assert response.status_code == 200
    assert not response.json()["ready"]
    assert next(check for check in response.json()["checks"] if check["id"] == "studio")["status"] == "fail"
