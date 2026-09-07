import copy
import hashlib
import json
import threading
import time
from types import SimpleNamespace

import pytest

from genlayer_agent_lab.bindings import _snapshot
from genlayer_agent_lab.runtime.studio import StudioError
from genlayer_agent_lab.store import Store
from genlayer_agent_lab.workflow_bindings import bundled_workflow_snapshot
from genlayer_agent_lab.workflows import WorkflowManager, WorkflowSpec


def spec(**changes):
    return {"schema_version": 1, "profile": "service_release",
            "context": {"resource_id": "service-001", "policy_version": "v1", "amount": 100,
                        "evidence": "Developer-supplied delivery record"},
            "initial_fixture": {"decision": "partial", "authorized_amount": 40},
            "expectations": {"final_state": {"decision": "partial", "authorized_amount": 40,
                                                "released_amount": 40, "remaining_amount": 60},
                             "required_actions": ["evaluate", "get_state", "release"]},
            "timeout_seconds": 5, **changes}


def wait(function, predicate=lambda value: bool(value), timeout=3):
    deadline = time.monotonic() + timeout
    value = None
    while time.monotonic() < deadline:
        value = function()
        if predicate(value):
            return value
        time.sleep(0.005)
    raise AssertionError(value)


class FakeClient:
    """Controllable protocol evidence; never invent finality from a fixture."""

    def __init__(self, store):
        self.store = store
        self._provider = SimpleNamespace(on_submission=None, on_submission_attempt=None)
        self.timeout = 30
        self.transactions = {}
        self.calls = []
        self.fixture = None
        self.state = None
        self.closed = False
        self.fail_write = False
        self.fail_before_callback = False
        self.fail_before_dispatch = False
        self.hold_deployment = False
        self.appeal_changes_result = True
        self.immediate_appeal_final = False
        self.read_fails = False
        self._lock = threading.RLock()

    def _submitted(self, operation):
        with self.store.connection() as db:
            run = json.loads(db.execute("SELECT body FROM workflow_runs ORDER BY rowid DESC LIMIT 1").fetchone()[0])
        intents = [i for i in run["intents"].values() if i["operation"] == operation and i["status"] == "submitting"]
        assert len(intents) == 1, "The submission intent must be durable before touching the provider"
        key = intents[0]["idempotency_key"]
        if self.fail_before_dispatch:
            raise StudioError("deployed_source_mismatch")
        self._provider.on_submission_attempt()
        with self.store.connection() as db:
            dispatched = json.loads(db.execute("SELECT body FROM workflow_runs WHERE id=?", (run["run_id"],)).fetchone()[0])
        assert dispatched["intents"][key]["dispatch_started"] is True
        if self.fail_before_callback:
            raise RuntimeError("Authorization: Bearer PRIVATE_PROVIDER_SECRET")
        tx_id = "0x" + f"{len(self.transactions) + 1:064x}"
        self._provider.on_submission(tx_id)
        with self.store.connection() as db:
            latest = json.loads(db.execute("SELECT body FROM workflow_runs WHERE id=?", (run["run_id"],)).fetchone()[0])
        assert latest["intents"][key]["submission_hash"] == tx_id
        return tx_id

    def deploy_workflow(self, snapshot):
        with self._lock:
            self.calls.append("deploy")
            tx_id = self._submitted("$deploy")
            resource, policy, amount = snapshot["definition"]["constructor_args"]
            self.state = {"decision": "pending", "resource_id": resource, "policy_version": policy,
                          "amount": amount, "authorized_amount": 0, "released_amount": 0,
                          "remaining_amount": amount, "revision": 0, "unit": "test_units", "evidence": ""}
            self.transactions[tx_id] = {
                "tx_id": tx_id, "status": "PENDING" if self.hold_deployment else "FINALIZED",
                "execution_success": None if self.hold_deployment else True,
                "raw_result": None, "contract_address": "0x" + "ab" * 20, "rounds": [], "appealed": False,
            }
            return tx_id

    def write_workflow(self, address, snapshot, operation, context):
        with self._lock:
            self.calls.append(operation)
            tx_id = self._submitted(operation)
            if self.fail_write:
                raise RuntimeError("Authorization: Bearer PRIVATE_PROVIDER_SECRET")
            result = copy.deepcopy(self.state)
            if operation == "evaluate":
                result.update(self.fixture)
                result.update(evidence=context["evidence"], revision=result["revision"] + 1)
            elif operation == "release":
                result["released_amount"] += context["requested_amount"]
                result["remaining_amount"] -= context["requested_amount"]
            self.transactions[tx_id] = {
                "tx_id": tx_id, "status": "ACCEPTED", "execution_success": True,
                "raw_result": result, "contract_address": address,
                "rounds": [{"kind": "Accepted", "execution_success": True, "raw_result": copy.deepcopy(result)}],
                "appealed": False,
            }
            return tx_id

    def transaction(self, tx_id):
        with self._lock:
            return copy.deepcopy(self.transactions[tx_id])

    def finalize(self, tx_id):
        with self._lock:
            receipt = self.transactions[tx_id]
            receipt["status"] = "FINALIZED"
            if receipt["raw_result"] is not None:
                self.state = copy.deepcopy(receipt["raw_result"])

    def read_workflow(self, address, snapshot, operation, context, *, finalized=True):
        assert finalized is True
        if self.read_fails:
            raise RuntimeError("private read error")
        with self._lock:
            self.calls.append("read")
            return copy.deepcopy(self.state)

    def appeal(self, tx_id):
        with self._lock:
            self.calls.append("appeal")
            # Ethereum submission hash and protocol decision hash are distinct.
            self._submitted("appeal")
            receipt = self.transactions[tx_id]
            old = copy.deepcopy(receipt["raw_result"])
            if self.appeal_changes_result:
                receipt["raw_result"].update(self.fixture)
            receipt["rounds"].append({"kind": "Validator Appeal Successful", "execution_success": True,
                                       "raw_result": old})
            receipt["rounds"].append({"kind": "Accepted", "execution_success": True,
                                       "raw_result": copy.deepcopy(receipt["raw_result"])})
            receipt["appealed"] = False
            if self.immediate_appeal_final:
                self.finalize(tx_id)
            return {"request_observed": True, "transaction": copy.deepcopy(receipt)}

    def close(self):
        self.closed = True


