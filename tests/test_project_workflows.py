"""Driver integration tests with persistent fake chain state and real SQLite.

These verify Lab orchestration/recovery, not GenVM execution or fee economics.
Actual modern Studio execution has a separate verification gate.
"""

import copy
import hashlib
import json
import threading
import time
from pathlib import Path

import pytest

from genlayer_agent_lab.api import initialize_data_dir
from genlayer_agent_lab.project_bindings import load_project_binding
from genlayer_agent_lab.project_journal import ProjectVault
from genlayer_agent_lab.project_scenarios import (
    approve_project_scenario,
    prediction_scenario_template,
    scenario_digest,
)
from genlayer_agent_lab.project_workflows import ProjectWorkflowManager
from genlayer_agent_lab.runtime.studio import StudioError
from genlayer_agent_lab.store import Store

EXAMPLE = Path(__file__).parents[1] / "examples/projects/prediction/project.yaml"
ORACLE, RECORDER = "0x" + "12" * 20, "0x" + "34" * 20


def wait(function, predicate=bool, timeout=5):
    deadline = time.monotonic() + timeout
    value = None
    while time.monotonic() < deadline:
        value = function()
        if predicate(value):
            return value
        time.sleep(.01)
    raise AssertionError(value)


def scenario(mode="finalize", change=None):
    draft = prediction_scenario_template(load_project_binding(EXAMPLE), mode=mode)
    if change:
        change(draft)
    return approve_project_scenario(draft, reviewer="test developer", expected_sha256=scenario_digest(draft))


class FakeChain:
    """Protocol outcomes are controlled independently of fixture application."""

    def __init__(self, store, data_dir):
        self.store, self.data_dir = store, data_dir
        self.lock = threading.RLock()
        self.tx = {}
        self.envelopes = {}
        self.prepared = []
        self.submissions = []
        self.keys = []
        self.codes = {}
        self.fixture = {}
        self.cohort_closed = 0
        self.cohort_abandoned = 0
        self.cohort_recovered = 0
        self.hold_resolve = False
        self.fail_after_submit = None
        self.fail_before_submit = None
        self.deploy_error = False
        self.child_failure = False
        self.provenance = {"owner": "fixture-chain", "source_commit": "a" * 40,
                           "chain_id": 61127, "endpoint": "http://127.0.0.1:19999"}
        self.oracle = {"market_id": "market-001", "outcome": "unresolved", "confidence_bps": 0,
                       "revision": 0, "evidence_hash": ""}
        self.final_oracle = copy.deepcopy(self.oracle)
        self.record = {"market_id": "market-001", "recorded": False, "outcome": "unresolved",
                       "oracle_revision": 0, "record_count": 0}

    def factory(self, data_dir, private_key):
        self.keys.append(private_key)
        return FakeClient(self)

    def latest_persisted(self):
        with self.store.connection() as db:
            return json.loads(db.execute("SELECT body FROM project_runs ORDER BY rowid DESC LIMIT 1").fetchone()[0])

    def receipt(self, tx_id, result=None, status="FINALIZED", **extras):
        return {"tx_id": tx_id, "status": status, "execution_success": True,
                "raw_result": copy.deepcopy(result), "rounds": [], "fees": {"test_only": True}, **extras}

    def prepare(self, method, **data):
        with self.lock:
            tx_id = "0x" + f"{len(self.prepared) + 1:064x}"
            prepared = {"tx_id": tx_id, "method": method, "fee_value": 7,
                        "signed_transaction": "PRIVATE_SIGNED_BYTES:" + tx_id, **data}
            self.prepared.append(copy.deepcopy(prepared))
            return prepared

    def finalize_resolution(self):
        with self.lock:
            for receipt in self.tx.values():
                if receipt.get("method") == "resolve":
                    receipt["status"] = "FINALIZED"
            self.final_oracle = copy.deepcopy(self.oracle)

    def finish_appeal(self, *, observed_round=True, outcome="yes"):
        with self.lock:
            target = next(r for r in self.tx.values() if r.get("method") == "resolve")
            if observed_round:
                target["rounds"].append({"kind": "ValidatorAppealSuccessful", "execution_success": True})
                self.oracle.update(outcome=outcome, confidence_bps=9100)
                target["raw_result"] = copy.deepcopy(self.oracle)
            target["status"] = "FINALIZED"
            self.final_oracle = copy.deepcopy(self.oracle)


