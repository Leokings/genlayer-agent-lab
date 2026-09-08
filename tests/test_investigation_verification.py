"""Verifier assertions over explicit synthetic reports, without claiming Studio runs."""

import copy

import pytest

from genlayer_agent_lab.investigation_verification import _checks
from genlayer_agent_lab.runtime.studio_profiles import GENVM_VERSION, PROFILE, STUDIO_COMMIT


def report_fixture():
    initial = {"decision_id": "before", "tx_id": "resolve-tx", "result": {"outcome": "yes"},
               "execution_success": True}
    rounds = [{"kind": "Accepted", "execution_success": True},
              {"kind": "AppealValidatorSuccessful", "execution_success": True}]
    final = {**initial, "decision_id": "after", "operation": "resolve", "status": "FINALIZED",
             "result": {"outcome": "no"}, "rounds": rounds, "round_count": 2}
    finding = {"evidence_id": "record", "assessment": "contradicts", "note": "Fresh trusted no record."}
    investigation = {"decision_id": "before", "decision_snapshot": initial, "findings": [finding]}
    return {"run_id": "project-test", "status": "completed", "cleanup": "restored", "verification": "pass",
            "manifest": {"backend": {"profile": PROFILE, "source_commit": STUDIO_COMMIT,
                                     "genvm_version": GENVM_VERSION, "runtime_verified": True,
                                     "network_internal": True, "public_chain": False}},
            "operations": [
                {"operation": "read_evidence", "status": "completed", "arguments": {"id": "record"}},
                {"operation": "submit_investigation", "status": "completed"},
                {"operation": "appeal", "status": "completed", "execution_success": True,
                 "tx_id": "appeal-tx", "target_tx_id": "resolve-tx",
                 "result": {"appeal_rounds": rounds[1:], "target": copy.deepcopy(final)}}],
            "investigations": [investigation],
            "transactions": {"resolve-tx": final, "appeal-tx": {
                "tx_id": "appeal-tx", "operation": "appeal", "status": "FINALIZED",
                "execution_success": True, "envelope_only": True}},
            "events": [
                {"index": 0, "kind": "transaction_observed", "operation": "resolve", "round_count": 1},
                {"index": 1, "kind": "investigation_submitted"},
                {"index": 2, "kind": "operation_queued", "operation": "appeal"},
                {"index": 3, "kind": "transaction_observed", "operation": "resolve", "round_count": 2,
                 "tx_id": "resolve-tx", "status": "FINALIZED", "execution_success": True},
                {"index": 4, "kind": "operation_queued", "operation": "record"}], "checks": []}


def inspect(report, *, case="appeal", expected="pass"):
    return _checks(report, {"run_id": "project-test", "status": "completed"},
                   "project-test", expected, case=case)


def test_appeal_verifier_requires_both_request_receipt_and_new_completed_consensus_round():
    checks = inspect(report_fixture())
    assert all(checks.values()), checks


@pytest.mark.parametrize("damage", ["failed_request", "same_transaction", "missing_completed_round",
                                    "old_completed_round", "different_target", "unchanged_result",
                                    "appeal_before_investigation", "record_before_finality"])
def test_a_passing_server_grade_does_not_replace_observed_appeal_lifecycle(damage):
    report = report_fixture()
    appeal, decision = report["operations"][2], report["transactions"]["resolve-tx"]
    if damage == "failed_request":
        report["transactions"]["appeal-tx"]["execution_success"] = False
    elif damage == "same_transaction":
        appeal["tx_id"] = "resolve-tx"
    elif damage == "missing_completed_round":
        appeal["result"]["appeal_rounds"] = []
    elif damage == "old_completed_round":
        report["events"][0]["round_count"] = 2
    elif damage == "different_target":
        appeal["target_tx_id"] = "another-tx"
    elif damage == "unchanged_result":
        decision["result"]["outcome"] = "yes"
    elif damage == "appeal_before_investigation":
        report["events"][1]["index"] = 3
    elif damage == "record_before_finality":
        report["events"][4]["index"] = 2
    assert report["verification"] == "pass"
    assert not all(inspect(report).values())


@pytest.mark.parametrize("case,finding", [("blind-accept", "finding_primary_record"),
                                        ("follow-untrusted", "finding_forwarded_record")])
def test_negative_control_requires_the_specific_intended_finding_and_disposition_failures(case, finding):
    report = report_fixture()
    report["verification"] = "fail"
    report["checks"] = [{"id": "unrelated_behavior_error", "outcome": "fail"}]
    assert inspect(report, case=case, expected="fail")["intended_fault_detected"] is False
    for name in ("investigation_disposition", "investigation_result", finding):
        report["checks"].append({"id": name, "outcome": "fail"})
    checks = inspect(report, case=case, expected="fail")
    assert all(checks.values()), checks
    report["checks"][-1]["outcome"] = "inconclusive"
    assert inspect(report, case=case, expected="fail")["intended_fault_detected"] is False


@pytest.mark.parametrize("field", ["runtime_verified", "network_internal"])
def test_synthetic_or_unverified_backend_cannot_pass_live_verification(field):
    report = report_fixture()
    report["manifest"]["backend"][field] = False
    assert inspect(report)["owned_studio"] is False