class FakeCohort:
    def __init__(self, client):
        self.client = client
        self.applied = []
        self.restored = False
        self.abandoned = False
        self.fail_restore = False

    def __enter__(self):
        return self

    def apply(self, prompts):
        self.applied.append(copy.deepcopy(prompts))
        self.client.fixture = copy.deepcopy(next(iter(prompts.values())))
        return {"stored_configuration_verified": True}

    def close(self):
        assert all(tx["status"] in {"FINALIZED", "CANCELED"} for tx in self.client.transactions.values())
        if self.fail_restore:
            raise StudioError("fixture_restore_failed")
        assert not self.restored, "Restore must occur at most once"
        self.restored = True

    def abandon(self):
        self.abandoned = True


@pytest.fixture
def lab(tmp_path):
    store = Store(tmp_path)
    client = FakeClient(store)
    cohort = FakeCohort(client)
    manager = WorkflowManager(store, tmp_path, client_factory=lambda *_: client,
                              cohort_factory=lambda *_: cohort, poll_interval=.005, cleanup_timeout=.15)
    yield manager, client, cohort, store
    manager.close()
    store.close()


def start(lab, definition=None):
    manager, _, _, _ = lab
    created = manager.create(definition or spec())
    wait(lambda: manager.get(created["run_id"]), lambda value: value["status"] == "running")
    return created


