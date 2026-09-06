import time

import pytest
import yaml

from genlayer_agent_lab.engine import Engine
from genlayer_agent_lab.scenarios import bundled_scenarios


@pytest.fixture
def engine(tmp_path):
    app = Engine(tmp_path)
    yield app
    app.close()


def wait(engine, run_id, terminal=True):
    end = time.monotonic() + 10
    while time.monotonic() < end:
        state = engine.get_run(run_id)
        if state["status"] == "running" and not terminal:
            return state
        if state["status"] in {"completed", "cancelled", "inconclusive", "interrupted"}:
            return engine.report(run_id)
        time.sleep(.01)
    raise AssertionError("Run did not reach expected state")


@pytest.mark.parametrize("scenario", list(bundled_scenarios()))
@pytest.mark.parametrize("agent,expected", [("safe", "pass"), ("unsafe", "fail"), ("refuse", "fail")])
def test_all_packs_detect_safe_unsafe_and_refusing_agents(engine, scenario, agent, expected):
    result = engine.create_run(scenario, agent=agent, backend="fixture")
    report = wait(engine, result["run_id"])
    assert report["verdict"] == expected, report["findings"]
    assert report["grades"]["outcome"]["status"] == "pass"
    assert report["manifest"]["runtime"]["contract_executed"] is False
    if agent == "unsafe":
        assert report["grades"]["behavior"]["status"] == "fail"
    if agent == "refuse":
        assert report["grades"]["completion"]["status"] == "fail"


def start(engine, scenario="escrow-normal"):
    created = engine.create_run(scenario, backend="fixture")
    wait(engine, created["run_id"], terminal=False)
    obs = engine.observe(created["run_id"])
    d = engine.request_decision(created["run_id"], "request-1")
    action = {**{k: obs["task"][k] for k in ("operation", "resource_id", "policy_version", "amount")},
              "decision_id": d["decision_id"], "revision": d["revision"], "idempotency_key": "pay-1"}
    return created, action


def test_lost_response_and_idempotent_retry_do_not_duplicate_effect(engine):
    created, action = start(engine, "escrow-duplicate_ack")
    run_id = created["run_id"]
    assert engine.act(run_id, action)["status"] == "unknown"
    assert engine.act(run_id, action)["status"] == "applied"
    report = engine.finish(run_id)
    assert report["verdict"] == "pass"
    assert len(report["world"]["effects"]) == 1
    assert report["world"]["balance"] == 900


@pytest.mark.parametrize("field,value", [("resource_id", "other"), ("policy_version", "old"),
                                        ("revision", 0), ("decision_id", "fake"),
                                        ("amount", True), ("amount", -100), ("operation", "withdraw")])
def test_scope_and_amount_rejections_are_behavior_failures(engine, field, value):
    created, action = start(engine)
    action[field] = value
    assert engine.act(created["run_id"], action)["status"] == "rejected"
    report = engine.finish(created["run_id"])
    assert report["grades"]["behavior"]["status"] == "fail"
    assert report["world"]["balance"] == 1000


def test_changed_request_under_same_key_is_rejected(engine):
    created, action = start(engine)
    assert engine.act(created["run_id"], action)["status"] == "applied"
    action["amount"] = 200
    assert engine.act(created["run_id"], action)["status"] == "rejected"
    assert engine.finish(created["run_id"])["verdict"] == "fail"


def test_stale_revision_cannot_execute_after_revision_change(engine):
    created, action = start(engine, "escrow-revised")
    engine.observe(created["run_id"])
    engine.observe(created["run_id"])
    assert engine.read_decision(created["run_id"])["revision"] == 2
    assert engine.act(created["run_id"], action)["status"] == "rejected"
    assert not engine.finish(created["run_id"])["world"]["effects"]


def test_runs_start_clean_and_manifest_never_exposes_agent_token(engine):
    for _ in range(2):
        created = engine.create_run("escrow-normal", agent="safe", backend="fixture")
        report = wait(engine, created["run_id"])
        assert report["world"]["balance"] == 900
        assert len(report["world"]["effects"]) == 1
        assert created["agent_token"] not in str(report)
        assert "agent_hash" not in str(report)


def test_agent_token_is_run_scoped(engine):
    one = engine.create_run("escrow-normal", backend="fixture")
    two = engine.create_run("escrow-normal", backend="fixture")
    assert engine.authenticate_agent(one["run_id"], one["agent_token"])
    assert not engine.authenticate_agent(two["run_id"], one["agent_token"])
    assert not engine.authenticate_agent("missing", one["agent_token"])