class FakeClient:
    def __init__(self, chain):
        self.chain = chain
        self.workflow_provenance = copy.deepcopy(chain.provenance)

    def ensure_test_balance(self, amount):
        return {"local": True, "balance": amount}

    def prepare_deploy(self, code, args):
        alias = "oracle" if code[:2] == b"PK" else "recorder"
        if alias == "recorder":
            assert args == [ORACLE, "market-001"]
        self.chain.codes[ORACLE if alias == "oracle" else RECORDER] = code
        return self.chain.prepare("deploy", alias=alias, args=args)

    def prepare_write(self, address, method, args):
        return self.chain.prepare(method, address=address, args=args)

    def prepare_appeal(self, target_tx_id, quote):
        return self.chain.prepare("appeal", target_tx_id=target_tx_id, quoted=quote)

    def submit(self, prepared):
        chain = self.chain
        with chain.lock:
            # Real SQLite and encryption: provider access must follow the commit.
            run = chain.latest_persisted()
            intent = next(i for i in run["intents"].values() if i.get("tx_id") == prepared["tx_id"])
            assert intent["status"] == "submitting"
            assert ProjectVault(chain.data_dir).open(intent["prepared"]) == prepared
            chain.submissions.append(copy.deepcopy(prepared))
            method, tx_id = prepared["method"], prepared["tx_id"]
            if chain.fail_before_submit == method:
                chain.fail_before_submit = None
                raise RuntimeError("PRIVATE_PROVIDER_SECRET")
            if tx_id in chain.tx or tx_id in chain.envelopes:
                return tx_id
            if method == "deploy":
                address = ORACLE if prepared["alias"] == "oracle" else RECORDER
                chain.tx[tx_id] = chain.receipt(tx_id, contract_address=address,
                                               execution_success=not chain.deploy_error)
            elif method == "resolve":
                response = next(iter(chain.fixture.values()))
                chain.oracle.update(**response, revision=chain.oracle["revision"] + 1,
                                    evidence_hash=hashlib.sha256(prepared["args"][0].encode()).hexdigest())
                chain.tx[tx_id] = chain.receipt(tx_id, chain.oracle,
                    status="ACCEPTED" if chain.hold_resolve else "FINALIZED", method=method,
                    rounds=[{"kind": "Consensus", "execution_success": True}])
                if not chain.hold_resolve:
                    chain.final_oracle = copy.deepcopy(chain.oracle)
                if chain.child_failure:
                    child_id = "0x" + "f" * 64
                    chain.tx[tx_id]["child_transactions"] = [child_id]
                    chain.tx[child_id] = chain.receipt(child_id, execution_success=False)
            elif method == "record":
                revision, outcome = prepared["args"]
                succeeds = not chain.record["recorded"] and revision == chain.oracle["revision"] and outcome == chain.oracle["outcome"]
                if succeeds:
                    chain.record.update(recorded=True, outcome=outcome, oracle_revision=revision, record_count=1)
                chain.tx[tx_id] = chain.receipt(tx_id, chain.record if succeeds else None,
                                               execution_success=succeeds)
            elif method == "appeal":
                # Submission is not an appeal result; only finish_appeal adds one.
                chain.envelopes[tx_id] = {"success": True}
                chain.tx[prepared["target_tx_id"]]["appealed"] = True
            else:
                raise AssertionError(method)
            if chain.fail_after_submit == method:
                chain.fail_after_submit = None
                raise RuntimeError("PRIVATE_PROVIDER_SECRET")
            return tx_id

    def transaction(self, tx_id):
        with self.chain.lock:
            if tx_id not in self.chain.tx:
                raise StudioError("transaction_not_found")
            return copy.deepcopy(self.chain.tx[tx_id])

    def envelope(self, tx_id):
        return copy.deepcopy(self.chain.envelopes.get(tx_id))

    def read(self, address, method, args, finalized=True):
        with self.chain.lock:
            return copy.deepcopy(self.chain.final_oracle if address == ORACLE else self.chain.record)

    def verify_contract(self, address, code):
        assert self.chain.codes[address] == code

    def appeal_quote(self, tx_id):
        return {"target_tx_id": tx_id, "bond": 4, "fee_value": 7, "test_only": True}

    def estimate_write(self, address, method, args):
        return {"fee_value": 7, "test_only": True}

    def balance(self):
        return 10**24 - 7 * len(self.chain.prepared)

    def close(self):
        pass


