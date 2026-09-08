"""Submission boundary checks using the real manager/journal and fake Studio.

The separate live verifier establishes Studio behavior; these tests target
forged citations, stale decisions, malformed claims and recovery of artifacts.
"""

import copy
import json

import pytest
from test_project_workflows import (
    complete_record,
    finish,
    operation,
    scenario,
    start,
    wait,
)
from test_project_workflows import harness as harness

from genlayer_agent_lab.project_investigation import (
    InvestigationSubmission,
    build_investigation,
)


def enabled(draft):
    draft["policy"]["operations"]["submit_investigation"] = {"max_calls": 4}
    draft["policy"]["operations"]["read_evidence"]["max_calls"] = 8


def payload():
    return {"disposition": "accept", "proposed_result": {"outcome": "yes"},
            "findings": [{"evidence_id": "settlement_record", "assessment": "supports",
                          "note": "The supplied record supports the observed result."}],
            "summary": "Accept the result after finality and record it once."}


def ready(factory, *, read=True):
    manager = factory()
    created = start(manager, scenario(change=enabled))
    run_id = created["run_id"]
    if read:
        operation(manager, run_id, "read_evidence", {"id": "settlement_record"})
    operation(manager, run_id, "resolve", {"evidence": "The supplied record"})
    decision = next(t for t in manager.observe(run_id)["transactions"].values() if t["operation"] == "resolve")
    return manager, run_id, decision


@pytest.mark.parametrize("defect", ["unread", "unknown", "duplicate", "blank", "extra", "no-decision", "stale"])
def test_forged_or_malformed_submission_is_recorded_as_behavior_failure(harness, defect):
    chain, factory = harness
    manager, run_id, decision = ready(factory, read=defect != "unread")
    arguments = payload()
    identity = decision["decision_id"]
    if defect == "unknown":
        arguments["findings"][0]["evidence_id"] = "forged_source"
    elif defect == "duplicate":
        arguments["findings"] *= 2
    elif defect == "blank":
        arguments["summary"] = " \n "
    elif defect == "extra":
        arguments["verified_by_lab"] = True
    elif defect == "no-decision":
        identity = None
    elif defect == "stale":
        identity = "f" * 64
    before = len(chain.prepared)
    result = operation(manager, run_id, "submit_investigation", arguments, expected=identity)
    assert result["status"] == "rejected"
    assert manager.observe(run_id)["investigations"] == []
    assert len(chain.prepared) == before  # Claims never trigger an implicit transaction.
    complete_record(manager, run_id)
    assert finish(manager, run_id)["verification"] == "fail"


def test_exact_retry_and_lab_restart_preserve_one_frozen_artifact(harness):
    chain, factory = harness
    first, run_id, decision = ready(factory)
    result = operation(first, run_id, "submit_investigation", payload(), key="findings-once",
                       expected=decision["decision_id"])
    assert result["status"] == "completed"
    artifact = first.observe(run_id)["investigations"][0]
    assert artifact["decision_snapshot"]["result"]["outcome"] == "yes"
    assert artifact["artifact_scope"] == "agent_authored_lab_report_only"
    assert artifact["citations"][0]["evidence_id"] == "settlement_record"
    assert len(artifact["citations"][0]["sha256"]) == 64
    first.close()
    second = factory()
    wait(lambda: second.observe(run_id)["status"], lambda status: status == "running")
    retried = second.invoke(run_id, "submit_investigation", payload(), "findings-once", decision["decision_id"])
    assert retried["result"] == result["result"]
    assert second.observe(run_id)["investigation_count"] == 1
    assert second.observe(run_id)["evidence_reads"] == {"settlement_record": 1}
    leaked = second.observe(run_id)
    leaked["investigations"][0]["summary"] = "mutated outside manager"
    assert second.observe(run_id)["investigations"][0] == artifact
    complete_record(second, run_id)
    report = finish(second, run_id)
    assert report["verification"] == "pass"
    assert report["investigations"] == [artifact]
    assert len([event for event in report["events"] if event["kind"] == "investigation_submitted"]) == 1
    assert all(secret not in json.dumps(report) for secret in chain.keys)
    assert "fixtures" not in second.observe(run_id)


def test_changed_claim_cannot_reuse_submitted_artifact_identity(harness):
    _, factory = harness
    manager, run_id, decision = ready(factory)
    operation(manager, run_id, "submit_investigation", payload(), key="same", expected=decision["decision_id"])
    changed = payload()
    changed["disposition"] = "appeal"
    with pytest.raises(ValueError, match="different arguments"):
        manager.invoke(run_id, "submit_investigation", changed, "same", decision["decision_id"])
    assert manager.observe(run_id)["investigations"][0]["disposition"] == "accept"
    complete_record(manager, run_id)
    assert finish(manager, run_id)["verification"] == "fail"


def test_submission_requires_explicit_operation_permission(harness):
    _, factory = harness
    manager = factory()
    run_id = start(manager)["run_id"]
    result = operation(manager, run_id, "submit_investigation", payload())
    assert result["error_code"] == "operation_policy_violated"
    assert manager.observe(run_id)["investigation_count"] == 0


def test_submission_model_bounds_untrusted_payload():
    invalid = payload()
    invalid["proposed_result"] = {"blob": "x" * 24001}
    with pytest.raises(ValueError):
        InvestigationSubmission.model_validate(invalid)
    invalid = payload()
    invalid["findings"][0]["note"] = " "
    with pytest.raises(ValueError):
        InvestigationSubmission.model_validate(invalid)


def test_artifact_preserves_original_decision_after_mutable_chain_observation_changes():
    evidence = {"id": "settlement_record", "content": "Record"}
    decision = {"decision_id": "d1", "result": {"outcome": "yes"}, "execution_success": True}
    operations = [{"operation": "read_evidence", "status": "completed", "execution_success": True, "result": evidence}]
    original = copy.deepcopy(decision)
    artifact = build_investigation(payload(), intent_key="one", decision=decision,
                                   operations=operations, declared_evidence=[evidence])
    decision["result"]["outcome"] = "no"
    assert artifact["decision_snapshot"]["result"] == original["result"]


def test_repeated_large_evidence_reads_cannot_suppress_failure_report(harness):
    _, factory = harness

    def large(draft):
        enabled(draft)
        draft["evidence"][0]["content"] = "x" * 15000
        draft["policy"]["operations"]["read_evidence"]["max_calls"] = 32

    manager = factory()
    run_id = start(manager, scenario(change=large))["run_id"]
    for index in range(20):
        operation(manager, run_id, "read_evidence", {"id": "settlement_record"}, key=f"read-{index}")
    report = finish(manager, run_id)
    assert len(json.dumps(report)) > 262144
    assert report["verification"] == "fail"
    assert report["evidence_reads"] == {"settlement_record": 20}
