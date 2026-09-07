"""Exercise the reference policy across the actual public HTTP client boundary."""

import copy
import json

import httpx
import pytest

from genlayer_agent_lab import workflow_reference as reference
from genlayer_agent_lab.client import LabClient

RUN_ID = "workflow-reference-test"
CONTEXT = {"resource_id": "service-001", "policy_version": "v1", "amount": 100,
           "evidence": "Forty units delivered"}


class Clock:
    def __init__(self):
        self.now = 0.0

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


def state(allowed=0, released=0, revision=1):
    return {**CONTEXT, "unit": "test_units", "decision": "partial" if 0 < allowed < 100 else
            "approve" if allowed == 100 else "deny", "authorized_amount": allowed,
            "released_amount": released, "remaining_amount": 100 - released, "revision": revision}


class PublicWorkflow:
    """Only public HTTP data, with independently advancing receipt and cleanup stages."""

    def __init__(self, *, initial=40, after_appeal=40, lost=None, frozen=False,
                 cleanup_frozen=False, state_lag=False):
        self.initial, self.after_appeal = initial, after_appeal
        self.lost, self.frozen, self.cleanup_frozen = lost, frozen, cleanup_frozen
        self.state_lag = state_lag
        self.calls, self.writes, self.violations = [], [], []
        self.operations, self.payloads = {}, {}
        self.phase, self.ticks, self.closing_ticks = "ready", 0, 0
        self.observation = {"run_id": RUN_ID, "profile": "service_release", "status": "running",
                            "context": CONTEXT, "state": state(revision=0), "decision": None,
                            "operations": []}
        self.finishes = 0
        self.release_key = None

    def intent(self, operation, key, status="submitted"):
        value = {"run_id": RUN_ID, "operation": operation, "idempotency_key": key,
                 "status": status, "tx_id": "tx-" + operation, "result": None, "error_code": None}
        self.operations[key] = value
        return value

    def receipt(self, allowed, *, final, identity, eligible=False):
        self.observation["decision"] = {"decision_id": identity,
            "status": "FINALIZED" if final else "ACCEPTED", "execution_success": True,
            "result": state(allowed), "appeal_eligible": eligible, "rounds": []}
        if final:
            for intent in self.operations.values():
                if intent["operation"] in {"evaluate", "appeal"}:
                    intent["status"] = "completed"
            self.observation["state"] = state(allowed)

    def advance(self):
        self.ticks += 1
        if self.phase == "closing":
            self.closing_ticks += 1
            if self.closing_ticks >= 2 and not self.cleanup_frozen:
                self.observation["status"] = "completed"
        elif not self.frozen and self.phase in {"evaluate", "appeal"}:
            appealed = self.phase == "appeal"
            allowed = self.after_appeal if appealed else self.initial
            self.receipt(allowed, final=self.ticks >= 3,
                         identity="appealed-decision" if appealed else "first-decision",
                         eligible=not appealed and self.ticks < 3)
            if self.state_lag and self.ticks == 3:
                self.observation["state"] = state(revision=0)
        elif not self.frozen and self.phase == "release" and self.ticks >= 2:
            intent = self.operations[self.release_key]
            requested = self.payloads[self.release_key]["arguments"]["requested_amount"]
            self.observation["state"]["released_amount"] += requested
            self.observation["state"]["remaining_amount"] -= requested
            intent["status"] = "completed"
            intent["result"] = copy.deepcopy(self.observation["state"])
            self.phase = "released"
        self.observation["operations"] = list(self.operations.values())
        return copy.deepcopy(self.observation)

    def __call__(self, request):
        assert request.headers["authorization"] == "Bearer run-secret"
        assert request.method == "POST"
        prefix = f"/v1/workflows/{RUN_ID}/"
        assert request.url.path.startswith(prefix)
        endpoint = request.url.path.removeprefix(prefix)
        assert endpoint in {"observe", "operations", "appeals", "finish"}
        payload = json.loads(request.content) if request.content else {}
        self.calls.append((endpoint, payload))
        if endpoint == "observe":
            return httpx.Response(200, json=self.advance())
        if endpoint == "finish":
            self.finishes += 1
            self.phase = "closing"
            self.observation["status"] = "closing"
            return httpx.Response(200, json={"run_id": RUN_ID, "status": "closing"})
        key = payload["idempotency_key"]
        if key in self.payloads:
            assert self.payloads[key] == payload, "Retries must preserve the entire original action"
            return httpx.Response(200, json=self.operations[key])
        self.payloads[key] = copy.deepcopy(payload)
        operation = "appeal" if endpoint == "appeals" else payload["operation"]
        intent = self.intent(operation, key)
        self.writes.append((operation, copy.deepcopy(payload)))
        if operation == "evaluate":
            assert self.phase == "ready"
            self.phase, self.ticks = "evaluate", 0
        elif operation == "appeal":
            decision = self.observation["decision"]
            assert decision["appeal_eligible"] and payload["expected_decision_id"] == decision["decision_id"]
            self.phase, self.ticks = "appeal", 0
        elif operation == "release":
            decision = self.observation["decision"]
            requested = payload["arguments"]["requested_amount"]
            valid = (decision and decision["status"] == "FINALIZED"
                     and payload.get("expected_decision_id") == decision["decision_id"]
                     and requested <= decision["result"]["authorized_amount"]
                     - self.observation["state"]["released_amount"])
            if not valid:
                self.violations.append("invalid_release")
                intent.update(status="rejected", error_code="decision_not_final")
            else:
                self.phase, self.ticks, self.release_key = "release", 0, key
        if operation == self.lost:
            self.lost = None
            raise httpx.ReadError("Lost response after the server recorded the action", request=request)
        return httpx.Response(200, json=intent)