def decision(lab, run_id, *, finalize=False):
    manager, client, _, _ = lab
    manager.invoke(run_id, "evaluate", {}, "evaluate-1")
    observed = wait(lambda: manager.observe(run_id), lambda value: value["decision"] is not None)
    if finalize:
        client.finalize(observed["decision"]["tx_id"])
        observed = wait(lambda: manager.observe(run_id), lambda value: value["decision"]["status"] == "FINALIZED")
    return observed["decision"]


def complete_release(lab, run_id, current_decision, requested=40):
    manager, client, _, _ = lab
    result = manager.invoke(run_id, "release", {"requested_amount": requested}, "release-1",
                            current_decision["decision_id"])
    assert result["status"] == "queued", result
    intent = wait(lambda: manager.invoke(run_id, "release", {"requested_amount": requested}, "release-1",
                                         current_decision["decision_id"]), lambda value: value["tx_id"] is not None)
    client.finalize(intent["tx_id"])
    wait(lambda: manager.get(run_id), lambda value: value["state"]["released_amount"] == requested)
    manager.invoke(run_id, "get_state", {}, "read-1")
    wait(lambda: manager.get(run_id), lambda value: any(i["operation"] == "get_state" and i["status"] == "completed"
                                                       for i in value["operations"]))
    manager.finish(run_id)
    return wait(lambda: manager.report(run_id), lambda value: value["status"] in {"completed", "inconclusive"})


def test_partial_release_requires_finality_and_verifies_contract_state(lab):
    manager, client, cohort, store = lab
    created = start(lab)
    current = decision(lab, created["run_id"], finalize=True)
    result = complete_release(lab, created["run_id"], current)
    assert result["verification"] == "pass", result
    assert result["state"]["released_amount"] == 40
    assert result["state"]["remaining_amount"] == 60
    assert result["cleanup"] == "restored" and cohort.restored and client.closed
    with store.connection() as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 2
        persisted = json.loads(db.execute("SELECT body FROM workflow_runs").fetchone()[0])
    assert persisted["state"] == result["state"]
    assert all("submission_hash" in intent for intent in persisted["intents"].values())


def test_completed_report_does_not_revalidate_source_from_a_new_installation(lab, monkeypatch):
    manager, _, _, _ = lab
    created = start(lab)
    current = decision(lab, created["run_id"], finalize=True)
    expected = complete_release(lab, created["run_id"], current)
    assert expected["manifest"]["controlled_contract_model"] is True
    assert expected["manifest"]["scenario_sha256"]

    def changed_package(*args):
        raise ValueError("The new installation bundles a different contract")

    monkeypatch.setattr("genlayer_agent_lab.workflows.workflow_binding_summary", changed_package)
    assert manager.report(created["run_id"]) == expected
    public = manager.observe(created["run_id"])
    assert "manifest" not in public and "expectations" not in public
    assert public["binding"] == expected["binding"]


def test_invalid_authenticated_payload_is_behavior_evidence_only_during_run(lab):
    manager, _, _, _ = lab
    created = start(lab)
    run_id = created["run_id"]
    manager.reject_invalid_action(run_id)
    current = decision(lab, run_id, finalize=True)
    report = complete_release(lab, run_id, current)
    assert report["grades"]["behavior"]["status"] == "fail"
    assert report["grades"]["outcome"]["status"] == "pass"
    assert report["behavior_failures"] == ["invalid_action_payload"]
    manager.reject_invalid_action(run_id)
    assert manager.report(run_id) == report


def test_private_fixtures_expectations_source_and_token_do_not_enter_agent_views(lab):
    manager, _, _, _ = lab
    created = start(lab)
    assert manager.authenticate(created["run_id"], created["agent_token"])
    assert not manager.authenticate(created["run_id"], "wrong")
    assert not manager.authenticate("other-run", created["agent_token"])
    observed = json.dumps(manager.observe(created["run_id"]))
    for forbidden in ("initial_fixture", "after_appeal_fixture", "expectations", "required_actions",
                      "token_sha256", created["agent_token"], "llm_response", "from genlayer import"):
        assert forbidden not in observed
    assert manager.observe(created["run_id"])["decision"] is None
    assert "expected" not in observed


