"""Synthetic HTTP reports test the verifier; none are live Studio evidence."""

import copy

import pytest

from genlayer_agent_lab import __version__
from genlayer_agent_lab import workflow_verification as verifier
from genlayer_agent_lab.bindings import _hash
from genlayer_agent_lab.workflow_reference import KEYS

ADMIN_TOKEN = "private-admin-token"


def report_for(name, spec, run_id):
    """Construct independent observed-state/round/trace fixtures, not a backend."""
    decision_tx, release_tx = "0x" + "11" * 32, "0x" + "22" * 32
    expected = spec["expectations"]["final_state"]
    state = {**spec["context"], **expected, "unit": "test_units", "revision": 1}
    decision_result = {**state, "released_amount": 0, "remaining_amount": 100}
    initial = {**decision_result, **spec["initial_fixture"]}
    rounds = [{"index": 0, "kind": "Accepted", "execution_success": True, "result": initial}]
    if name == "overturn":
        rounds.extend([{"index": 1, "kind": "Validator Appeal Successful"},
                       {"index": 2, "kind": "Accepted", "execution_success": True, "result": decision_result}])
    elif name == "upheld":
        rounds.append({"index": 1, "kind": "Validator Appeal Failed"})
    operations = [{"run_id": run_id, "operation": "evaluate", "idempotency_key": KEYS["evaluate"],
                   "tx_id": decision_tx, "status": "completed", "result": decision_result, "error_code": None}]
    if name in {"overturn", "upheld"}:
        operations.append({"run_id": run_id, "operation": "appeal", "idempotency_key": KEYS["appeal"],
                           "tx_id": decision_tx, "status": "completed", "result": decision_result, "error_code": None})
    if name == "unsafe":
        operations.append({"run_id": run_id, "operation": "release", "idempotency_key": KEYS["unsafe-release"],
                           "tx_id": None, "status": "rejected", "result": None, "error_code": "decision_not_final"})
    if name != "deny":
        operations.append({"run_id": run_id, "operation": "release", "idempotency_key": KEYS["release"],
                           "tx_id": release_tx, "status": "completed", "result": state, "error_code": None})
    events = [{"index": 0, "kind": "transaction_observed", "tx_id": decision_tx,
               "status": "FINALIZED", "result_sha256": _hash(decision_result)}]
    if name != "deny":
        events.extend([{"index": 1, "kind": "submission_intent", "operation": "release", "key": KEYS["release"]},
                       {"index": 2, "kind": "transaction_observed", "tx_id": release_tx,
                        "status": "FINALIZED", "result_sha256": _hash(state)}])
    return {"run_id": run_id, "status": "completed", "verification": "fail" if name == "unsafe" else "pass",
            "cleanup": "restored", "error_code": None, "state": copy.deepcopy(state),
            "decision": {"tx_id": decision_tx, "status": "FINALIZED", "execution_success": True,
                         "result": copy.deepcopy(decision_result), "round_count": len(rounds), "rounds": copy.deepcopy(rounds)},
            "grades": {key: {"status": "fail" if key == "behavior" and name == "unsafe" else "pass"}
                       for key in ("decision", "behavior", "outcome", "completion")},
            "behavior_failures": ["decision_not_final"] if name == "unsafe" else [],
            "operations": copy.deepcopy(operations), "events": events,
            "binding": {key: spec["binding_snapshot"][key] for key in ("binding_sha256", "source_sha256")},
            "capabilities": {"backend": "studio", "workflow_bridge": True, "appeals_supported": True,
                             "bond_accounting": False, "public_chain": False, "test_units_only": True},
            "manifest": {"toolkit_version": __version__, "controlled_contract_model": True,
                         "contract_model_quality_evaluated": False, "scenario_sha256": _hash(spec),
                         "scenario": copy.deepcopy(spec), "backend": {
                             "origin": "owned_local_studio", "source_commit": verifier.STUDIO_COMMIT,
                             "studio_version": verifier.STUDIO_VERSION.removeprefix("v"),
                             "genvm_version": verifier.GENVM_VERSION, "sdk_version": verifier.SDK_VERSION,
                             "fixture_patch": verifier.FIXTURE_CONFIG_PATCH, "runtime_verified": True,
                             "network_internal": True, "fixture_only": True,
                             "image_id": "sha256:" + "a" * 64, "configuration_sha256": "b" * 64}}}


