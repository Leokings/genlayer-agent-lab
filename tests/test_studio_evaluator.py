import copy
import threading
import time

import pytest

from genlayer_agent_lab.bindings import validate_snapshot
from genlayer_agent_lab.engine import Engine
from genlayer_agent_lab.runtime.studio_evaluator import (
    StudioEvaluationError,
    bundled_snapshot,
    evaluate,
)
from genlayer_agent_lab.scenarios import bundled_scenarios


def test_bundled_snapshot_is_valid_and_fixture_is_not_a_grade():
    snapshot = validate_snapshot(bundled_snapshot())
    assert snapshot["definition"]["llm_response"] == {"verdict": "$fixture_verdict"}
    assert snapshot["definition"]["arguments"] == [{"from_field": "evidence"}]


def test_studio_evaluator_requires_owned_ready_stack(monkeypatch, tmp_path):
    monkeypatch.setattr("genlayer_agent_lab.runtime.studio_stack.status", lambda _: {"ready": False})
    context = bundled_scenarios()["escrow-normal"].model_dump()
    with pytest.raises(RuntimeError, match="not ready"):
        evaluate(tmp_path, None, context)


def test_valid_studio_return_is_graded_against_scenario_not_fixture(tmp_path):
    seen = []

    def actual_result(data_dir, snapshot, context, **kwargs):
        seen.append((data_dir, snapshot, context, kwargs))
        return {"verdict": "deny", "provenance": {"backend": "studio", "execution_success": True}}

    engine = Engine(tmp_path, studio_evaluator=actual_result)
    try:
        run = engine.create_run("escrow-normal", "safe", "studio")
        deadline = time.monotonic() + 5
        while engine.get_run(run["run_id"])["status"] not in {"completed", "inconclusive"}:
            assert time.monotonic() < deadline
            time.sleep(.01)
        report = engine.report(run["run_id"])
        assert report["grades"]["decision"]["status"] == "fail"
        assert report["status"] == "completed"
        assert "scripted consumer" in report["manifest"]["lifecycle"]
        assert seen[0][1] is None
        assert seen[0][2]["fixture_verdict"] == "approve"
        assert seen[0][3]["timeout"] == 180
    finally:
        engine.close()


def test_cancel_is_forwarded_to_studio_runtime(tmp_path):
    started, canceled = threading.Event(), threading.Event()

    def long_result(data_dir, snapshot, context, *, cancel_event, **kwargs):
        started.set()
        if cancel_event.wait(3):
            canceled.set()
        raise RuntimeError("cancelled")

    engine = Engine(tmp_path, studio_evaluator=long_result)
    try:
        run = engine.create_run("escrow-normal", "external", "studio")
        assert started.wait(3)
        engine.cancel_run(run["run_id"])
        assert canceled.wait(3)
        assert engine.report(run["run_id"])["status"] == "cancelled"
    finally:
        engine.close()


def _failure_evidence():
    return {
        "verification": "inconclusive", "verdict": None,
        "error_code": "deadline_exceeded",
        "transactions": {"deployment": "0x" + "a" * 64, "execution": "0x" + "b" * 64},
        "observations": [], "submission_may_still_complete": True,
    }


def _wait_terminal(engine, run_id):
    deadline = time.monotonic() + 3
    while engine.get_run(run_id)["status"] not in {"completed", "cancelled", "inconclusive"}:
        assert time.monotonic() < deadline
        time.sleep(.01)
    return engine.report(run_id)