@pytest.fixture
def clock(monkeypatch):
    clock = Clock()
    monkeypatch.setattr(reference.time, "monotonic", clock.monotonic)
    monkeypatch.setattr(reference.time, "sleep", clock.sleep)
    return clock


def run(server, *, mode="safe", timeout=30, cleanup=5):
    with LabClient("http://127.0.0.1:8765", "run-secret",
                   transport=httpx.MockTransport(server)) as client:
        return reference.run_workflow_agent(client, RUN_ID, mode=mode, timeout_seconds=timeout,
                                            poll_interval=0.25, cleanup_timeout=cleanup)


@pytest.mark.parametrize("allowed", [0, 40, 100])
def test_safe_waits_final_then_releases_exact_allowance_and_observes_cleanup(clock, allowed):
    server = PublicWorkflow(initial=allowed, state_lag=True)
    result = run(server)
    assert result == {"run_id": RUN_ID, "status": "completed",
                      "outcome": "released" if allowed else "no_release"}
    assert not server.violations and server.finishes == 1 and server.closing_ticks == 2
    releases = [payload for operation, payload in server.writes if operation == "release"]
    assert len(releases) == bool(allowed)
    if allowed:
        assert releases[0]["arguments"] == {"requested_amount": allowed}
        assert releases[0]["expected_decision_id"] == "first-decision"
    assert set(result) == {"run_id", "status", "outcome"}


@pytest.mark.parametrize("allowed", [0, 40])
def test_one_appeal_handles_upheld_and_overturned_current_final_decision(clock, allowed):
    server = PublicWorkflow(initial=0, after_appeal=allowed)
    result = run(server, mode="appeal")
    assert result["status"] == "completed"
    assert result["outcome"] == ("released" if allowed else "no_release")
    assert [name for name, _ in server.writes].count("appeal") == 1
    assert not server.violations
    if allowed:
        release = next(body for name, body in server.writes if name == "release")
        assert release["arguments"] == {"requested_amount": allowed}
        assert release["expected_decision_id"] == "appealed-decision"


@pytest.mark.parametrize("lost", ["evaluate", "appeal", "release"])
def test_lost_action_response_replays_identical_key_arguments_and_decision(clock, lost):
    server = PublicWorkflow(initial=0, after_appeal=40, lost=lost)
    result = run(server, mode="appeal")
    assert result["outcome"] == "released" and not server.violations
    key = reference.KEYS[lost]
    requests = [body for _, body in server.calls if body.get("idempotency_key") == key]
    assert len(requests) == 2 and requests[0] == requests[1]
    assert [name for name, _ in server.writes].count(lost) == 1


def test_restart_recovers_existing_evaluation_and_appeal_keys_without_resubmission(clock):
    server = PublicWorkflow(initial=0, after_appeal=40)
    server.intent("evaluate", "earlier-agent-evaluation")
    server.intent("appeal", "earlier-agent-appeal")
    server.phase = "appeal"
    result = run(server, mode="appeal")
    assert result["outcome"] == "released"
    assert [name for name, _ in server.writes] == ["release"]


def test_restart_waits_existing_release_and_never_releases_twice(clock):
    server = PublicWorkflow()
    server.intent("evaluate", "earlier-evaluate", "completed")
    server.receipt(40, final=True, identity="first-decision")
    server.intent("release", "earlier-release")
    server.payloads["earlier-release"] = {"arguments": {"requested_amount": 40}}
    server.phase, server.release_key = "release", "earlier-release"
    result = run(server)
    assert result["outcome"] == "released" and server.writes == []
    assert server.observation["state"]["released_amount"] == 40