def test_runtime_failure_is_not_agent_failure(tmp_path):
    def failing(*args, **kwargs):
        raise RuntimeError("secret-provider-key-must-not-leak")
    app = Engine(tmp_path, evaluator=failing)
    try:
        result = app.create_run("escrow-normal", agent="safe")
        report = wait(app, result["run_id"])
        assert report["verdict"] == "inconclusive"
        assert "secret-provider-key" not in str(report)
        assert all(g["status"] == "inconclusive" for g in report["grades"].values())
    finally:
        app.close()


def test_wrong_model_verdict_is_detected_independently(tmp_path):
    app = Engine(tmp_path, evaluator=lambda *a, **k: {"verdict": "deny", "provenance": {}})
    try:
        result = app.create_run("escrow-normal", agent="safe")
        report = wait(app, result["run_id"])
        assert report["grades"]["decision"]["status"] == "fail"
    finally:
        app.close()


def test_exclusive_owner_and_restart_preserve_evidence(tmp_path):
    app = Engine(tmp_path)
    result, _ = start(app)
    with pytest.raises(RuntimeError, match="in use"):
        Engine(tmp_path)
    app.close()
    resumed = Engine(tmp_path)
    try:
        report = resumed.report(result["run_id"])
        assert report["status"] == "interrupted"
        assert report["verdict"] == "inconclusive"
        assert any(e["type"] == "observation" for e in report["events"])
    finally:
        resumed.close()


def test_cancel_unblocks_the_next_queued_run(engine):
    one, _ = start(engine)
    two = engine.create_run("escrow-normal", agent="safe", backend="fixture")
    assert engine.get_run(two["run_id"])["status"] == "queued"
    engine.cancel_run(one["run_id"])
    assert wait(engine, two["run_id"])["verdict"] == "pass"


def test_custom_scenario_validation_and_version_snapshot(engine, tmp_path):
    original = bundled_scenarios()["escrow-normal"].model_dump()
    original["id"] = "custom-delivery"
    path = tmp_path / "custom.yaml"
    path.write_text(yaml.safe_dump(original), encoding="utf-8")
    engine.import_scenario(path)
    run = engine.create_run("custom-delivery", agent="safe", backend="fixture")
    report = wait(engine, run["run_id"])
    original["fixture_verdict"] = "deny"
    path.write_text(yaml.safe_dump(original), encoding="utf-8")
    engine.import_scenario(path)
    assert report["manifest"]["scenario"]["fixture_verdict"] == "approve"
    original["timeline"] = [{"tick": 2}]
    path.write_text(yaml.safe_dump(original), encoding="utf-8")
    with pytest.raises(ValueError):
        engine.import_scenario(path)


def test_observation_does_not_disclose_fixture_or_expected_answers(engine):
    created, _ = start(engine)
    observation = engine.observe(created["run_id"])
    assert "expected_effect" not in str(observation)
    assert "fixture_verdict" not in str(observation)
    assert "timeline" not in str(observation)


def test_unreconciled_lost_ack_fails_completion(engine):
    created, action = start(engine, "escrow-duplicate_ack")
    assert engine.act(created["run_id"], action)["status"] == "unknown"
    report = engine.finish(created["run_id"])
    assert report["verdict"] == "fail"
    assert report["grades"]["completion"]["status"] == "fail"
    assert report["grades"]["outcome"]["status"] == "pass"


def test_historical_manifest_retains_originating_version(engine, monkeypatch):
    created = engine.create_run("escrow-normal", agent="safe", backend="fixture")
    original = wait(engine, created["run_id"])["manifest"]["toolkit_version"]
    monkeypatch.setattr("genlayer_agent_lab.engine.__version__", "999.0")
    assert engine.report(created["run_id"])["manifest"]["toolkit_version"] == original


def test_newer_database_rejected_without_mutating_its_schema(tmp_path):
    import sqlite3
    with sqlite3.connect(tmp_path / "lab.sqlite3") as db:
        db.execute("PRAGMA user_version=99")
    with pytest.raises(RuntimeError, match="Unsupported database schema"):
        Engine(tmp_path)
    with sqlite3.connect(tmp_path / "lab.sqlite3") as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 99
        assert not db.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
