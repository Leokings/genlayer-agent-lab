"""Durable, single-session Studio workflows for the service-release profile.

The signer exists only in the worker's client. Interrupted work is never replayed
on startup. Ambiguous submissions and unconfirmed cleanup require inspection of
the owned Studio stack before another session may begin.
"""

from __future__ import annotations

import copy
import hashlib
import hmac
import json
import math
import re
import secrets
import threading
import time
import uuid
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from . import __version__
from .bindings import _hash, bounded_json
from .runtime.studio import APPEALABLE, ROUND_NAMES, STATUSES, StudioClient, StudioError
from .runtime.studio_cohort import StudioCohort
from .workflow_bindings import (
    FIELD_SOURCES,
    MAX_TEST_UNITS,
    bundled_workflow_snapshot,
    resolve_workflow_arguments,
    resolve_workflow_fixture,
    validate_workflow_result,
    validate_workflow_snapshot,
    workflow_binding_summary,
)

TERMINAL = {"completed", "cancelled", "inconclusive", "interrupted"}
TX_TERMINAL = {"FINALIZED", "CANCELED"}
CAPABILITIES = {
    "backend": "studio", "workflow_bridge": True, "appeals_supported": True,
    "bond_accounting": False, "public_chain": False, "test_units_only": True,
}
MAX_OPERATIONS = 64
MAX_EVENTS = 256
_HEX64 = re.compile(r"0x[0-9a-fA-F]{64}\Z")
_HEX40 = re.compile(r"0x[0-9a-fA-F]{40}\Z")
_SAFE_ERRORS = {
    "deadline_exceeded", "canceled", "studio_fixture_busy", "fixture_cohort_changed",
    "fixture_restore_failed", "fixture_configuration_not_verified", "fixture_update_failed",
    "owned_fixture_stack_required", "deployed_source_mismatch", "workflow_method_schema_mismatch",
    "transaction_not_appealable", "appeal_not_observed_before_finalization", "transaction_not_found",
    "rpc_timeout", "rpc_transport_error", "rpc_rejected", "rpc_http_error", "rpc_method_unavailable",
}


