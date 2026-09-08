"""Missing execution observations cannot establish faulty agent behavior."""

import pytest
from test_project_workflows import FakeClient, complete_record, finish, operation, start
from test_project_workflows import harness as harness


@pytest.mark.parametrize("execution_success, expected", [(None, "inconclusive"), (False, "fail")])
def test_required_write_distinguishes_unknown_from_failed_execution(harness, monkeypatch,
                                                                  execution_success, expected):
    chain, factory = harness
    original = FakeClient.transaction

    def incomplete_receipt(client, tx_id):
        receipt = original(client, tx_id)
        prepared = next((item for item in chain.prepared if item["tx_id"] == tx_id), None)
        if prepared and prepared["method"] == "record":
            receipt.update(execution_success=execution_success, raw_result=None)
        return receipt

    monkeypatch.setattr(FakeClient, "transaction", incomplete_receipt)
    manager = factory()
    run_id = start(manager)["run_id"]
    operation(manager, run_id, "resolve", {"evidence": "yes"})
    complete_record(manager, run_id)
    report = finish(manager, run_id)
    assert report["verification"] == expected
    checks = {check["id"]: check for check in report["checks"]}
    assert checks["execution_evidence"]["outcome"] == ("inconclusive" if execution_success is None else "pass")
    assert checks["required_record"]["outcome"] == "fail"
    # Matching final state does not fill in the missing transaction result.
    assert report["state"]["record_state"]["recorded"] is True


@pytest.mark.parametrize("status, success, expected", [
    ("FINALIZED", None, "inconclusive"),
    ("FINALIZED", False, "pass"),
    ("CANCELED", None, "pass"),
])
def test_child_outcome_unknown_failure_and_cancellation_are_distinct(harness, monkeypatch,
                                                                  status, success, expected):
    chain, factory = harness
    chain.child_failure = True
    original = FakeClient.transaction
    child_id = "0x" + "f" * 64

    def child_receipt(client, tx_id):
        receipt = original(client, tx_id)
        if tx_id == child_id:
            receipt.update(status=status, execution_success=success, raw_result=None)
        return receipt

    monkeypatch.setattr(FakeClient, "transaction", child_receipt)
    manager = factory()
    run_id = start(manager)["run_id"]
    operation(manager, run_id, "resolve", {"evidence": "yes"})
    complete_record(manager, run_id)
    report = finish(manager, run_id)
    checks = {check["id"]: check for check in report["checks"]}
    assert report["verification"] == expected
    assert checks["child_effects"]["outcome"] == expected
    assert checks["execution_evidence"]["outcome"] == expected
    assert report["transactions"][child_id]["execution_success"] is success
    assert report["transactions"][child_id]["status"] == status
    child_summary = report["child_effects"]["by_operation"]["resolve"]
    assert child_summary["pending"] == (1 if status == "FINALIZED" and success is None else 0)
    assert child_summary["failed"] == (0 if status == "FINALIZED" and success is None else 1)
    # This scenario has no required child business effect. A known child failure
    # or cancellation is retained, while its successfully executed parent passes.
    assert checks["required_record"]["outcome"] == "pass"
