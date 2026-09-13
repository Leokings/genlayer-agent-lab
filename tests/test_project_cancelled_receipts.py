"""Canceled transactions cannot satisfy successful-action or finality requirements."""

import copy

import pytest
from test_project_workflows import (
    FakeClient,
    complete_record,
    finish,
    operation,
    scenario,
    start,
    wait,
)
from test_project_workflows import harness as harness


@pytest.mark.parametrize("execution_success", [True, False, None])
@pytest.mark.parametrize("require_finalized", [True, False])
def test_canceled_write_never_counts_as_success_even_with_matching_state(
        harness, monkeypatch, execution_success, require_finalized):
    chain, factory = harness
    original = FakeClient.transaction

    def canceled_receipt(client, tx_id):
        receipt = original(client, tx_id)
        prepared = next((item for item in chain.prepared if item["tx_id"] == tx_id), None)
        if prepared and prepared["method"] == "record":
            # A provisional leader result can survive cancellation in Studio's
            # receipt; matching state is not proof this required write finalized.
            receipt.update(status="CANCELED", execution_success=execution_success)
        return receipt

    def requirements(draft):
        draft["expectations"]["require_finalized"] = require_finalized

    monkeypatch.setattr(FakeClient, "transaction", canceled_receipt)
    manager = factory()
    run_id = start(manager, scenario(change=requirements))["run_id"]
    operation(manager, run_id, "resolve", {"evidence": "yes"})
    result = complete_record(manager, run_id)
    report = finish(manager, run_id)
    assert result["status"] == "failed" and result["error_code"] == "transaction_canceled"
    checks = {check["id"]: check for check in report["checks"]}
    assert checks["required_record"]["outcome"] == "fail"
    if require_finalized:
        assert checks["transactions_finalized"]["outcome"] == "fail"
    assert report["verification"] == "fail" and report["cleanup"] == "restored"


def test_canceled_appeal_target_settles_without_inventing_a_completed_round(harness):
    chain, factory = harness
    chain.hold_resolve = True
    manager = factory()
    run_id = start(manager, scenario("appeal_changed"))["run_id"]
    manager.invoke(run_id, "resolve", {"evidence": "record says yes"}, "resolve")
    decision = wait(lambda: manager.observe(run_id)["operations"],
                    lambda ops: ops and ops[0]["transaction_status"] == "ACCEPTED")[0]
    manager.appeal(run_id, "appeal", decision["decision_id"])
    wait(lambda: manager.observe(run_id)["operations"],
         lambda ops: len(ops) == 2 and ops[1].get("transaction_status") == "FINALIZED")
    with chain.lock:
        chain.tx[decision["tx_id"]]["status"] = "CANCELED"
    appeal = wait(lambda: manager.observe(run_id)["operations"][1], lambda op: op["status"] == "failed")
    assert appeal["error_code"] == "appeal_round_not_observed"
    report = finish(manager, run_id)
    assert report["status"] == "completed" and report["cleanup"] == "restored"
    assert report["verification"] == "fail"


@pytest.mark.parametrize("status, execution_success", [
    ("CANCELED", True), ("CANCELED", None), ("FINALIZED", False),
])
def test_terminal_failed_deployment_ends_promptly_and_restores_cleanup(
        harness, monkeypatch, status, execution_success):
    chain, factory = harness
    original = FakeClient.transaction

    def failed_deployment(client, tx_id):
        receipt = original(client, tx_id)
        prepared = next(item for item in chain.prepared if item["tx_id"] == tx_id)
        if prepared["method"] == "deploy":
            receipt.update(status=status, execution_success=execution_success)
        return receipt

    monkeypatch.setattr(FakeClient, "transaction", failed_deployment)
    manager = factory()
    run_id = manager.create(scenario())["run_id"]
    wait(lambda: manager.get(run_id), lambda run: run["status"] == "inconclusive")
    report = manager.report(run_id)
    assert report["error_code"] == "deployment_not_successfully_finalized"
    assert report["cleanup"] == "restored"
    assert report["contracts"] == {}
    assert len(chain.submissions) == 1
    receipt = next(iter(report["transactions"].values()))
    assert receipt["status"] == status and receipt["execution_success"] is execution_success
    assert chain.cohort_closed == 1 and chain.cohort_abandoned == 0


@pytest.mark.parametrize("status", ["CANCELED", "FINALIZED"])
def test_resume_terminal_failed_deployment_does_not_wait_for_run_deadline(harness, monkeypatch, status):
    chain, factory = harness
    manager = factory()
    transaction = FakeClient.transaction
    save = manager._save
    snapshots = []

    def failed_deployment(client, tx_id):
        return {**transaction(client, tx_id), "status": status, "execution_success": False}

    def save_with_crash_snapshot(run):
        if (not snapshots and run["error_code"] is None and run["transactions"]
                and all(receipt["status"] == status for receipt in run["transactions"].values())):
            snapshots.append(copy.deepcopy(run))
        save(run)

    monkeypatch.setattr(FakeClient, "transaction", failed_deployment)
    monkeypatch.setattr(manager, "_save", save_with_crash_snapshot)
    run_id = manager.create(scenario())["run_id"]
    wait(lambda: manager.get(run_id)["status"], lambda value: value == "inconclusive")
    manager.close()
    # Replay the durable state at an interruption after receipt commit but
    # before the worker recorded the deployment failure or performed cleanup.
    assert len(snapshots) == 1 and snapshots[0]["cleanup"] == "required"
    save(snapshots[0])
    resumed = factory()
    wait(lambda: resumed.get(run_id)["status"], lambda value: value == "inconclusive")
    report = resumed.report(run_id)
    assert report["error_code"] == "deployment_not_successfully_finalized"
    assert report["cleanup"] == "restored" and report["contracts"] == {}
    assert len(chain.submissions) == 1