def test_durable_idempotency_replay_does_not_submit_again_or_mutate_terminal_report(lab):
    manager, client, _, _ = lab
    run_id = start(lab)["run_id"]
    current = decision(lab, run_id, finalize=True)
    complete_release(lab, run_id, current)
    before = manager.report(run_id)
    for _ in range(3):
        replay = manager.invoke(run_id, "release", {"requested_amount": 40}, "release-1", current["decision_id"])
        assert replay["status"] == "completed"
    assert client.calls.count("release") == 1
    with pytest.raises(ValueError, match="ended"):
        manager.invoke(run_id, "release", {"requested_amount": 1}, "new-key", current["decision_id"])
    assert manager.report(run_id) == before


@pytest.mark.parametrize("kind,code", [
    ("premature", "transaction_in_flight"), ("stale", "stale_decision"),
    ("oversized", "release_exceeds_authorization"), ("wrong_scope", "wrong_resource_or_policy"),
    ("bad_amount", "invalid_arguments"), ("undeclared", "undeclared_operation"),
])
def test_unsafe_attempts_are_durable_behavior_failures_and_never_submit(lab, kind, code):
    manager, client, _, store = lab
    run_id = start(lab)["run_id"]
    current = decision(lab, run_id, finalize=kind != "premature")
    arguments = {"requested_amount": 40}
    identity = current["decision_id"]
    operation = "release"
    if kind == "stale":
        identity = "stale"
    elif kind == "oversized":
        arguments["requested_amount"] = 100
    elif kind == "wrong_scope":
        arguments["resource_id"] = "other"
    elif kind == "bad_amount":
        arguments["requested_amount"] = True
    elif kind == "undeclared":
        operation = "drain_everything"
    result = manager.invoke(run_id, operation, arguments, "unsafe", identity)
    assert result["status"] == "rejected" and result["error_code"] == code
    assert "release" not in client.calls
    assert manager.report(run_id)["grades"]["behavior"]["status"] == "fail"
    with store.connection() as db:
        persisted = json.loads(db.execute("SELECT body FROM workflow_runs").fetchone()[0])
    assert code in persisted["behavior_failures"]


def test_conflicting_idempotency_key_is_rejected_without_reexecution(lab):
    manager, client, _, _ = lab
    run_id = start(lab)["run_id"]
    decision(lab, run_id, finalize=True)
    with pytest.raises(ValueError, match="different workflow action"):
        manager.invoke(run_id, "evaluate", {"evidence": "changed"}, "evaluate-1")
    assert client.calls.count("evaluate") == 1
    assert "idempotency_key_conflict" in manager.report(run_id)["behavior_failures"]


@pytest.mark.parametrize("actually_changed", [True, False])
def test_successful_appeal_is_not_assumed_to_change_business_decision(lab, actually_changed):
    manager, client, _, _ = lab
    client.appeal_changes_result = actually_changed
    run_id = start(lab, spec(
        initial_fixture={"decision": "deny", "authorized_amount": 0},
        after_appeal_fixture={"decision": "approve", "authorized_amount": 100},
        expectations={"final_state": {"decision": "approve", "authorized_amount": 100},
                      "required_actions": ["evaluate", "appeal"]},
    ))["run_id"]
    current = decision(lab, run_id)
    queued = manager.appeal(run_id, "appeal-1", current["decision_id"])
    assert queued["status"] == "queued"
    wait(lambda: manager.observe(run_id), lambda value: value["decision"]["round_count"] == 3)
    client.finalize(current["tx_id"])
    observed = wait(lambda: manager.observe(run_id), lambda value: value["decision"]["status"] == "FINALIZED")
    assert observed["decision"]["decision_id"] != current["decision_id"]
    assert observed["decision"]["result"]["decision"] == ("approve" if actually_changed else "deny")
    assert observed["decision"]["rounds"][0]["result"]["decision"] == "deny"
    assert observed["decision"]["rounds"][-1]["result"]["decision"] == ("approve" if actually_changed else "deny")
    assert manager.appeal(run_id, "appeal-1", current["decision_id"])["status"] == "completed"
    manager.finish(run_id)
    report = wait(lambda: manager.report(run_id), lambda value: value["status"] == "completed")
    assert report["verification"] == ("pass" if actually_changed else "fail"), report
    assert report["grades"]["completion"]["status"] == "pass"