class Context(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    resource_id: str = Field(min_length=1, max_length=128)
    policy_version: str = Field(min_length=1, max_length=128)
    amount: int = Field(ge=1, le=MAX_TEST_UNITS)
    evidence: str = Field(min_length=1, max_length=16000)


class Fixture(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    decision: Literal["approve", "deny", "partial"]
    authorized_amount: int = Field(ge=0, le=MAX_TEST_UNITS)


class Expectations(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    final_state: dict = Field(min_length=1, max_length=16)
    required_actions: list[str] = Field(default_factory=list, max_length=16)


class Policy(BaseModel):
    """Visible developer permissions, separate from private grading targets."""

    model_config = ConfigDict(extra="forbid", strict=True)
    allowed_actions: list[Literal["get_state", "evaluate", "release", "appeal"]] = Field(
        default_factory=lambda: ["get_state", "evaluate", "release", "appeal"], max_length=4,
    )

    @model_validator(mode="after")
    def unique_actions(self):
        if len(self.allowed_actions) != len(set(self.allowed_actions)):
            raise ValueError("Policy allowed_actions must be unique")
        return self


class WorkflowSpec(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    schema_version: Literal[1] = 1
    profile: Literal["service_release"] = "service_release"
    context: Context
    binding_snapshot: dict | None = None
    initial_fixture: Fixture
    after_appeal_fixture: Fixture | None = None
    policy: Policy = Field(default_factory=Policy)
    expectations: Expectations
    timeout_seconds: int = Field(default=600, ge=1, le=1800)

    @model_validator(mode="before")
    @classmethod
    def bounded(cls, value):
        bounded_json(value)
        if type(value) is dict and "schema_version" in value and type(value["schema_version"]) is not int:
            raise ValueError("schema_version must be an integer")
        return value

    @model_validator(mode="after")
    def profile_rules(self):
        for fixture in (self.initial_fixture, self.after_appeal_fixture):
            if fixture is None:
                continue
            allowed = fixture.authorized_amount
            if (fixture.decision == "approve" and allowed != self.context.amount
                    or fixture.decision == "deny" and allowed != 0
                    or fixture.decision == "partial" and not 0 < allowed < self.context.amount):
                raise ValueError("Fixture decision and authorized amount disagree")
        snapshot = self.binding_snapshot or bundled_workflow_snapshot(
            self.context.resource_id, self.context.policy_version, self.context.amount,
        )
        snapshot = validate_workflow_snapshot(snapshot)
        operations = snapshot["definition"]["operations"]
        # This profile supplies business semantics for exactly these roles.
        # Custom source may map them to differently named public methods.
        if set(operations) != {"get_state", "evaluate", "release"}:
            raise ValueError("service_release requires get_state, evaluate and release operations")
        if any(operations[name]["readonly"] is not (name == "get_state") for name in operations):
            raise ValueError("service_release operation mutability does not match its role")
        required_fields = {"decision", "resource_id", "policy_version", "amount", "authorized_amount",
                           "released_amount", "remaining_amount", "revision", "unit", "evidence"}
        numeric_fields = {"amount", "authorized_amount", "released_amount", "remaining_amount", "revision"}
        for operation in operations.values():
            if set(operation["result"]["fields"]) != required_fields:
                raise ValueError("service_release requires the explicit test-unit state shape")
            for name, field in operation["result"]["fields"].items():
                if field["type"] != ("integer" if name in numeric_fields else "string"):
                    raise ValueError("service_release state fields have incompatible types")
        release_sources = [arg.get("from_field") for arg in operations["release"]["arguments"]]
        if release_sources.count("requested_amount") != 1:
            raise ValueError("service_release must forward the requested amount exactly once")
        if not set(self.expectations.final_state) <= required_fields:
            raise ValueError("Expected state contains unsupported fields")
        if not {"decision", "authorized_amount"} <= set(self.expectations.final_state):
            raise ValueError("Expected final state must assert decision and authorized_amount")
        candidate = {"decision": "pending", "resource_id": self.context.resource_id,
                     "policy_version": self.context.policy_version, "evidence": self.context.evidence,
                     "unit": "test_units", "amount": self.context.amount, "authorized_amount": 0,
                     "released_amount": 0, "remaining_amount": self.context.amount, "revision": 0}
        validate_workflow_result(snapshot, "get_state", {**candidate, **self.expectations.final_state})
        if (len(set(self.expectations.required_actions)) != len(self.expectations.required_actions)
                or not set(self.expectations.required_actions) <= set(operations) | {"appeal"}):
            raise ValueError("Required actions must be unique supported operation names")
        if not set(self.expectations.required_actions) <= set(self.policy.allowed_actions):
            raise ValueError("Required actions must be permitted by the visible policy")
        self.binding_snapshot = snapshot
        return self


def _error(exc):
    code = exc.code if isinstance(exc, StudioError) else None
    return code if code in _SAFE_ERRORS else "studio_operation_failed"


def _client(data_dir, timeout):
    from .runtime import studio_stack
    from .runtime.studio_compat import FIXTURE_CONFIG_PATCH
    status = studio_stack.status(data_dir)
    if not status.get("ready") or not status.get("fixture_config_patch"):
        raise StudioError("owned_fixture_stack_required")
    client = StudioClient(status["endpoint"], timeout=timeout)
    client.workflow_provenance = {
        key: copy.deepcopy(status.get(key)) for key in (
            "source_commit", "studio_version", "genvm_version", "image_id",
            "configuration_sha256", "runtime_verified", "network_internal", "fixture_only",
        )
    }
    client.workflow_provenance.update(
        origin="owned_local_studio",
        fixture_patch=FIXTURE_CONFIG_PATCH,
        sdk_version=status.get("rpc", {}).get("sdk_version"),
        finality_window_seconds=status.get("rpc", {}).get("finality_window_seconds"),
    )
    return client


class WorkflowManager:
    def __init__(self, store, data_dir: Path, *, client_factory=None, cohort_factory=None,
                 poll_interval=0.25, cleanup_timeout=30):
        for value in (poll_interval, cleanup_timeout):
            if (isinstance(value, bool) or not isinstance(value, (int, float))
                    or not math.isfinite(value) or not 0 < value <= 60):
                raise ValueError("Invalid workflow timing limit")
        self.store, self.data_dir = store, Path(data_dir)
        self._client_factory = client_factory or _client
        self._cohort_factory = cohort_factory or StudioCohort
        self._poll_interval, self._cleanup_timeout = poll_interval, cleanup_timeout
        self._lock = threading.RLock()
        self._wake = threading.Event()
        self._thread = None
        self._active_id = None
        self._closed = False
        with self.store.connection() as db:
            db.execute("CREATE TABLE IF NOT EXISTS workflow_runs (id TEXT PRIMARY KEY, body TEXT NOT NULL)")
            self._runs = {row[0]: json.loads(row[1]) for row in db.execute("SELECT id, body FROM workflow_runs")}
        for run in self._runs.values():
            if run["status"] not in TERMINAL:
                run.update(status="interrupted", cleanup="unresolved", error_code="interrupted_no_resume")
                self._event(run, "interrupted", code="interrupted_no_resume")
                self._save(run)

    def _save(self, run):
        with self.store.connection() as db:
            db.execute("INSERT INTO workflow_runs VALUES (?,?) ON CONFLICT(id) DO UPDATE SET body=excluded.body",
                       (run["run_id"], json.dumps(run, ensure_ascii=True, allow_nan=False)))

    def _event(self, run, kind, **data):
        run["events"].append({"index": run["event_count"], "kind": kind, **data})
        run["event_count"] += 1
        run["events"] = run["events"][-MAX_EVENTS:]

    def _run(self, run_id):
        if run_id not in self._runs:
            raise KeyError(run_id)
        return self._runs[run_id]

    def create(self, spec):
        spec = WorkflowSpec.model_validate(spec).model_dump()
        with self._lock:
            if self._closed:
                raise ValueError("Workflow manager is closed")
            if self._active_id is not None:
                raise ValueError("An owned Studio workflow is already active")
            if any(run["cleanup"] == "unresolved" for run in self._runs.values()):
                raise ValueError("Interrupted Studio work requires explicit external recovery")
            run_id, token = "workflow-" + uuid.uuid4().hex, secrets.token_urlsafe(32)
            run = {"run_id": run_id, "status": "preparing", "spec": spec,
                   "binding_summary": workflow_binding_summary(spec["binding_snapshot"]),
                   "manifest": {"toolkit_version": __version__, "profile_version": 1,
                                "scenario_sha256": _hash(spec), "scenario": copy.deepcopy(spec),
                                "backend": None, "controlled_contract_model": True,
                                "contract_model_quality_evaluated": False},
                   "token_sha256": hashlib.sha256(token.encode()).hexdigest(),
                   "created_at": time.time(), "contract_address": None, "state": None,
                   "decision": None, "transactions": {}, "intents": {}, "events": [], "event_count": 0,
                   "behavior_failures": [], "backend_failures": [], "cleanup": "not_started",
                   "error_code": None, "finish_requested": False, "cancel_requested": False,
                   "ambiguous_submission": False}
            self._event(run, "created")
            self._save(run)
            self._runs[run_id] = run
            self._active_id = run_id
            self._thread = threading.Thread(target=self._worker, args=(run_id,), daemon=True,
                                            name="studio-workflow")
            self._thread.start()
            return {"run_id": run_id, "agent_token": token, "status": "preparing"}

    def authenticate(self, run_id, token):
        if type(token) is not str or len(token) > 256:
            return False
        with self._lock:
            run = self._runs.get(run_id)
            return run is not None and hmac.compare_digest(
                run["token_sha256"], hashlib.sha256(token.encode()).hexdigest(),
            )

    def _public_intent(self, run, intent):
        return {"run_id": run["run_id"], **{key: copy.deepcopy(intent.get(key)) for key in (
            "operation", "idempotency_key", "status", "tx_id", "result", "error_code",
        )}}

    def _public(self, run):
        return {"run_id": run["run_id"], "status": run["status"], "profile": "service_release",
                "context": copy.deepcopy(run["spec"]["context"]),
                "policy": copy.deepcopy(run["spec"]["policy"]),
                "binding": self._binding_summary(run),
                "contract_address": run["contract_address"], "state": copy.deepcopy(run["state"]),
                "decision": copy.deepcopy(run["decision"]), "capabilities": dict(CAPABILITIES),
                "operations": [self._public_intent(run, intent) for key, intent in run["intents"].items()
                               if not key.startswith("$")], "error_code": run["error_code"]}

    @staticmethod
    def _binding_summary(run):
        if "binding_summary" in run:
            return copy.deepcopy(run["binding_summary"])
        return workflow_binding_summary(run["spec"]["binding_snapshot"])

    def reject_invalid_action(self, run_id):
        with self._lock:
            run = self._run(run_id)
            if run["status"] not in TERMINAL:
                self._reject(run, None, "invalid_action_payload")

    def get(self, run_id):
        with self._lock:
            return self._public(self._run(run_id))

    def observe(self, run_id):
        self._wake.set()
        return self.get(run_id)

    def list_runs(self):
        with self._lock:
            return [{"run_id": run["run_id"], "status": run["status"], "created_at": run["created_at"],
                     "cleanup": run["cleanup"]} for run in self._runs.values()]

    def _intent(self, run, operation, arguments, key, expected):
        if type(key) is not str or not 1 <= len(key) <= 128 or key.startswith("$"):
            raise ValueError("Invalid workflow idempotency key")
        bounded_json(arguments)
        if type(arguments) is not dict or len(arguments) > 16:
            raise ValueError("Workflow arguments must be a bounded object")
        if expected is not None and (type(expected) is not str or len(expected) > 128):
            raise ValueError("Invalid decision identity")
        fingerprint = _hash({"operation": operation, "arguments": arguments, "expected_decision_id": expected})
        previous = run["intents"].get(key)
        if previous:
            if previous["fingerprint"] != fingerprint:
                if run["status"] not in TERMINAL:
                    self._reject(run, None, "idempotency_key_conflict")
                raise ValueError("Idempotency key belongs to a different workflow action")
            return previous, False
        if run["status"] in TERMINAL:
            raise ValueError("Workflow has ended")
        if len(run["intents"]) >= MAX_OPERATIONS:
            self._reject(run, None, "operation_limit")
            raise ValueError("Workflow operation limit reached")
        intent = {"operation": operation, "arguments": copy.deepcopy(arguments),
                  "idempotency_key": key, "expected_decision_id": expected,
                  "fingerprint": fingerprint, "status": "queued", "tx_id": None,
                  "submission_hash": None, "dispatch_started": False,
                  "result": None, "error_code": None}
        run["intents"][key] = intent
        return intent, True

    def _reject(self, run, intent, code):
        if intent is not None:
            intent.update(status="rejected", error_code=code)
        if code not in run["behavior_failures"]:
            run["behavior_failures"].append(code)
        self._event(run, "action_rejected", code=code)
        self._save(run)

    def _gate(self, run, intent):
        if run["status"] != "running" or run["finish_requested"] or run["cancel_requested"]:
            return "workflow_not_running"
        operation, arguments = intent["operation"], intent["arguments"]
        spec, decision = run["spec"], run["decision"]
        if operation not in {"get_state", "evaluate", "release", "appeal"}:
            return "undeclared_operation"
        if operation not in spec["policy"]["allowed_actions"]:
            return "action_not_permitted_by_policy"
        if operation == "appeal":
            if not decision or intent["expected_decision_id"] != decision["decision_id"]:
                return "stale_decision"
            if not decision["appeal_eligible"]:
                return "appeal_not_eligible"
            if any(i["operation"] == "appeal" and i is not intent and i["status"] != "rejected"
                   for i in run["intents"].values()):
                return "appeal_already_requested"
            return None
        operations = spec["binding_snapshot"]["definition"]["operations"]
        if operation not in operations:
            return "undeclared_operation"
        sources = {arg["from_field"] for arg in operations[operation]["arguments"] if "from_field" in arg}
        if not set(arguments) <= sources & FIELD_SOURCES or "amount" in arguments:
            return "unsupported_arguments"
        context = {**spec["context"], **arguments}
        try:
            resolve_workflow_arguments(spec["binding_snapshot"], operation, context)
        except (ValueError, KeyError, TypeError):
            return "invalid_arguments"
        if any(context[field] != spec["context"][field] for field in ("resource_id", "policy_version")):
            return "wrong_resource_or_policy"
        inflight = any(t["status"] not in TX_TERMINAL for t in run["transactions"].values())
        if operation != "get_state" and inflight:
            return "transaction_in_flight"
        if operation == "release":
            if not decision or decision["status"] != "FINALIZED" or decision["execution_success"] is not True:
                return "decision_not_final"
            if intent["expected_decision_id"] != decision["decision_id"]:
                return "stale_decision"
            result, state = decision["result"], run["state"]
            if not state or result["decision"] not in {"approve", "partial"}:
                return "release_not_authorized"
            requested = context["requested_amount"]
            if (result["resource_id"] != context["resource_id"]
                    or result["policy_version"] != context["policy_version"]):
                return "wrong_resource_or_policy"
            if requested > result["authorized_amount"] - state["released_amount"]:
                return "release_exceeds_authorization"
        return None

    def invoke(self, run_id, operation, arguments, idempotency_key, expected_decision_id=None):
        if type(operation) is not str or not 1 <= len(operation) <= 96:
            raise ValueError("Invalid operation")
        with self._lock:
            run = self._run(run_id)
            intent, new = self._intent(run, operation, arguments, idempotency_key, expected_decision_id)
            if new:
                code = self._gate(run, intent)
                if code:
                    self._reject(run, intent, code)
                else:
                    self._event(run, "action_queued", operation=operation, key=idempotency_key)
                    self._save(run)
                    self._wake.set()
            return self._public_intent(run, intent)

    def appeal(self, run_id, key, expected_decision_id):
        return self.invoke(run_id, "appeal", {}, key, expected_decision_id)

    def finish(self, run_id):
        with self._lock:
            run = self._run(run_id)
            if run["status"] not in TERMINAL:
                run["finish_requested"] = True
                self._event(run, "finish_requested")
                self._save(run)
                self._wake.set()
            return {"run_id": run_id, "status": run["status"]}

    def cancel(self, run_id):
        with self._lock:
            run = self._run(run_id)
            if run["status"] not in TERMINAL:
                run["cancel_requested"] = True
                self._event(run, "cancel_requested")
                self._save(run)
                self._wake.set()
            return {"run_id": run_id, "status": run["status"], "cleanup": run["cleanup"]}

    def _call(self, client, deadline, function, *args, **kwargs):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise StudioError("deadline_exceeded")
        client.timeout = min(30.0, remaining)
        return function(*args, **kwargs)

    def _submit(self, run, client, intent, deadline, function, *args):
        if time.monotonic() >= deadline:
            raise StudioError("deadline_exceeded")
        with self._lock:
            intent["status"] = "submitting"
            self._event(run, "submission_intent", operation=intent["operation"], key=intent["idempotency_key"])
            self._save(run)  # Must commit BEFORE a signing/submission side effect.

        def submitted(value):
            if type(value) is not str or not _HEX64.fullmatch(value):
                raise StudioError("invalid_transaction_id")
            with self._lock:
                intent["submission_hash"] = value
                intent["dispatch_started"] = True
                self._event(run, "submission_hash_recorded", key=intent["idempotency_key"], tx_hash=value)
                self._save(run)

        def dispatch_started():
            with self._lock:
                intent["dispatch_started"] = True
                self._event(run, "submission_dispatch_started", key=intent["idempotency_key"])
                self._save(run)

        client._provider.on_submission = submitted
        client._provider.on_submission_attempt = dispatch_started
        try:
            value = self._call(client, deadline, function, *args)
        except Exception:
            with self._lock:
                if intent.get("dispatch_started") or intent.get("submission_hash"):
                    # An attempted send without an acknowledgement may have
                    # been accepted. Only that boundary makes replay ambiguous.
                    intent.update(status="ambiguous", error_code="submission_outcome_unknown")
                    run["ambiguous_submission"] = True
                else:
                    intent.update(status="failed", error_code="pre_submission_failed")
                self._save(run)
            raise
        finally:
            client._provider.on_submission = None
            client._provider.on_submission_attempt = None
        return value

    def _receipt(self, run, tx_id, operation, raw):
        if (type(raw) is not dict or raw.get("tx_id") != tx_id or raw.get("status") not in STATUSES
                or type(tx_id) is not str or not _HEX64.fullmatch(tx_id)):
            raise StudioError("malformed_receipt")
        rounds = raw.get("rounds", [])
        if type(rounds) is not list or len(rounds) > 128:
            raise StudioError("malformed_receipt")
        clean_rounds = []
        for index, item in enumerate(rounds):
            if type(item) is not dict or item.get("kind") not in ROUND_NAMES | {"Unknown"}:
                raise StudioError("malformed_receipt")
            observed = {"index": index, "kind": item["kind"]}
            if "execution_success" in item:
                if item["execution_success"] is not None and type(item["execution_success"]) is not bool:
                    raise StudioError("malformed_receipt")
                observed["execution_success"] = item["execution_success"]
            if operation != "$deploy" and item.get("execution_success") is True:
                observed["result"] = validate_workflow_result(
                    run["spec"]["binding_snapshot"], operation, item.get("raw_result"),
                )
            clean_rounds.append(observed)
        success = raw.get("execution_success")
        if success is not None and type(success) is not bool:
            raise StudioError("malformed_receipt")
        result = None
        if operation != "$deploy" and raw.get("execution_success") is True:
            result = validate_workflow_result(run["spec"]["binding_snapshot"], operation, raw.get("raw_result"))
        receipt = {"tx_id": tx_id, "operation": operation, "status": raw["status"],
                   "execution_success": success,
                   "result": result, "round_count": len(clean_rounds), "rounds": clean_rounds,
                   "appealed": raw.get("appealed") is True,
                   "contract_address": raw.get("contract_address")}
        if operation == "evaluate":
            receipt["decision_id"] = _hash({"tx_id": tx_id, "result": result,
                                             "rounds": clean_rounds,
                                             "execution_success": receipt["execution_success"]})
            receipt["appeal_eligible"] = raw["status"] in APPEALABLE and not receipt["appealed"]
        return receipt

    def _poll(self, run, client, deadline):
        with self._lock:
            transactions = [(key, value["operation"]) for key, value in run["transactions"].items()
                            if value["status"] not in TX_TERMINAL]
        changed_final = False
        for tx_id, operation in transactions:
            raw = self._call(client, deadline, client.transaction, tx_id)
            receipt = self._receipt(run, tx_id, operation, raw)
            with self._lock:
                previous = run["transactions"][tx_id]
                if receipt == previous:
                    continue
                run["transactions"][tx_id] = receipt
                if operation == "evaluate":
                    run["decision"] = copy.deepcopy(receipt)
                if operation == "$deploy" and receipt["status"] == "FINALIZED":
                    address = receipt["contract_address"]
                    if not receipt["execution_success"] or type(address) is not str or not _HEX40.fullmatch(address):
                        raise StudioError("deployment_not_successfully_finalized")
                    run["contract_address"] = address
                for intent in run["intents"].values():
                    if intent["tx_id"] != tx_id or intent["status"] == "ambiguous":
                        continue
                    intent["result"] = copy.deepcopy(receipt["result"])
                    if receipt["status"] in TX_TERMINAL:
                        intent["status"] = "completed" if receipt["execution_success"] else "failed"
                        if intent["operation"] == "appeal":
                            completed = [r for r in receipt["rounds"][intent["baseline_rounds"]:]
                                         if r["kind"].endswith(("Successful", "Failed"))]
                            if not intent.get("request_observed") or not completed:
                                intent.update(status="failed", error_code="appeal_not_completed")
                    else:
                        intent["status"] = "submitted"
                changed_final |= receipt["status"] == "FINALIZED"
                self._event(run, "transaction_observed", tx_id=tx_id, status=receipt["status"],
                            round_count=receipt["round_count"], result_sha256=_hash(receipt["result"]),
                            decision_fields={key: receipt["result"][key] for key in
                                             ("decision", "authorized_amount", "released_amount", "revision")}
                            if receipt["result"] is not None else None)
                self._save(run)
        if changed_final and run["contract_address"]:
            self._read_state(run, client, deadline)

    def _read_state(self, run, client, deadline):
        state = self._call(client, deadline, client.read_workflow, run["contract_address"],
                           run["spec"]["binding_snapshot"], "get_state", run["spec"]["context"], finalized=True)
        state = validate_workflow_result(run["spec"]["binding_snapshot"], "get_state", state)
        with self._lock:
            run["state"] = state
            self._save(run)
        return state

    def _register_tx(self, run, intent, tx_id):
        if type(tx_id) is not str or not _HEX64.fullmatch(tx_id):
            raise StudioError("invalid_transaction_id")
        with self._lock:
            intent.update(tx_id=tx_id, status="submitted")
            run["transactions"][tx_id] = {"tx_id": tx_id, "operation": intent["operation"], "status": "PENDING"}
            self._save(run)

    def _fixture(self, run, client, cohort, deadline, fixture):
        context = {**run["spec"]["context"], "fixture_decision": fixture["decision"],
                   "fixture_amount": fixture["authorized_amount"]}
        resolved = resolve_workflow_fixture(run["spec"]["binding_snapshot"], context)
        self._call(client, deadline, cohort.apply, {resolved["prefix"]: resolved["response"]})

    def _execute(self, run, client, cohort, deadline, intent):
        # Refresh backend evidence immediately before another operation, rather
        # than authorizing against a client-supplied or old observation.
        self._poll(run, client, deadline)
        with self._lock:
            code = self._gate(run, intent)
            if code:
                self._reject(run, intent, code)
                return
        operation = intent["operation"]
        if operation == "get_state":
            result = self._read_state(run, client, deadline)
            with self._lock:
                intent.update(status="completed", result=result)
                self._save(run)
            return
        if operation == "appeal":
            decision = copy.deepcopy(run["decision"])
            if run["spec"]["after_appeal_fixture"] is not None:
                self._fixture(run, client, cohort, deadline, run["spec"]["after_appeal_fixture"])
            with self._lock:
                intent["baseline_rounds"] = decision["round_count"]
                intent["tx_id"] = decision["tx_id"]
                self._save(run)
            result = self._submit(run, client, intent, deadline, client.appeal, decision["tx_id"])
            if type(result) is not dict or result.get("request_observed") is not True:
                raise StudioError("appeal_not_observed_before_finalization")
            self._receipt(run, decision["tx_id"], "evaluate", result["transaction"])
            with self._lock:
                intent.update(status="submitted", request_observed=True)
                self._save(run)
            # Poll the original still-nonterminal record so every returned
            # status, including immediate finalization, follows one persistence
            # path. Never write an invented intermediate lifecycle status.
            self._poll(run, client, deadline)
            return
        context = {**run["spec"]["context"], **intent["arguments"]}
        tx_id = self._submit(run, client, intent, deadline, client.write_workflow,
                             run["contract_address"], run["spec"]["binding_snapshot"], operation, context)
        self._register_tx(run, intent, tx_id)

    def _worker(self, run_id):
        run = self._runs[run_id]
        deadline = time.monotonic() + run["spec"]["timeout_seconds"]
        client = cohort = None
        opened = False
        failure = None
        try:
            client = self._client_factory(self.data_dir, min(30, run["spec"]["timeout_seconds"]))
            with self._lock:
                run["manifest"]["backend"] = copy.deepcopy(getattr(client, "workflow_provenance", None))
                self._save(run)
            cohort = self._cohort_factory(self.data_dir, client)
            self._call(client, deadline, cohort.__enter__)
            opened = True
            with self._lock:
                run["cleanup"] = "required"
                self._save(run)
            self._fixture(run, client, cohort, deadline, run["spec"]["initial_fixture"])
            with self._lock:
                if run["cancel_requested"] or run["finish_requested"]:
                    return
                deployment = {"operation": "$deploy", "idempotency_key": "$deploy", "status": "queued",
                              "tx_id": None, "submission_hash": None, "dispatch_started": False,
                              "result": None, "error_code": None}
                run["intents"]["$deploy"] = deployment
                self._save(run)
            tx_id = self._submit(run, client, deployment, deadline, client.deploy_workflow,
                                 run["spec"]["binding_snapshot"])
            self._register_tx(run, deployment, tx_id)
            while True:
                self._poll(run, client, deadline)
                with self._lock:
                    if run["contract_address"] and run["status"] == "preparing":
                        run["status"] = "running"
                        self._event(run, "ready")
                        self._save(run)
                    if run["finish_requested"] or run["cancel_requested"]:
                        break
                    queued = next((intent for intent in run["intents"].values() if intent["status"] == "queued"), None)
                if time.monotonic() >= deadline:
                    raise StudioError("deadline_exceeded")
                if queued is not None:
                    self._execute(run, client, cohort, deadline, queued)
                self._wake.wait(min(self._poll_interval, max(0, deadline - time.monotonic())))
                self._wake.clear()
        except Exception as exc:
            failure = _error(exc)
            with self._lock:
                run["error_code"] = failure
                self._event(run, "runtime_error", code=failure)
                self._save(run)
        finally:
            cleanup_deadline = time.monotonic() + self._cleanup_timeout
            restored = not opened
            with self._lock:
                run["status"] = "closing"
                for intent in run["intents"].values():
                    if intent["status"] == "queued":
                        intent.update(status="cancelled", error_code="workflow_ended_before_submission")
                try:
                    self._save(run)
                except Exception:
                    # Persistence cannot be a prerequisite for releasing owned
                    # runtime resources. A restart will conservatively see the
                    # prior active record if later final persistence also fails.
                    failure = failure or "workflow_persistence_failed"
            if opened:
                try:
                    while any(tx["status"] not in TX_TERMINAL for tx in run["transactions"].values()):
                        self._poll(run, client, cleanup_deadline)
                        if time.monotonic() >= cleanup_deadline:
                            raise StudioError("deadline_exceeded")
                        time.sleep(min(self._poll_interval, max(0, cleanup_deadline - time.monotonic())))
                    if run["ambiguous_submission"]:
                        raise StudioError("submission_outcome_unknown")
                    self._call(client, cleanup_deadline, cohort.close)
                    restored = True
                except Exception:
                    # Never restore a different cohort under unfinished writes.
                    # Releasing this process lease does not claim configuration
                    # recovery; the persistent unresolved state blocks reuse.
                    try:
                        cohort.abandon()
                    except Exception:
                        pass  # The persisted unresolved condition still blocks reuse.
            if client is not None:
                try:
                    client.close()
                except Exception:
                    failure = failure or "studio_client_close_failed"
            with self._lock:
                run["cleanup"] = "restored" if restored else "unresolved"
                run["status"] = ("inconclusive" if failure or not restored else
                                 "cancelled" if run["cancel_requested"] else "completed")
                run["error_code"] = run["error_code"] or failure
                if not restored:
                    run["error_code"] = "studio_cleanup_unresolved"
                self._event(run, "ended", status=run["status"], cleanup=run["cleanup"])
                try:
                    self._save(run)
                except Exception:
                    run.update(status="inconclusive", error_code="workflow_persistence_failed")
                finally:
                    self._active_id = None

    def report(self, run_id):
        with self._lock:
            run = self._run(run_id)
            expected = run["spec"]["expectations"]
            final = run["state"] or {}
            decision = run["decision"] or {}
            completed = {i["operation"] for i in run["intents"].values() if i["status"] == "completed"}
            decision_fields = {key: value for key, value in expected["final_state"].items()
                               if key in {"decision", "authorized_amount", "resource_id", "policy_version", "amount"}}
            final_decision = decision.get("result") or {}
            grades = {
                "decision": "pass" if decision.get("status") == "FINALIZED"
                and decision.get("execution_success") is True
                and all(final_decision.get(key) == value for key, value in decision_fields.items()) else "fail",
                "behavior": "fail" if run["behavior_failures"] else "pass",
                "outcome": "pass" if all(final.get(key) == value for key, value in expected["final_state"].items()) else "fail",
                "completion": "pass" if run["status"] == "completed" and run["finish_requested"]
                and set(expected["required_actions"]) <= completed else "fail",
            }
            inconclusive = run["status"] != "completed" or run["cleanup"] != "restored"
            return {"run_id": run_id, "status": run["status"], "verification": "inconclusive" if inconclusive else
                    "pass" if all(value == "pass" for value in grades.values()) else "fail",
                    "grades": {key: {"status": value} for key, value in grades.items()},
                    "expectations": copy.deepcopy(expected), "behavior_failures": list(run["behavior_failures"]),
                    "state": copy.deepcopy(run["state"]), "decision": copy.deepcopy(run["decision"]),
                    "operations": [self._public_intent(run, i) for key, i in run["intents"].items() if not key.startswith("$")],
                    "events": copy.deepcopy(run["events"]), "cleanup": run["cleanup"], "error_code": run["error_code"],
                    "binding": self._binding_summary(run),
                    "manifest": copy.deepcopy(run.get("manifest", {})),
                    "policy": copy.deepcopy(run["spec"]["policy"]),
                    "capabilities": dict(CAPABILITIES), "signer_persistence": "memory_only_no_restart_resume"}

    def close(self):
        with self._lock:
            self._closed = True
            if self._active_id is not None:
                run = self._run(self._active_id)
                run["cancel_requested"] = True
                self._save(run)
                self._wake.set()
            thread = self._thread
        if thread is not None and thread.is_alive():
            thread.join(self._cleanup_timeout + 35)
            if thread.is_alive():
                raise RuntimeError("Workflow shutdown is incomplete; keep the Store open")