class FakeCohort:
    def __init__(self, data_dir, client, state, save):
        self.chain, self.state, self.save = client.chain, state, save

    def __enter__(self):
        if self.state:
            self.chain.cohort_recovered += 1
        else:
            self.state = {"original_fixture": copy.deepcopy(self.chain.fixture), "id": "cohort-test"}
            self.save(self.state)
        return self

    def apply(self, fixtures):
        self.chain.fixture = copy.deepcopy(fixtures)

    def close(self):
        self.chain.fixture = copy.deepcopy(self.state["original_fixture"])
        self.chain.cohort_closed += 1

    def abandon(self):
        self.chain.cohort_abandoned += 1


@pytest.fixture
def harness(tmp_path):
    data_dir = initialize_data_dir(tmp_path / "lab")
    store = Store(data_dir)
    chain = FakeChain(store, data_dir)
    managers = []

    def manager():
        result = ProjectWorkflowManager(store, data_dir, client_factory=chain.factory,
            cohort_factory=FakeCohort, poll_interval=.01, cleanup_timeout=.15)
        managers.append(result)
        return result

    yield chain, manager
    for instance in managers:
        instance.close()
    store.close()


def start(manager, spec=None):
    created = manager.create(spec or scenario())
    run_id = created["run_id"]
    wait(lambda: manager.observe(run_id), lambda r: r["status"] in {"running", "inconclusive"})
    assert manager.observe(run_id)["status"] == "running", manager.report(run_id)
    return created


def operation(manager, run_id, name, args=None, key=None, expected=None):
    manager.invoke(run_id, name, args or {}, key or name, expected)
    return wait(lambda: manager.observe(run_id)["operations"],
                lambda ops: any(i["idempotency_key"] == (key or name) and i["status"] in
                               {"completed", "rejected", "failed"} for i in ops))[-1]


def finish(manager, run_id):
    manager.finish(run_id)
    wait(lambda: manager.get(run_id), lambda r: r["status"] in {"completed", "inconclusive"})
    return manager.report(run_id)


def complete_record(manager, run_id):
    state = wait(lambda: manager.observe(run_id)["state"].get("oracle_state"),
                 lambda s: s and s["revision"] > 0)
    return operation(manager, run_id, "record", {"expected_revision": state["revision"], "outcome": state["outcome"]})


def test_safe_agent_completes_two_contract_workflow_without_leaking_private_inputs(harness):
    chain, factory = harness
    manager = factory()
    created = start(manager)
    run_id = created["run_id"]
    assert manager.authenticate(run_id, created["agent_token"])
    assert not manager.authenticate(run_id, "wrong")
    before = json.dumps(manager.observe(run_id))
    assert "fixtures" not in before and "expectations" not in before
    assert "AGENT_LAB_PREDICTION_V2" not in before
    operation(manager, run_id, "resolve", {"evidence": "Controlled record reports yes"})
    complete_record(manager, run_id)
    report = finish(manager, run_id)
    assert report["verification"] == "pass", report["checks"]
    assert report["state"]["record_state"]["record_count"] == 1
    assert chain.cohort_closed == 1 and chain.fixture == {}
    public = json.dumps(report)
    for secret in [*chain.keys, "PRIVATE_SIGNED_BYTES", "PRIVATE_PROVIDER_SECRET", created["agent_token"]]:
        assert secret not in public
    assert "AGENT_LAB_PREDICTION_V2" not in public
    assert all(i["prepared"].startswith("gAAAA") for i in chain.latest_persisted()["intents"].values())


def test_unsafe_agent_is_failed_when_early_record_is_blocked(harness):
    chain, factory = harness
    chain.hold_resolve = True
    manager = factory()
    run_id = start(manager)["run_id"]
    manager.invoke(run_id, "resolve", {"evidence": "yes"}, "resolve")
    wait(lambda: manager.observe(run_id)["operations"], lambda ops: ops and ops[0]["transaction_status"] == "ACCEPTED")
    result = operation(manager, run_id, "record", {"expected_revision": 1, "outcome": "yes"})
    assert result["status"] == "rejected"
    assert not any(p["method"] == "record" for p in chain.prepared)
    chain.finalize_resolution()
    report = finish(manager, run_id)
    assert report["verification"] == "fail"
    assert report["state"]["record_state"]["record_count"] == 0


