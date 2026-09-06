import copy
import json
import threading
from pathlib import Path

import pytest

from genlayer_agent_lab import studio_conformance as workflow
from genlayer_agent_lab.bindings import load_binding
from genlayer_agent_lab.runtime.studio import StudioError

DEPLOY = "0x" + "a" * 64
EXECUTE = "0x" + "b" * 64
ADDRESS = "0x" + "c" * 40
ENDPOINT = "http://127.0.0.1:8766"
CONTEXT = {"evidence": "private evidence must not be exported", "resource_id": "escrow-001",
           "policy_version": "v1", "amount": 100, "fixture_verdict": "approve"}


class FakeStudio:
    def __init__(self, endpoint, timeout=180, cancel_event=None):
        self.timeout, self.cancel_event = timeout, cancel_event
        self.calls = []
        self.closed = False
        self.want_appeal = False
        self.complete_appeal = True
        self.execution_success = True
        self.deployment_success = True
        self.final_verdict = "approve"
        self.finalized_early = False
        self.wait_error = None

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.closed = True

    def record(self, name):
        self.calls.append((name, self.timeout))
        if self.cancel_event is not None and self.cancel_event.is_set():
            raise StudioError("canceled")

    def doctor(self):
        self.record("doctor")
        return {"ready": True, "rpc_compatible": True, "chain_id": 61999,
                "finality_window_seconds": 30, "private_key": "never-export-key"}

    def deploy(self, snapshot, sim_config=None):
        self.record("deploy")
        return DEPLOY

    def write(self, address, snapshot, context, sim_config=None):
        self.record("write")
        assert address == ADDRESS
        assert context == CONTEXT
        return EXECUTE

    def receipt(self, tx, status):
        rounds = [{"kind": "Accepted", "validator_votes": ["agree", "agree", "agree"],
                   "private_key": "never-export-key"}]
        if self.want_appeal and self.complete_appeal and status == "FINALIZED":
            rounds.append({"kind": "Validator Appeal Failed", "validator_votes": ["agree"] * 5})
        return {"tx_id": tx, "contract_address": ADDRESS, "status": status,
                "execution_success": self.deployment_success if tx == DEPLOY else self.execution_success,
                "raw_result": {"assessment": {"outcome": self.final_verdict},
                               "provider_config": "never-export-provider"},
                "result_code": "return", "votes": ["agree"] * 3, "rounds": rounds,
                "round_count": len(rounds), "appealed": self.want_appeal,
                "private_key": "never-export-key", "timestamp_appeal": 10 if self.want_appeal else None}

    def transaction(self, tx):
        self.record("transaction")
        return self.receipt(tx, "PENDING")

    def wait(self, tx, until="accepted"):
        self.record("wait:" + until)
        if tx == EXECUTE and self.wait_error:
            raise self.wait_error
        return self.receipt(tx, "FINALIZED" if until == "finalized" or self.finalized_early else "ACCEPTED")

    def appeal(self, tx):
        self.record("appeal")
        self.want_appeal = True
        return {"transaction": self.receipt(tx, "ACCEPTED"), "request_observed": True,
                "appeal_completed": True, "completed_rounds": []}


@pytest.fixture
def configured(monkeypatch):
    snapshot = load_binding(Path(__file__).parents[1] / "examples/contracts/delivery-binding.yaml")
    client = FakeStudio(ENDPOINT)

    def create(endpoint, timeout=180, cancel_event=None):
        assert endpoint == ENDPOINT
        client.timeout, client.cancel_event = timeout, cancel_event
        return client

    monkeypatch.setattr(workflow, "StudioClient", create)
    return snapshot, client


def test_observed_deploy_write_finality_exports_no_receipts_or_credentials(configured):
    snapshot, client = configured
    owner = {"endpoint": ENDPOINT, "source_commit": workflow.STUDIO_COMMIT,
             "image_id": "sha256:" + "d" * 64, "project": "gl-agent-lab-test", "network_internal": True,
             "private_key": "never-export-key"}
    result = workflow.run_studio_conformance(ENDPOINT, snapshot, CONTEXT,
                                             sim_config={"validators": [{"private_key": "never-export-key"}]},
                                             stack_pins=owner, expected_verdict="approve")
    assert result["verification"] == "pass" and result["verdict"] == "approve"
    assert result["transactions"] == {"deployment": DEPLOY, "execution": EXECUTE}
    assert result["accepted_observed"] is True
    assert result["binding"]["source_sha256"] == snapshot["source_sha256"]
    assert result["stack_pins"]["image_id"] == owner["image_id"]
    assert result["stack_pins"]["rpc_release_verified"] is False
    assert result["public_chain"] is result["bond_accounting"] is False
    serialized = json.dumps(result)
    for private in ("private_key", "never-export", "private evidence", "raw_result", "provider_config"):
        assert private not in serialized
    assert [entry["checkpoint"] for entry in result["observations"]] == [
        "deployment_submitted", "deployment_finalization_result", "execution_submitted",
        "execution_decided", "execution_finalization_result",
    ]
    assert client.closed
    assert all(0 < seconds <= 180 for _, seconds in client.calls)