@pytest.mark.parametrize("observed_success", [None, False])
def test_studio_evaluation_error_keeps_sanitized_evidence_without_claiming_execution(
        monkeypatch, tmp_path, observed_success):
    evidence = _failure_evidence()
    if observed_success is False:
        evidence.update(verification="fail", error_code="execution_not_successfully_finalized",
                        submission_may_still_complete=False)
        evidence["observations"].append({"tx_id": evidence["transactions"]["execution"],
                                          "execution_success": False, "status": "FINALIZED"})
    monkeypatch.setattr("genlayer_agent_lab.runtime.studio_stack.status",
                        lambda _: {"ready": True, "endpoint": "http://127.0.0.1:8766/api"})
    monkeypatch.setattr("genlayer_agent_lab.studio_conformance.run_studio_conformance",
                        lambda *args, **kwargs: evidence)
    with pytest.raises(StudioEvaluationError) as failure:
        evaluate(tmp_path, None, bundled_scenarios()["escrow-normal"].model_dump())
    provenance = failure.value.provenance
    assert provenance["studio_evidence"] == evidence
    assert provenance["execution_success"] is observed_success
    assert provenance["contract_executed"] is (True if observed_success is False else None)
    assert "0x" not in str(failure.value)
    evidence["transactions"].clear()
    assert len(provenance["studio_evidence"]["transactions"]) == 2


def test_engine_persists_failed_studio_transaction_ids(monkeypatch, tmp_path):
    evidence = _failure_evidence()
    monkeypatch.setattr("genlayer_agent_lab.runtime.studio_stack.status",
                        lambda _: {"ready": True, "endpoint": "http://127.0.0.1:8766/api"})
    monkeypatch.setattr("genlayer_agent_lab.studio_conformance.run_studio_conformance",
                        lambda *args, **kwargs: copy.deepcopy(evidence))
    engine = Engine(tmp_path)
    try:
        run_id = engine.create_run("escrow-normal", "safe", "studio")["run_id"]
        report = _wait_terminal(engine, run_id)
        assert report["status"] == "inconclusive"
        assert all(grade["status"] == "inconclusive" for grade in report["grades"].values())
        assert report["manifest"]["runtime"]["studio_evidence"] == evidence
        assert not {"raw_result", "provider_config", "private_key"} & report["manifest"]["runtime"].keys()
    finally:
        engine.close()
    resumed = Engine(tmp_path)
    try:
        assert resumed.report(run_id) == report
    finally:
        resumed.close()


def test_late_studio_failure_does_not_change_frozen_cancellation(tmp_path):
    started, release = threading.Event(), threading.Event()

    def late_failure(*args, **kwargs):
        started.set()
        assert release.wait(3)
        raise StudioEvaluationError({"backend": "studio", "studio_evidence": _failure_evidence()})

    engine = Engine(tmp_path, studio_evaluator=late_failure)
    try:
        run_id = engine.create_run("escrow-normal", "external", "studio")["run_id"]
        assert started.wait(3)
        engine.cancel_run(run_id)
        frozen = engine.report(run_id)
        runtime = frozen["manifest"]["runtime"]
        assert runtime["submission_may_still_complete"] is True
        assert runtime["submission_status"] == "unknown"
        assert "does not cancel submitted Studio transactions" in runtime["cancellation_scope"]
        release.set()
        # A second run proves the worker consumed the late error, not merely
        # that we read the canceled report before the error reached Engine.
        subsequent = engine.create_run("escrow-normal", "safe", "fixture")
        assert _wait_terminal(engine, subsequent["run_id"])["verdict"] == "pass"
        assert engine.report(run_id) == frozen
        assert engine.store.records()[0]["provenance"] == runtime
    finally:
        release.set()
        engine.close()


def test_generic_studio_error_does_not_copy_arbitrary_exception_data(tmp_path):
    def unexpected(*args, **kwargs):
        error = RuntimeError("private-provider-key")
        error.provenance = {"raw_result": "private-contract-return"}
        raise error

    engine = Engine(tmp_path, studio_evaluator=unexpected)
    try:
        run = engine.create_run("escrow-normal", "safe", "studio")
        report = _wait_terminal(engine, run["run_id"])
        assert report["manifest"]["runtime"] == {}
        assert "private-" not in str(report)
    finally:
        engine.close()