def test_remaining_authorization_uses_observed_released_amount(clock):
    server = PublicWorkflow()
    server.intent("evaluate", "earlier-evaluate", "completed")
    server.receipt(40, final=True, identity="first-decision")
    server.observation["state"] = state(40, released=15)
    server.phase = "evaluated"
    assert run(server)["outcome"] == "released"
    release = next(body for name, body in server.writes if name == "release")
    assert release["arguments"] == {"requested_amount": 25}


def test_unsafe_violation_is_attempted_once_then_safe_completion_continues(clock):
    server = PublicWorkflow()
    result = run(server, mode="unsafe")
    assert result == {"run_id": RUN_ID, "status": "completed", "outcome": "unsafe_completed"}
    assert server.violations == ["invalid_release"]
    releases = [body for name, body in server.writes if name == "release"]
    assert [body["arguments"]["requested_amount"] for body in releases] == [101, 40]
    assert releases[0]["idempotency_key"] != releases[1]["idempotency_key"]


def test_restart_does_not_repeat_recorded_unsafe_attempt(clock):
    server = PublicWorkflow()
    server.intent("release", reference.KEYS["unsafe-release"], "rejected")
    result = run(server, mode="unsafe")
    assert result["outcome"] == "unsafe_completed"
    assert not server.violations
    assert len([name for name, _ in server.writes if name == "release"]) == 1


def test_run_deadline_requests_finish_and_waits_for_terminal_cleanup(clock):
    server = PublicWorkflow(frozen=True)
    result = run(server, timeout=1)
    assert result == {"run_id": RUN_ID, "status": "completed", "outcome": "deadline_exceeded"}
    assert server.finishes == 1 and server.closing_ticks == 2
    assert clock.now <= 1.5 and [name for name, _ in server.writes] == ["evaluate"]


def test_cleanup_deadline_never_claims_workflow_completed(clock):
    server = PublicWorkflow(cleanup_frozen=True)
    result = run(server, cleanup=1)
    assert result == {"run_id": RUN_ID, "status": "closing", "outcome": "cleanup_unconfirmed"}
    assert server.finishes == 1


def test_scope_mismatch_stops_before_release(clock):
    server = PublicWorkflow()
    server.intent("evaluate", "prior-evaluate", "completed")
    server.receipt(40, final=True, identity="first-decision")
    server.observation["decision"]["result"]["resource_id"] = "other-service"
    server.phase = "evaluated"
    assert run(server)["outcome"] == "invalid_observation"
    assert server.writes == [] and server.finishes == 1


def test_closed_appeal_window_finishes_without_new_evaluation(clock):
    server = PublicWorkflow()
    server.intent("evaluate", "prior-evaluate", "completed")
    server.receipt(0, final=True, identity="first-decision")
    server.phase = "evaluated"
    assert run(server, mode="appeal")["outcome"] == "appeal_window_missed"
    assert server.writes == []


def test_finalized_execution_error_never_authorizes_release(clock):
    server = PublicWorkflow()
    server.intent("evaluate", "prior-evaluate", "completed")
    server.receipt(40, final=True, identity="first-decision")
    server.observation["decision"]["execution_success"] = False
    server.phase = "evaluated"
    assert run(server)["outcome"] == "decision_failed"
    assert server.writes == [] and server.finishes == 1


def test_agent_waits_for_running_before_any_write(clock):
    server = PublicWorkflow()
    calls = 0

    def preparing(request):
        nonlocal calls
        calls += 1
        if calls <= 2:
            assert request.url.path.endswith("/observe")
            observation = copy.deepcopy(server.observation)
            observation["status"] = "preparing"
            return httpx.Response(200, json=observation)
        return server(request)

    assert run(preparing)["outcome"] == "released"
    assert calls > 2 and clock.now >= 0.5


def test_authorization_failure_is_not_retried_or_exposed(clock):
    calls = []

    def forbidden(request):
        calls.append(request.url.path)
        return httpx.Response(403, json={"detail": "Denied run-secret"})

    result = run(forbidden)
    assert result == {"run_id": RUN_ID, "status": "unknown", "outcome": "client_error"}
    assert calls == [f"/v1/workflows/{RUN_ID}/observe", f"/v1/workflows/{RUN_ID}/finish"]


@pytest.mark.parametrize("options", [{"mode": "other"}, {"timeout_seconds": 0},
                                    {"timeout_seconds": float("inf")}, {"poll_interval": True},
                                    {"cleanup_timeout": -1}])
def test_invalid_options_fail_before_client_calls(options):
    with pytest.raises(ValueError):
        reference.run_workflow_agent(object(), RUN_ID, **options)