def test_completed_new_appeal_round_is_required_even_if_request_claims_complete(configured):
    snapshot, client = configured
    client.complete_appeal = False
    result = workflow.run_studio_conformance(ENDPOINT, snapshot, CONTEXT, appeal=True)
    assert result["verification"] == "inconclusive"
    assert result["appeal"]["request_observed"] is True
    assert result["appeal"]["completed"] is False
    assert result["error_code"] == "appeal_completion_not_observed"
    client.complete_appeal = True
    result = workflow.run_studio_conformance(ENDPOINT, snapshot, CONTEXT, appeal=True)
    assert result["verification"] == "pass"
    assert result["appeal"]["completed"] is True
    assert result["appeal"]["completed_rounds"][0]["kind"] == "Validator Appeal Failed"


def test_already_finalized_decision_does_not_invent_an_appeal(configured):
    snapshot, client = configured
    client.finalized_early = True
    result = workflow.run_studio_conformance(ENDPOINT, snapshot, CONTEXT, appeal=True)
    assert result["error_code"] == "appeal_window_missed"
    assert result["accepted_observed"] is False
    assert result["appeal"]["request_observed"] is False
    assert "appeal" not in [name for name, _ in client.calls]


def test_fixture_is_not_the_expected_contract_grade(configured):
    snapshot, client = configured
    client.final_verdict = "deny"
    result = workflow.run_studio_conformance(ENDPOINT, snapshot, CONTEXT)
    assert result["verification"] == "pass" and result["verdict"] == "deny"
    assert result["expected_verdict"] is None
    result = workflow.run_studio_conformance(ENDPOINT, snapshot, CONTEXT, expected_verdict="approve")
    assert result["verification"] == "fail" and result["error_code"] == "unexpected_contract_verdict"


@pytest.mark.parametrize("stage", ["deployment", "execution"])
def test_finality_alone_never_establishes_success(configured, stage):
    snapshot, client = configured
    setattr(client, stage + "_success", False)
    result = workflow.run_studio_conformance(ENDPOINT, snapshot, CONTEXT)
    assert result["verification"] == "fail"
    assert result["error_code"] == stage + "_not_successfully_finalized"
    if stage == "deployment":
        assert "write" not in [name for name, _ in client.calls]


def test_timeout_preserves_identifiers_without_resubmitting_or_claiming_cancellation(configured):
    snapshot, client = configured
    client.wait_error = StudioError("deadline_exceeded")
    result = workflow.run_studio_conformance(ENDPOINT, snapshot, CONTEXT)
    assert result["verification"] == "inconclusive"
    assert result["transactions"]["execution"] == EXECUTE
    assert result["submission_may_still_complete"] is True
    assert [name for name, _ in client.calls].count("write") == 1


def test_cancellation_and_invalid_snapshot_stop_before_submission(configured):
    snapshot, client = configured
    event = threading.Event()
    event.set()
    result = workflow.run_studio_conformance(ENDPOINT, snapshot, CONTEXT, cancel_event=event)
    assert result["error_code"] == "canceled"
    assert not result["transactions"]
    altered = copy.deepcopy(snapshot)
    altered["source"] += "\n# tamper"
    result = workflow.run_studio_conformance(ENDPOINT, altered, CONTEXT)
    assert result["verification"] == "inconclusive"
    assert not result["transactions"]


def test_owner_endpoint_mismatch_is_rejected_before_network(configured):
    snapshot, client = configured
    result = workflow.run_studio_conformance(ENDPOINT, snapshot, CONTEXT,
                                             stack_pins={"endpoint": "http://127.0.0.1:9999"})
    assert result["error_code"] == "owner_endpoint_mismatch"
    assert not client.calls