@pytest.mark.parametrize("failure", ["after", "before"])
def test_lost_rpc_reply_reconciles_same_signed_transaction(harness, failure):
    chain, factory = harness
    setattr(chain, "fail_" + failure + "_submit", "resolve")
    manager = factory()
    run_id = start(manager)["run_id"]
    operation(manager, run_id, "resolve", {"evidence": "yes"})
    complete_record(manager, run_id)
    report = finish(manager, run_id)
    assert report["verification"] == "pass", report["checks"]
    preparations = [p for p in chain.prepared if p["method"] == "resolve"]
    submissions = [p for p in chain.submissions if p["method"] == "resolve"]
    assert len(preparations) == 1
    assert len(submissions) == (1 if failure == "after" else 2)
    assert all(p == preparations[0] for p in submissions)
    assert report["state"]["oracle_state"]["revision"] == 1


def test_restart_recovers_signer_cohort_and_unfinished_transaction_without_redeploy(harness):
    chain, factory = harness
    chain.hold_resolve = True
    first = factory()
    created = start(first)
    run_id = created["run_id"]
    first.invoke(run_id, "resolve", {"evidence": "yes"}, "resolve")
    wait(lambda: first.observe(run_id)["operations"], lambda ops: ops and ops[0]["transaction_status"] == "ACCEPTED")
    first.close()
    assert first.observe(run_id)["status"] == "paused"
    assert chain.cohort_abandoned == 1 and chain.cohort_closed == 0
    second = factory()
    wait(lambda: second.observe(run_id), lambda r: r["status"] == "running")
    assert second.authenticate(run_id, created["agent_token"])
    assert len(set(chain.keys)) == 1 and chain.cohort_recovered == 1
    chain.finalize_resolution()
    wait(lambda: second.observe(run_id)["operations"][0], lambda i: i["status"] == "completed")
    complete_record(second, run_id)
    report = finish(second, run_id)
    assert report["verification"] == "pass", report["checks"]
    assert [p["method"] for p in chain.prepared] == ["deploy", "deploy", "resolve", "record"]


def test_stale_decision_and_idempotency_conflict_never_dispatch(harness):
    chain, factory = harness
    manager = factory()
    run_id = start(manager)["run_id"]
    operation(manager, run_id, "resolve", {"evidence": "yes"})
    result = operation(manager, run_id, "record", {"expected_revision": 1, "outcome": "yes"}, expected="0" * 64)
    assert result["error_code"] == "stale_or_unknown_decision"
    with pytest.raises(ValueError, match="different arguments"):
        manager.invoke(run_id, "resolve", {"evidence": "changed"}, "resolve")
    assert len(chain.prepared) == 3
    assert finish(manager, run_id)["verification"] == "fail"


@pytest.mark.parametrize("completed_round", [True, False])
def test_appeal_envelope_is_separate_from_observed_round_outcome(harness, completed_round):
    chain, factory = harness
    chain.hold_resolve = True
    manager = factory()
    run_id = start(manager, scenario("appeal_changed"))["run_id"]
    manager.invoke(run_id, "resolve", {"evidence": "record says yes"}, "resolve")
    decision = wait(lambda: manager.observe(run_id)["operations"],
                    lambda ops: ops and ops[0]["transaction_status"] == "ACCEPTED")[0]
    manager.appeal(run_id, "appeal", decision["decision_id"])
    wait(lambda: manager.observe(run_id)["operations"], lambda ops: len(ops) == 2 and ops[1]["tx_id"])
    wait(lambda: manager.observe(run_id)["operations"][1], lambda op: op.get("transaction_status") == "FINALIZED")
    assert manager.observe(run_id)["operations"][1]["status"] == "submitted"
    assert manager.observe(run_id)["operations"][1]["execution_success"] is None
    chain.finish_appeal(observed_round=completed_round)
    result = wait(lambda: manager.observe(run_id)["operations"][1], lambda op: op["status"] in {"completed", "failed"})
    assert result["status"] == ("completed" if completed_round else "failed")
    if completed_round:
        assert result["result"]["target"]["result"]["outcome"] == "yes"
        complete_record(manager, run_id)
    report = finish(manager, run_id)
    assert report["verification"] == ("pass" if completed_round else "fail"), report["checks"]


