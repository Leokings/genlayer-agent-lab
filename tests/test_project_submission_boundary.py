"""Stop requests and expired deadlines forbid new durable transaction identities."""

import threading
import time

import pytest
from test_project_workflows import FakeClient, scenario, start, wait
from test_project_workflows import harness as harness


@pytest.mark.parametrize("stage,ending", [
    (stage, ending) for stage in ("deployment", "agent_write")
    for ending in ("cancel", "deadline", "finish")
] + [("deployment", "setup_deadline")])
def test_run_ending_during_preparation_prevents_new_submission(harness, monkeypatch, stage, ending):
    chain, factory = harness
    manager = factory()
    entered, release = threading.Event(), threading.Event()
    method = "prepare_deploy" if stage == "deployment" else "prepare_write"
    original = getattr(FakeClient, method)

    def delayed(client, *args, **kwargs):
        prepared = original(client, *args, **kwargs)
        entered.set()
        assert release.wait(5), "Test did not release the controlled preparation"
        return prepared

    if stage == "agent_write":
        run_id = start(manager)["run_id"]
    monkeypatch.setattr(FakeClient, method, delayed)
    if stage == "deployment":
        run_id = manager.create(scenario(), wait_for_agent=ending == "setup_deadline")["run_id"]
    else:
        manager.invoke(run_id, "resolve", {"evidence": "yes"}, "delayed-resolve")
    assert entered.wait(5), "Worker did not reach the controlled preparation"
    submitted_before = len(chain.submissions)
    try:
        if ending in {"deadline", "setup_deadline"}:
            with manager._lock:
                manager._runs[run_id][ending + "_at"] = time.time() - 1
        else:
            getattr(manager, ending)(run_id)
    finally:
        release.set()
    ended = wait(lambda: manager.get(run_id),
                 lambda value: value["status"] in {"completed", "cancelled", "inconclusive"})
    assert len(chain.submissions) == submitted_before
    saved = chain.latest_persisted()
    blocked = next(intent for intent in saved["intents"].values()
                   if intent["operation"] == ("$deploy:oracle" if stage == "deployment" else "resolve"))
    assert blocked["status"] == "cancelled" and blocked["tx_id"] is None
    assert blocked["prepared"] is None and blocked["submission_attempts"] == 0
    assert saved["fee_reserved"] == 0
    assert ended["cleanup"] == "restored"
    assert ended["status"] == {"cancel": "cancelled", "deadline": "inconclusive",
                               "setup_deadline": "inconclusive", "finish": "completed"}[ending]
    if ending in {"deadline", "setup_deadline"}:
        assert ended["error_code"] == ("connection_setup_deadline_exceeded" if ending == "setup_deadline"
                                       else "deadline_exceeded")


def test_cancel_after_durable_submission_preserves_identical_reconciliation(harness, monkeypatch):
    chain, factory = harness
    manager = factory()
    run_id = start(manager)["run_id"]
    original = FakeClient.submit
    chain.fail_before_submit = "resolve"

    def cancel_during_dispatch(client, prepared):
        if prepared["method"] == "resolve":
            manager.cancel(run_id)
        return original(client, prepared)

    monkeypatch.setattr(FakeClient, "submit", cancel_during_dispatch)
    manager.invoke(run_id, "resolve", {"evidence": "yes"}, "resolve")
    ended = wait(lambda: manager.get(run_id), lambda value: value["status"] == "cancelled")
    attempts = [item for item in chain.submissions if item["method"] == "resolve"]
    assert len(attempts) == 2 and attempts[0] == attempts[1]
    saved = chain.latest_persisted()
    assert saved["fee_reserved"] == 7  # Reconciliation does not reserve twice.
    assert saved["intents"]["resolve"]["status"] == "completed"
    assert ended["cleanup"] == "restored"


def test_shutdown_before_journal_keeps_unsubmitted_action_queued_for_resume(harness, monkeypatch):
    chain, factory = harness
    manager = factory()
    run_id = start(manager)["run_id"]
    entered, release = threading.Event(), threading.Event()
    original = FakeClient.prepare_write

    def delayed(client, *args, **kwargs):
        prepared = original(client, *args, **kwargs)
        entered.set()
        assert release.wait(5)
        return prepared

    monkeypatch.setattr(FakeClient, "prepare_write", delayed)
    manager.invoke(run_id, "resolve", {"evidence": "yes"}, "resolve")
    assert entered.wait(5)
    closer = threading.Thread(target=manager.close)
    closer.start()
    try:
        wait(manager._stop.is_set)
    finally:
        release.set()
        closer.join(5)
    assert not closer.is_alive()
    saved = chain.latest_persisted()
    assert saved["status"] == "paused" and saved["fee_reserved"] == 0
    assert saved["intents"]["resolve"]["status"] == "queued"
    assert saved["intents"]["resolve"]["tx_id"] is None
    assert all(item["method"] != "resolve" for item in chain.submissions)
    resumed = factory()
    wait(lambda: resumed.observe(run_id)["operations"][0]["status"], lambda status: status == "completed")
    assert len([item for item in chain.submissions if item["method"] == "resolve"]) == 1
    resumed.cancel(run_id)
    wait(lambda: resumed.get(run_id)["status"], lambda status: status == "cancelled")
