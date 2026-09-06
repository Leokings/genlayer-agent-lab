"""Actual imported-contract integration; requires an already built Linux worker.

These tests never start a daemon or build/pull an image. They execute the example
contract through the production Engine/container path in a temporary installation.
"""

import hashlib
import time
from pathlib import Path

import pytest
import yaml

from genlayer_agent_lab.engine import TERMINAL, Engine
from genlayer_agent_lab.runtime import container

pytestmark = pytest.mark.container

EXAMPLE = Path(__file__).parents[1] / "examples/contracts/delivery-binding.yaml"


@pytest.fixture(scope="module")
def actual_worker():
    state = container.doctor()
    if not state.get("ready"):
        pytest.skip("Actual Linux worker image unavailable: " + state.get("error", "not ready"))
    return state


def await_report(engine, run_id):
    deadline = time.monotonic() + 100
    while time.monotonic() < deadline:
        if engine.get_run(run_id)["status"] in TERMINAL:
            return engine.report(run_id)
        time.sleep(0.05)
    pytest.fail(f"Custom run did not finish: {engine.get_run(run_id)['status']}")


def assert_execution(report, summary, actual_worker, expected_result):
    runtime = report["manifest"]["runtime"]
    assert runtime["backend"] == "container-glsim"
    assert runtime["image_id"] == actual_worker["image_id"]
    assert runtime["method"] == "assess_delivery"
    assert runtime["raw_result"] == {
        "assessment": {"outcome": expected_result}, "resource_id": "escrow-001"
    }
    assert runtime["contract_executed"] is True
    assert runtime["execution_success"] is True
    assert runtime["mocked_io"] is True
    assert runtime["isolated"] is True
    assert runtime["source_sha256"] == summary["source_sha256"]
    assert runtime["binding_sha256"] == summary["binding_sha256"]
    assert report["manifest"]["binding"]["binding_sha256"] == summary["binding_sha256"]
    assert runtime["consensus"]["status"] == "FINALIZED"
    assert runtime["consensus"]["captured_validators"] > 0
    assert runtime["consensus"]["llm_fixture_calls"] > 1


def test_actual_delivery_binding_runs_structured_method_and_persists_report(actual_worker, tmp_path):
    engine = Engine(tmp_path / "lab")
    try:
        summary = engine.import_binding(EXAMPLE)
        assert summary["source_sha256"] == hashlib.sha256(
            EXAMPLE.with_name("delivery.py").read_bytes()
        ).hexdigest()
        created = engine.create_run(
            "escrow-normal", agent="safe", backend="container-glsim", binding_id=summary["id"]
        )
        report = await_report(engine, created["run_id"])
        assert report["status"] == "completed", report["findings"]
        assert report["verdict"] == "pass", report["findings"]
        assert all(grade["status"] == "pass" for grade in report["grades"].values())
        assert_execution(report, summary, actual_worker, "approve")
        assert report["world"]["balance"] == 900
        assert report["world"]["recipient_balance"] == 100
        assert len(report["world"]["effects"]) == 1
        assert report["world"]["effects"][0]["decision"]["resource_id"] == "escrow-001"
    finally:
        engine.close()

    reopened = Engine(tmp_path / "lab")
    try:
        assert reopened.report(created["run_id"]) == report
    finally:
        reopened.close()


def test_actual_wrong_decision_is_graded_separately_from_successful_execution(actual_worker, tmp_path):
    # Keep the scenario's independently specified expected approval unchanged.
    # Force the custom contract's actual model fixture to deny instead.
    definition = yaml.safe_load(EXAMPLE.read_text(encoding="utf-8"))
    definition["id"] = "delivery-denial-control"
    definition["llm_response"] = {"decision": "deny"}
    (tmp_path / "delivery.py").write_bytes(EXAMPLE.with_name("delivery.py").read_bytes())
    binding_path = tmp_path / "denial-binding.yaml"
    binding_path.write_text(yaml.safe_dump(definition), encoding="utf-8")
    engine = Engine(tmp_path / "lab")
    try:
        summary = engine.import_binding(binding_path)
        created = engine.create_run(
            "escrow-normal", agent="safe", backend="container-glsim", binding_id=summary["id"]
        )
        report = await_report(engine, created["run_id"])
        assert report["status"] == "completed", report["findings"]
        assert_execution(report, summary, actual_worker, "deny")
        assert report["manifest"]["scenario"]["expected_decision"] == "approve"
        assert report["verdict"] == "fail"
        assert report["grades"]["decision"]["status"] == "fail"
        assert report["grades"]["behavior"]["status"] == "pass"
        assert report["grades"]["outcome"]["status"] == "pass"
        assert report["grades"]["completion"]["status"] == "fail"
        assert report["world"]["effects"] == []
        assert report["world"]["balance"] == 1000
        assert report["world"]["recipient_balance"] == 0
    finally:
        engine.close()