class FakeHTTP:
    def __init__(self):
        self.names = list(verifier.CASES)
        self.calls = []
        self.reports = {}
        self.modify = lambda name, report: None
        self.fail_create = False
        self.fail_driver = False
        self.create_count = 0

    def client(self, url, token, *, timeout):
        fixture = self
        assert url == "http://127.0.0.1:8765"
        assert 0 < timeout <= 5

        class Client:
            def __enter__(self):
                return self

            def __exit__(self, *_):
                pass

            def workflow_create(self, spec):
                assert token == ADMIN_TOKEN
                fixture.calls.append(("create", token))
                if fixture.fail_create:
                    raise RuntimeError("Bearer " + ADMIN_TOKEN)
                name = fixture.names[fixture.create_count]
                fixture.create_count += 1
                run_id = "workflow-" + f"{fixture.create_count:032x}"
                report = report_for(name, spec, run_id)
                fixture.modify(name, report)
                fixture.reports[run_id] = report
                return {"run_id": run_id, "agent_token": "agent-token-" + run_id}

            def workflow_report(self, run_id):
                assert token == ADMIN_TOKEN
                fixture.calls.append(("report", run_id))
                return copy.deepcopy(fixture.reports[run_id])

            def workflow_cancel(self, run_id):
                assert token == ADMIN_TOKEN
                fixture.calls.append(("cancel", run_id))
                return {"run_id": run_id, "status": "cancelled"}

        client = Client()
        client.token = token
        return client

    def agent(self, client, run_id, *, mode, timeout_seconds, cleanup_timeout, poll_interval):
        assert client.token == "agent-token-" + run_id and client.token != ADMIN_TOKEN
        assert timeout_seconds > 0 and cleanup_timeout > 0 and poll_interval > 0
        self.calls.append(("agent", run_id, mode))
        if self.fail_driver:
            raise RuntimeError(client.token + " Authorization: Bearer " + ADMIN_TOKEN)
        report = self.reports[run_id]
        return {"run_id": run_id, "status": "completed", "outcome": "unsafe_completed" if mode == "unsafe" else
                "no_release" if report["state"]["authorized_amount"] == 0 else "released"}


@pytest.fixture
def server(monkeypatch):
    fake = FakeHTTP()
    monkeypatch.setattr(verifier, "LabClient", fake.client)
    monkeypatch.setattr(verifier, "run_workflow_agent", fake.agent)
    return fake


def verify(server, names=None, **kwargs):
    if names is not None:
        server.names = names
    return verifier.verify_workflows("http://127.0.0.1:8765", ADMIN_TOKEN, cases=names, **kwargs)


def test_six_expected_evidence_shapes_and_roles_are_checked_sequentially(server):
    result = verify(server)
    assert result["verification"] == "pass", result["cases"]
    assert [item["case"] for item in result["cases"]] == list(verifier.CASES)
    assert list(result["reports"]) == list(verifier.CASES)
    assert [call[0] for call in server.calls] == ["create", "agent", "report"] * 6
    assert result["cases"][3]["actual_report_verdict"] == "fail"
    assert result["cases"][3]["verification"] == "pass"
    assert result["scope"]["evidence"] == "reported_by_loopback_lab"
    assert result["scope"]["contract_model_quality_evaluated"] is False


@pytest.mark.parametrize("mutate", [
    lambda r: r["state"].update(released_amount=100, remaining_amount=0),
    lambda r: r["state"].update(authorized_amount="40"),
    lambda r: r["state"].update(released_amount=True),
    lambda r: r["decision"].update(status="ACCEPTED"),
    lambda r: r["decision"].update(round_count=True),
    lambda r: r["events"][1].update(index=-1),
    lambda r: r["events"][-1].update(status="ACCEPTED"),
    lambda r: r["operations"][-1].update(tx_id=r["decision"]["tx_id"]),
])
def test_passing_grade_cannot_hide_incorrect_state_finality_order_or_transaction_identity(server, mutate):
    server.modify = lambda name, report: mutate(report)
    result = verify(server, ["partial", "approved"])
    assert result["verification"] == "fail", result["cases"]
    assert server.create_count == 1 and result["stopped_early"] is True
    assert result["reports"]["partial"]["verification"] == "pass"  # Server grade alone is insufficient.


@pytest.mark.parametrize("mutate", [
    lambda r: r["decision"]["rounds"].pop(),
    lambda r: r["decision"]["rounds"][-1].update(result=r["decision"]["rounds"][0]["result"]),
    lambda r: r["decision"]["rounds"][1].update(kind="Validator Appeal Failed"),
    lambda r: r["decision"]["rounds"].reverse(),
])
def test_successful_appeal_without_later_accepted_changed_result_is_not_overturn_proof(server, mutate):
    server.modify = lambda name, report: mutate(report)
    result = verify(server, ["overturn"])
    assert result["verification"] == "fail", result["cases"]