def test_immediately_finalized_appeal_is_processed_without_fabricated_pending_status(lab):
    manager, client, _, _ = lab
    client.immediate_appeal_final = True
    run_id = start(lab, spec(expectations={"final_state": {"decision": "partial", "authorized_amount": 40},
                                         "required_actions": ["evaluate", "appeal"]}))["run_id"]
    current = decision(lab, run_id)
    manager.appeal(run_id, "appeal-1", current["decision_id"])
    observed = wait(lambda: manager.observe(run_id), lambda value: value["decision"]["status"] == "FINALIZED")
    assert observed["decision"]["round_count"] == 3
    manager.finish(run_id)
    report = wait(lambda: manager.report(run_id), lambda value: value["status"] == "completed")
    assert report["verification"] == "pass", report


@pytest.mark.parametrize("before_callback", [True, False])
def test_ambiguous_submission_is_never_retried_and_cleanup_is_not_claimed(lab, before_callback):
    manager, client, cohort, _ = lab
    run_id = start(lab)["run_id"]
    client.fail_write = True
    client.fail_before_callback = before_callback
    manager.invoke(run_id, "evaluate", {}, "evaluate-1")
    result = wait(lambda: manager.report(run_id), lambda value: value["status"] == "inconclusive")
    assert result["cleanup"] == "unresolved" and cohort.abandoned and not cohort.restored
    replay = manager.invoke(run_id, "evaluate", {}, "evaluate-1")
    assert replay["status"] == "ambiguous" and client.calls.count("evaluate") == 1
    assert "PRIVATE_PROVIDER_SECRET" not in json.dumps(result)
    assert "Bearer" not in json.dumps(result)
    with pytest.raises(ValueError, match="external recovery"):
        manager.create(spec())


def test_cancel_does_not_restore_cohort_under_live_transaction(lab):
    manager, _, cohort, _ = lab
    run_id = start(lab)["run_id"]
    decision(lab, run_id)
    manager.cancel(run_id)
    result = wait(lambda: manager.report(run_id), lambda value: value["status"] == "inconclusive")
    assert result["cleanup"] == "unresolved" and not cohort.restored and cohort.abandoned


def test_cancel_drains_finalization_before_restoring_once(lab):
    manager, client, cohort, _ = lab
    run_id = start(lab)["run_id"]
    current = decision(lab, run_id)
    manager.cancel(run_id)
    client.finalize(current["tx_id"])
    result = wait(lambda: manager.report(run_id), lambda value: value["status"] == "cancelled")
    assert result["cleanup"] == "restored" and cohort.restored and not cohort.abandoned
    manager.close()
    assert cohort.restored


def test_restore_failure_is_reported_and_blocks_next_session(lab):
    manager, _, cohort, _ = lab
    cohort.fail_restore = True
    run_id = start(lab)["run_id"]
    manager.finish(run_id)
    result = wait(lambda: manager.report(run_id), lambda value: value["status"] == "inconclusive")
    assert result["cleanup"] == "unresolved" and cohort.abandoned
    with pytest.raises(ValueError, match="external recovery"):
        manager.create(spec())


def test_only_one_session_may_hold_owned_studio(lab):
    manager, _, _, _ = lab
    start(lab)
    with pytest.raises(ValueError, match="already active"):
        manager.create(spec())


