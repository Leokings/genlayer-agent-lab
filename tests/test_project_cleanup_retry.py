"""Recovery of an unresolved cleanup never restarts the failed agent task."""

import copy

import pytest
from test_project_workflows import FakeChain, FakeCohort, scenario, start, wait

from genlayer_agent_lab.api import initialize_data_dir
from genlayer_agent_lab.project_workflows import ProjectWorkflowManager
from genlayer_agent_lab.store import Store


@pytest.fixture
def retry_harness(tmp_path):
    data_dir = initialize_data_dir(tmp_path / "lab")
    store = Store(data_dir)
    chain = FakeChain(store, data_dir)
    managers = []

    def factory(*, cleanup_timeout=.1):
        manager = ProjectWorkflowManager(store, data_dir, client_factory=chain.factory,
            cohort_factory=FakeCohort, poll_interval=.01, cleanup_timeout=cleanup_timeout)
        managers.append(manager)
        return manager

    yield chain, factory
    for manager in managers:
        manager.close()
    store.close()


def _unresolved(chain, factory):
    chain.hold_resolve = True
    manager = factory()
    created = start(manager)
    run_id = created["run_id"]
    manager.invoke(run_id, "resolve", {"evidence": "yes"}, "resolve-once")
    wait(lambda: manager.observe(run_id)["operations"],
         lambda items: any(item["transaction_status"] == "ACCEPTED" for item in items))
    manager.finish(run_id)
    wait(lambda: manager.report(run_id), lambda report: report["cleanup"] == "unresolved")
    manager.close()
    return manager, created


def test_startup_cleanup_waits_for_settlement_without_resuming_task(retry_harness):
    chain, factory = retry_harness
    manager, created = _unresolved(chain, factory)
    run_id = created["run_id"]
    original = manager.report(run_id)
    prepared = copy.deepcopy(chain.prepared)
    submissions = copy.deepcopy(chain.submissions)

    restarted = factory(cleanup_timeout=2)
    wait(lambda: chain.cohort_recovered, lambda value: value == 1)
    with pytest.raises(ValueError, match="unfinished"):
        restarted.create(scenario())
    with pytest.raises(ValueError, match="not accepting"):
        restarted.invoke(run_id, "record", {"expected_revision": 1, "outcome": "yes"}, "new-record")
    assert restarted.get(run_id)["cleanup"] != "restored"
    assert chain.cohort_closed == 0

    chain.finalize_resolution()
    recovered = wait(lambda: restarted.report(run_id), lambda report: report["cleanup"] == "restored")
    wait(lambda: restarted._active_id is None)
    assert recovered["status"] == "inconclusive"
    assert recovered["verification"] == "inconclusive"
    assert recovered["error_code"] == original["error_code"] == "cleanup_deadline_exceeded"
    assert recovered["contracts"] == original["contracts"]
    assert set(recovered["transactions"]) == set(original["transactions"])
    assert chain.prepared == prepared and chain.submissions == submissions
    assert chain.oracle["revision"] == 1 and chain.record["record_count"] == 0
    assert chain.keys[0] == chain.keys[1]
    assert chain.cohort_closed == 1 and chain.fixture == {}
    assert any(event["kind"] == "cleanup_recovery_started" for event in recovered["events"])
    # Successful cleanup removes the install-wide block, while this run stays failed.
    following = start(restarted)
    restarted.cancel(following["run_id"])


@pytest.mark.parametrize("alteration", ["no_signer", "restored_history", "different_backend"])
def test_cleanup_retry_refuses_history_without_original_runtime_identity(retry_harness, alteration):
    chain, factory = retry_harness
    manager, created = _unresolved(chain, factory)
    run_id = created["run_id"]
    saved = manager._runs[run_id]
    if alteration == "no_signer":
        saved.pop("private_account")
    elif alteration == "restored_history":
        saved["error_code"] = "interrupted_by_restore"
    else:
        chain.provenance["owner"] = "another-owned-stack"
    manager._save(saved)
    chain.finalize_resolution()
    restarted = factory()
    if alteration == "different_backend":
        wait(lambda: restarted._active_id is None)
        assert restarted.report(run_id)["error_code"] == "workflow_backend_identity_changed"
    else:
        assert restarted._thread is None
    assert restarted.report(run_id)["cleanup"] == "unresolved"
    assert chain.cohort_closed == 0
    assert chain.cohort_recovered == 0
    with pytest.raises(ValueError, match="unfinished"):
        restarted.create(scenario())