def test_finalized_deployment_error_is_inconclusive_not_a_running_project(harness):
    chain, factory = harness
    chain.deploy_error = True
    manager = factory()
    run_id = manager.create(scenario())["run_id"]
    wait(lambda: manager.get(run_id), lambda r: r["status"] == "inconclusive")
    report = manager.report(run_id)
    assert report["verification"] == "inconclusive"
    assert report["contracts"] == {}
    assert len(chain.prepared) == 1


def test_real_quote_exceeding_agent_limit_is_rejected_before_submission(harness):
    chain, factory = harness
    spec = scenario(change=lambda draft: draft["policy"].update(max_fee=6))
    manager = factory()
    run_id = start(manager, spec)["run_id"]
    result = operation(manager, run_id, "resolve", {"evidence": "yes"})
    assert result["error_code"] == "fee_policy_violated"
    assert [p["method"] for p in chain.submissions] == ["deploy", "deploy"]
    assert finish(manager, run_id)["verification"] == "fail"


def test_failed_child_effect_is_preserved_without_inventing_parent_rollback(harness):
    chain, factory = harness
    chain.child_failure = True
    manager = factory()
    run_id = start(manager)["run_id"]
    operation(manager, run_id, "resolve", {"evidence": "yes"})
    complete_record(manager, run_id)
    report = finish(manager, run_id)
    # This two-contract rubric has no dependent child business effect. Preserve
    # its failed execution distinctly; do not invent rollback of the parent.
    # The messages example separately requires the actual audit state effect.
    assert report["verification"] == "pass"
    child = next(t for t in report["transactions"].values() if t.get("parent_tx_id"))
    assert child["execution_success"] is False
    assert report["state"]["record_state"]["recorded"] is True
    assert next(c for c in report["checks"] if c["id"] == "child_effects")["outcome"] == "pass"


def test_cleanup_never_restores_fixtures_while_submission_identity_is_unobserved(harness, monkeypatch):
    chain, factory = harness
    manager = factory()
    run_id = start(manager)["run_id"]
    submit = FakeClient.submit
    def uncertain(client, prepared):
        if prepared["method"] == "resolve":
            manager.finish(run_id)
            raise StudioError("rpc_transport_error")
        return submit(client, prepared)
    monkeypatch.setattr(FakeClient, "submit", uncertain)
    manager.invoke(run_id, "resolve", {"evidence": "yes"}, "uncertain-resolution")
    wait(lambda: manager.get(run_id), lambda r: r["status"] == "inconclusive")
    report = manager.report(run_id)
    assert report["cleanup"] == "unresolved"
    assert chain.cohort_closed == 0 and chain.cohort_abandoned == 1
    assert report["verification"] == "inconclusive"
    assert len([p for p in chain.prepared if p["method"] == "resolve"]) == 1


@pytest.mark.parametrize("completed", [False, True])
def test_portable_project_history_retires_secrets_and_never_resumes(harness, tmp_path, completed):
    from genlayer_agent_lab.recovery import backup, restore
    chain, factory = harness
    manager = factory()
    created = start(manager)
    run_id = created["run_id"]
    if completed:
        operation(manager, run_id, "resolve", {"evidence": "yes"})
        complete_record(manager, run_id)
        original = finish(manager, run_id)
    manager.close()
    archive = tmp_path / "history.zip"
    manager.store.close()
    backup(manager.data_dir, archive)
    # The original installation still has its encrypted signer for its own recovery.
    assert "private_account" in chain.latest_persisted()
    restored_path = tmp_path / "restored-history"
    restore(archive, restored_path)
    store = Store(restored_path)
    restored = ProjectWorkflowManager(store, restored_path)
    try:
        assert not restored.authenticate(run_id, created["agent_token"])
        assert restored._thread is None
        saved = restored._runs[run_id]
        assert "private_account" not in saved
        assert all("prepared" not in intent for intent in saved["intents"].values())
        if completed:
            assert restored.report(run_id) == original
        else:
            report = restored.report(run_id)
            assert report["verification"] == "inconclusive"
            assert report["error_code"] == "interrupted_by_restore"
            assert report["cleanup"] == "unresolved"
            with pytest.raises(ValueError, match="unfinished"):
                restored.create(scenario())
    finally:
        restored.close()
        store.close()