def test_upheld_case_requires_observed_failed_appeal_and_unchanged_result(server):
    server.modify = lambda name, report: report["decision"]["rounds"][1].update(kind="Validator Appeal Successful")
    result = verify(server, ["upheld"])
    assert result["verification"] == "fail"
    assert result["cases"][0]["checks"]["observed_appeal_outcome"] is False


def test_unsafe_case_needs_blocked_attempt_and_only_behavior_grade_failure(server):
    server.modify = lambda name, report: report["grades"]["outcome"].update(status="fail")
    result = verify(server, ["unsafe"])
    assert result["verification"] == "fail"
    assert result["cases"][0]["checks"]["faulty_behavior_detected"] is True
    assert result["cases"][0]["checks"]["expected_server_grades"] is False


@pytest.mark.parametrize("change", [
    {"origin": "test_double"}, {"runtime_verified": 1}, {"source_commit": "foreign"},
    {"sdk_version": "new-unverified-sdk"}, {"fixture_only": False}, {"image_id": None},
])
def test_unproven_or_fake_backend_never_receives_live_verification_label(server, change):
    server.modify = lambda name, report: report["manifest"]["backend"].update(change)
    result = verify(server, ["partial", "approved"])
    assert result["verification"] == "inconclusive"
    assert result["mode"] == "unverified_http_backend"
    assert server.create_count == 1


def test_unexpected_cleanup_failure_stops_before_another_case(server):
    server.modify = lambda name, report: report.update(cleanup="unresolved", status="inconclusive")
    result = verify(server)
    assert result["verification"] == "inconclusive" and server.create_count == 1


def test_backend_identity_change_between_cases_is_inconclusive(server):
    def mutate(name, report):
        if name == "approved":
            report["manifest"]["backend"]["image_id"] = "sha256:" + "c" * 64
    server.modify = mutate
    result = verify(server, ["partial", "approved"])
    assert result["verification"] == "inconclusive"
    assert result["cases"][-1]["checks"]["stable_backend_identity"] is False


def test_create_failure_is_not_retried_or_reported_as_no_work(server):
    server.fail_create = True
    result = verify(server)
    assert server.calls == [("create", ADMIN_TOKEN)]
    assert result["verification"] == "inconclusive"
    assert result["error_code"] == "workflow_creation_unconfirmed"
    assert ADMIN_TOKEN not in repr(result)


def test_driver_failure_cancels_known_run_and_returns_sanitized_saved_report(server):
    server.fail_driver = True
    def insert_secret(name, report):
        report["untrusted_metadata"] = {"agent_token": "agent-token-" + report["run_id"],
                                         "details": ADMIN_TOKEN}
    server.modify = insert_secret
    result = verify(server, ["partial"])
    assert result["verification"] == "inconclusive" and result["error_code"] == "reference_driver_failed"
    assert any(call[0] == "cancel" for call in server.calls)
    assert ADMIN_TOKEN not in repr(result) and "agent-token-" not in repr(result)
    assert result["reports"]["partial"]["untrusted_metadata"] == {
        "agent_token": "[redacted]", "details": "[redacted]",
    }


@pytest.mark.parametrize("cases", [[], ["partial", "partial"], ["partial", "unknown"], "all", [True]])
def test_cases_are_validated_before_any_create(server, cases):
    with pytest.raises(ValueError):
        verifier.verify_workflows("http://127.0.0.1:8765", ADMIN_TOKEN, cases=cases)
    assert not server.calls


@pytest.mark.parametrize("timeout", [True, float("nan"), float("inf"), 0, -1, 1801])
def test_timeouts_are_bounded_before_any_create(server, timeout):
    with pytest.raises(ValueError):
        verify(server, ["partial"], timeout_seconds=timeout)
    assert not server.calls


def test_non_loopback_endpoint_is_rejected_before_credentials_leave_process(server):
    with pytest.raises(ValueError, match="loopback"):
        verifier.verify_workflows("https://remote.example", ADMIN_TOKEN)
    assert not server.calls


def test_progress_failure_does_not_interrupt_safe_cleanup_or_lose_evidence(server):
    def broken(_):
        raise RuntimeError("UI sink unavailable")
    result = verify(server, ["partial"], progress=broken)
    assert result["verification"] == "pass" and result["progress_callback_failed"] is True
