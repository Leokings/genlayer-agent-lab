"""Connection preparation is distinct from a bounded, durable behavioral run."""

import threading
import time
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from test_project_workflows import harness as harness
from test_project_workflows import scenario, wait

from genlayer_agent_lab.api import create_app, read_admin_token


def awaiting(manager):
    created = manager.create(scenario(), wait_for_agent=True)
    run_id = created["run_id"]
    wait(lambda: manager.get(run_id), lambda r: r["status"] == "awaiting_agent")
    return created


def test_admin_inspection_does_not_start_timer_and_observe_starts_once(harness):
    _, factory = harness
    manager = factory()
    created = awaiting(manager)
    run_id = created["run_id"]
    for _ in range(2):
        assert manager.get(run_id)["timing"]["phase"] == "setup"
        assert manager.report(run_id)["timing"]["deadline_at"] is None
    assert manager.authenticate(run_id, created["agent_token"])
    assert manager.get(run_id)["timing"]["started_at"] is None
    with pytest.raises(ValueError, match="not accepting"):
        manager.invoke(run_id, "read_evidence", {"id": "settlement_record"}, "premature")
    started = manager.observe(run_id)
    assert started["status"] == "running"
    timing = started["timing"]
    assert timing["phase"] == "test"
    assert timing["deadline_at"] - timing["started_at"] == started["timeout_seconds"]
    assert manager.observe(run_id)["timing"]["deadline_at"] == timing["deadline_at"]
    assert len([e for e in manager.report(run_id)["events"] if e["kind"] == "test_started"]) == 1


def test_waiting_connection_survives_service_restart_without_start_or_extension(harness):
    _, factory = harness
    manager = factory()
    created = awaiting(manager)
    run_id = created["run_id"]
    setup_deadline = manager.get(run_id)["timing"]["setup_deadline_at"]
    manager.close()
    resumed = factory()
    wait(lambda: resumed.get(run_id), lambda r: r["status"] == "awaiting_agent")
    assert resumed.get(run_id)["timing"]["started_at"] is None
    assert resumed.get(run_id)["timing"]["setup_deadline_at"] == setup_deadline
    started = resumed.observe(run_id)["timing"]
    resumed.close()
    again = factory()
    wait(lambda: again.get(run_id), lambda r: r["status"] == "running")
    assert again.observe(run_id)["timing"]["deadline_at"] == started["deadline_at"]


def test_expired_setup_cleans_up_without_becoming_a_behavioral_run(harness):
    _, factory = harness
    manager = factory()
    created = awaiting(manager)
    run_id = created["run_id"]
    with manager._lock:
        manager._runs[run_id]["setup_deadline_at"] = time.time() - 1
        manager._save(manager._runs[run_id])
    manager.observe(run_id)
    ended = wait(lambda: manager.get(run_id), lambda r: r["status"] == "inconclusive")
    assert ended["error_code"] == "connection_setup_deadline_exceeded"
    assert ended["timing"]["started_at"] is None
    assert ended["cleanup"] == "restored"
    assert manager.observe(run_id)["status"] == "inconclusive"


def test_cancelling_connection_preparation_restores_owned_fixtures(harness):
    _, factory = harness
    manager = factory()
    run_id = awaiting(manager)["run_id"]
    manager.cancel(run_id)
    ended = wait(lambda: manager.get(run_id), lambda r: r["status"] == "cancelled")
    assert ended["timing"]["started_at"] is None
    assert ended["cleanup"] == "restored"


def test_only_authenticated_agent_observe_starts_via_http(harness, tmp_path):
    _, factory = harness
    manager = factory()
    app = create_app(tmp_path / "http", engine=SimpleNamespace(workflows=manager))
    admin = {"Authorization": "Bearer " + read_admin_token(tmp_path / "http")}
    with TestClient(app, base_url="http://127.0.0.1") as client:
        created = client.post("/v1/workflows", headers=admin,
                              json={"spec": scenario(), "wait_for_agent": True})
        assert created.status_code == 201, created.text
        run = created.json()
        base = "/v1/workflows/" + run["run_id"]
        wait(lambda: client.get(base, headers=admin).json(), lambda r: r["status"] == "awaiting_agent")
        for headers in ({}, admin, {"Authorization": "Bearer wrong"}):
            assert client.post(base + "/observe", headers=headers).status_code == 401
        assert client.get(base + "/report", headers=admin).json()["timing"]["started_at"] is None
        agent = {"Authorization": "Bearer " + run["agent_token"]}
        observed = client.post(base + "/observe", headers=agent)
        assert observed.status_code == 200
        assert observed.json()["timing"]["phase"] == "test"
        assert observed.json()["status"] == "running"


def test_observation_during_preparation_waits_for_runtime_readiness(harness):
    chain, factory = harness
    original = chain.factory
    release = threading.Event()

    def delayed(*args):
        assert release.wait(5)
        return original(*args)

    manager = factory()
    manager._client_factory = delayed
    try:
        run = manager.create(scenario(), wait_for_agent=True)
        observed = manager.observe(run["run_id"])
        assert observed["status"] == "preparing"
        assert observed["timing"]["started_at"] is None
        release.set()
        ready = wait(lambda: manager.get(run["run_id"]), lambda r: r["status"] == "running")
        assert ready["timing"]["phase"] == "test"
        assert ready["timing"]["deadline_at"] - ready["timing"]["started_at"] == ready["timeout_seconds"]
    finally:
        release.set()


def test_observe_during_recovery_waits_for_current_worker_readiness(harness):
    chain, factory = harness
    manager = factory()
    run_id = awaiting(manager)["run_id"]
    setup_deadline = manager.get(run_id)["timing"]["setup_deadline_at"]
    manager.close()
    # Stopping an old manager must not arm a run, either.
    assert manager.observe(run_id)["timing"]["started_at"] is None
    original = chain.factory
    release = threading.Event()

    def delayed(*args):
        assert release.wait(5)
        return original(*args)

    chain.factory = delayed
    try:
        resumed = factory()
        observed = resumed.observe(run_id)
        assert observed["status"] == "recovering"
        assert observed["timing"]["started_at"] is None
        assert observed["timing"]["setup_deadline_at"] == setup_deadline
        release.set()
        ready = wait(lambda: resumed.get(run_id), lambda r: r["status"] == "running")
        assert ready["timing"]["deadline_at"] - ready["timing"]["started_at"] == ready["timeout_seconds"]
    finally:
        release.set()