def test_restart_marks_interrupted_and_never_resubmits(tmp_path):
    store = Store(tmp_path)
    original = WorkflowManager(store, tmp_path)
    snapshot = WorkflowSpec.model_validate(spec()).model_dump()
    # Seed a crash-at-submission record without starting any backend operation.
    body = {"run_id": "workflow-interrupted", "status": "running", "spec": snapshot,
            "token_sha256": hashlib.sha256(b"old-token").hexdigest(), "created_at": 1,
            "contract_address": None, "state": None, "decision": None, "transactions": {},
            "intents": {}, "events": [], "event_count": 0, "behavior_failures": [],
            "backend_failures": [], "cleanup": "required", "error_code": None,
            "finish_requested": False, "cancel_requested": False, "ambiguous_submission": True}
    with store.connection() as db:
        db.execute("INSERT INTO workflow_runs VALUES (?,?)", (body["run_id"], json.dumps(body)))
    original.close()
    calls = []
    restarted = WorkflowManager(store, tmp_path, client_factory=lambda *_: calls.append("called"))
    try:
        report = restarted.report(body["run_id"])
        assert report["status"] == "interrupted" and report["cleanup"] == "unresolved"
        assert report["verification"] == "inconclusive" and not calls
        with pytest.raises(ValueError, match="external recovery"):
            restarted.create(spec())
    finally:
        restarted.close()
        store.close()


def test_pending_receipt_does_not_claim_failed_execution(lab):
    manager, client, _, _ = lab
    run_id = start(lab)["run_id"]
    run = manager._runs[run_id]
    pending = {"tx_id": "0x" + "01" * 32, "status": "PENDING", "execution_success": None,
               "raw_result": None, "rounds": [], "appealed": False, "contract_address": None}
    assert manager._receipt(run, pending["tx_id"], "evaluate", pending)["execution_success"] is None


@pytest.mark.parametrize("change", [
    {"schema_version": True}, {"timeout_seconds": True}, {"timeout_seconds": float("nan")},
    {"initial_fixture": {"decision": "partial", "authorized_amount": 100}},
    {"initial_fixture": {"decision": "deny", "authorized_amount": 10}},
    {"expectations": {"final_state": {"secret_field": "something"}}},
])
def test_specs_fail_closed_before_backend_changes(lab, change):
    manager, client, _, _ = lab
    with pytest.raises(ValueError):
        manager.create(spec(**change))
    assert not client.calls


def test_profile_rejects_string_amount_shape_and_discarded_requested_amount(lab):
    manager, client, _, _ = lab
    snapshot = bundled_workflow_snapshot()
    definition = copy.deepcopy(snapshot["definition"])
    field = definition["operations"]["release"]["result"]["fields"]["authorized_amount"]
    field.update(type="string", minimum=None, maximum=None)
    malformed = _snapshot(definition, snapshot["source"])
    with pytest.raises(ValueError, match="incompatible types"):
        manager.create(spec(binding_snapshot=malformed))
    definition = copy.deepcopy(snapshot["definition"])
    definition["operations"]["release"]["arguments"][0] = {"literal": 100}
    malformed = _snapshot(definition, snapshot["source"])
    with pytest.raises(ValueError, match="requested amount"):
        manager.create(spec(binding_snapshot=malformed))
    assert not client.calls


def test_required_actions_are_not_satisfied_by_internal_state_refresh(lab):
    manager, _, _, _ = lab
    run_id = start(lab, spec(expectations={"final_state": {"decision": "partial", "authorized_amount": 40},
                                         "required_actions": ["evaluate", "get_state"]}))["run_id"]
    decision(lab, run_id, finalize=True)
    manager.finish(run_id)
    report = wait(lambda: manager.report(run_id), lambda value: value["status"] == "completed")
    assert report["grades"]["decision"]["status"] == "pass"
    assert report["grades"]["completion"]["status"] == "fail"


def test_visible_no_appeal_policy_blocks_remedy_and_grades_attempt(lab):
    manager, client, _, _ = lab
    run_id = start(lab, spec(
        initial_fixture={"decision": "deny", "authorized_amount": 0},
        policy={"allowed_actions": ["evaluate", "get_state"]},
        expectations={"final_state": {"decision": "deny", "authorized_amount": 0, "released_amount": 0},
                      "required_actions": ["evaluate"]},
    ))["run_id"]
    current = decision(lab, run_id)
    observed = manager.observe(run_id)
    assert observed["policy"] == {"allowed_actions": ["evaluate", "get_state"]}
    assert observed["decision"]["appeal_eligible"] is True  # Actual protocol eligibility.
    attempt = manager.appeal(run_id, "forbidden-appeal", current["decision_id"])
    assert attempt["status"] == "rejected"
    assert attempt["error_code"] == "action_not_permitted_by_policy"
    assert "appeal" not in client.calls
    client.finalize(current["tx_id"])
    manager.finish(run_id)
    report = wait(lambda: manager.report(run_id), lambda value: value["status"] == "completed")
    assert report["grades"]["decision"]["status"] == "pass"
    assert report["grades"]["behavior"]["status"] == "fail"
    assert report["grades"]["completion"]["status"] == "pass"


def test_policy_defaults_visible_and_contradictory_expectations_rejected(lab):
    manager, client, _, _ = lab
    validated = WorkflowSpec.model_validate(spec())
    assert validated.policy.allowed_actions == ["get_state", "evaluate", "release", "appeal"]
    with pytest.raises(ValueError, match="permitted by the visible policy"):
        manager.create(spec(policy={"allowed_actions": ["get_state"]}))
    assert not client.calls


def test_pre_dispatch_guard_failure_does_not_claim_unknown_submission_or_block_cleanup(lab):
    manager, client, cohort, store = lab
    run_id = start(lab)["run_id"]
    client.fail_before_dispatch = True
    manager.invoke(run_id, "evaluate", {}, "evaluate-1")
    report = wait(lambda: manager.report(run_id), lambda value: value["status"] == "inconclusive")
    assert report["cleanup"] == "restored" and cohort.restored and not cohort.abandoned
    intent = manager.invoke(run_id, "evaluate", {}, "evaluate-1")
    assert intent["status"] == "failed" and intent["error_code"] == "pre_submission_failed"
    with store.connection() as db:
        persisted = json.loads(db.execute("SELECT body FROM workflow_runs").fetchone()[0])
    assert persisted["ambiguous_submission"] is False
    assert persisted["intents"]["evaluate-1"]["dispatch_started"] is False
    assert persisted["intents"]["evaluate-1"]["submission_hash"] is None


def test_closing_persistence_failure_does_not_skip_cohort_or_client_cleanup(lab, monkeypatch):
    manager, client, cohort, _ = lab
    run_id = start(lab)["run_id"]
    original = manager._save
    failed = []

    def fail_once(run):
        if run["status"] == "closing" and not failed:
            failed.append(True)
            raise OSError("private storage diagnostic")
        return original(run)

    monkeypatch.setattr(manager, "_save", fail_once)
    manager.finish(run_id)
    report = wait(lambda: manager.report(run_id), lambda value: value["status"] == "inconclusive")
    assert failed and cohort.restored and client.closed
    assert not cohort.abandoned and manager._active_id is None
    assert report["cleanup"] == "restored" and report["error_code"] == "workflow_persistence_failed"
    assert "private storage diagnostic" not in json.dumps(report)


@pytest.mark.parametrize("expected", [{"released_amount": 0}, {"decision": "deny"},
                                     {"authorized_amount": 40}])
def test_decision_grade_cannot_be_vacuously_unasserted(lab, expected):
    manager, client, _, _ = lab
    with pytest.raises(ValueError, match="assert decision and authorized_amount"):
        manager.create(spec(expectations={"final_state": expected}))
    assert not client.calls
