"""Agent-driven project workflows with a journal written before every submission.

The local Studio owns consensus, execution and economic accounting. This driver
owns test inputs, permissions, durable observations and independent reporting.
Only identical signed bytes may be retried after an uncertain response.
"""

from __future__ import annotations

import copy
import hashlib
import hmac
import json
import re
import secrets
import threading
import time
import uuid
from pathlib import Path

from . import __version__
from .bindings import _hash
from .project_journal import ProjectVault
from .runtime.studio import APPEALABLE, STATUSES, StudioError

TERMINAL = {"completed", "cancelled", "inconclusive"}
TX_TERMINAL = {"FINALIZED", "CANCELED"}
BUILTINS = {"inspect_fees", "inspect_appeal", "read_evidence", "appeal", "submit_investigation"}
HEX64 = re.compile(r"0x[0-9a-fA-F]{64}\Z")
HEX40 = re.compile(r"0x[0-9a-fA-F]{40}\Z")
MAX_OPERATIONS = 128
MAX_EVENTS = 1024


def _safe_error(exc):
    # Provider bodies, contract errors and validation inputs can include secrets.
    if isinstance(exc, StudioError) and re.fullmatch(r"[a-z][a-z0-9_]{0,100}", exc.code):
        return exc.code
    return "project_runtime_error"


def _default_client(data_dir, private_key):
    from .runtime.studio_modern import StudioModernClient
    from .runtime.studio_profiles import modern_profile_status

    status = modern_profile_status(data_dir)
    if not status.get("ready") or not status.get("runtime_verified"):
        raise StudioError("modern_studio_not_ready")
    client = StudioModernClient(status["endpoint"], account_private_key=private_key, timeout=45)
    client.workflow_provenance = status
    return client


def _default_cohort(data_dir, client, state, save):
    from .runtime.project_cohort import ProjectCohort

    return ProjectCohort(data_dir, client, state=state, persist=save)


class ProjectWorkflowManager:
    def __init__(self, store, data_dir: Path, *, client_factory=None, cohort_factory=None,
                 poll_interval=.25, cleanup_timeout=60):
        self.store, self.data_dir = store, Path(data_dir)
        self._client_factory = client_factory or _default_client
        self._cohort_factory = cohort_factory or _default_cohort
        self._poll_interval, self._cleanup_timeout = poll_interval, cleanup_timeout
        self._lock = threading.RLock()
        self._wake, self._stop = threading.Event(), threading.Event()
        self._thread = None
        self._active_id = None
        with self.store.connection() as db:
            db.execute("CREATE TABLE IF NOT EXISTS project_runs (id TEXT PRIMARY KEY, body TEXT NOT NULL)")
            self._runs = {row[0]: json.loads(row[1]) for row in db.execute("SELECT id,body FROM project_runs")}
        pending = [run for run in self._runs.values()
                   if run["status"] not in TERMINAL or self._can_retry_cleanup(run)]
        if len(pending) > 1:
            raise RuntimeError("Multiple unfinished project workflows require inspection")
        if pending:
            run = pending[0]
            if self._can_retry_cleanup(run):
                run.setdefault("cleanup_retry_error", run.get("error_code") or "fixture_cleanup_unresolved")
            # Cleanup recovery never accepts new actions or resumes failed work.
            # The marker survives another interruption during this retry.
            cleanup_only = bool(run.get("cleanup_retry_error"))
            run["status"] = "closing" if cleanup_only else "recovering"
            self._event(run, "cleanup_recovery_started" if cleanup_only else "recovery_started")
            self._save(run)
            self._start(run)

    @staticmethod
    def _can_retry_cleanup(run):
        return (run.get("status") == "inconclusive" and run.get("cleanup") == "unresolved"
                and run.get("error_code") != "interrupted_by_restore"
                and type(run.get("private_account")) is str
                and type(run.get("fixture_state")) is dict)

    def _save(self, run):
        with self.store.connection() as db:
            db.execute("INSERT INTO project_runs VALUES (?,?) ON CONFLICT(id) DO UPDATE SET body=excluded.body",
                       (run["run_id"], json.dumps(run, allow_nan=False)))

    def _event(self, run, kind, **data):
        run["events"].append({"index": run["event_count"], "at": time.time(), "kind": kind, **data})
        run["event_count"] += 1
        run["events"] = run["events"][-MAX_EVENTS:]

    def _start(self, run):
        self._active_id = run["run_id"]
        self._thread = threading.Thread(target=self._worker, args=(run["run_id"],),
                                        daemon=True, name="studio-project-workflow")
        self._thread.start()

    def create(self, spec):
        from .project_scenarios import validate_project_scenario

        spec = validate_project_scenario(spec, require_review=True)
        with self._lock:
            if self._stop.is_set():
                raise ValueError("Project manager is stopping")
            if self._active_id or any(r.get("cleanup") == "unresolved" for r in self._runs.values()):
                raise ValueError("An unfinished project workflow must recover before starting another")
            vault = ProjectVault(self.data_dir)
            account = vault.new_account()
            token = secrets.token_urlsafe(32)
            created = time.time()
            run = {
                "run_id": "project-" + uuid.uuid4().hex, "status": "preparing", "spec": spec,
                "token_sha256": hashlib.sha256(token.encode()).hexdigest(),
                "private_account": vault.seal(account), "account_address": account["address"],
                "created_at": created, "deadline_at": created + spec["timeout_seconds"],
                "contracts": {}, "state": {}, "transactions": {}, "intents": {},
                "events": [], "event_count": 0, "behavior_failures": [], "backend_failures": [],
                "fixture_state": None, "fixture_phase": None, "cleanup": "not_started",
                "finish_requested": False, "cancel_requested": False, "error_code": None,
                "fee_reserved": 0, "setup_fee_reserved": 0,
                "manifest": {"toolkit_version": __version__, "schema_version": 2,
                             "scenario_sha256": _hash(spec), "backend": None,
                             "controlled_contract_model": True, "contract_model_quality_evaluated": False},
            }
            self._event(run, "created")
            self._save(run)
            self._runs[run["run_id"]] = run
            self._start(run)
            return {"run_id": run["run_id"], "agent_token": token, "status": "preparing"}

    def authenticate(self, run_id, token):
        with self._lock:
            run = self._runs.get(run_id)
            return bool(run and type(token) is str and len(token) <= 256 and hmac.compare_digest(
                run["token_sha256"], hashlib.sha256(token.encode()).hexdigest()))

    @staticmethod
    def _public_intent(intent):
        return {key: copy.deepcopy(intent.get(key)) for key in (
            "operation", "arguments", "idempotency_key", "status", "tx_id", "target_tx_id",
            "result", "error_code", "execution_success", "fee", "decision_id", "transaction_status",
        )}

    def _observation(self, run):
        from .project_bindings import project_binding_summary
        from .project_investigation import investigation_view
        from .project_scenarios import agent_scenario_view

        return {"run_id": run["run_id"], "status": run["status"], "profile": "project",
                **agent_scenario_view(run["spec"]),
                "binding": project_binding_summary(run["spec"]["project_snapshot"]),
                "contracts": copy.deepcopy(run["contracts"]), "state": copy.deepcopy(run["state"]),
                "transactions": copy.deepcopy(run["transactions"]),
                "child_effects": self._child_effects(run),
                "operations": [self._public_intent(i) for i in run["intents"].values()
                               if not i["operation"].startswith("$")],
                **investigation_view(run["intents"].values()),
                "account_address": run["account_address"],
                "fee_reserved": run["fee_reserved"], "fee_unit": "local_GEN_base_units",
                "error_code": run["error_code"]}

    @staticmethod
    def _child_effects(run):
        totals = {"total": 0, "pending": 0, "failed": 0, "succeeded": 0, "by_operation": {}}
        for tx in run["transactions"].values():
            parent = run["transactions"].get(tx.get("parent_tx_id"))
            if parent is None:
                continue
            group = totals["by_operation"].setdefault(parent["operation"],
                                                      {"total": 0, "pending": 0, "failed": 0, "succeeded": 0})
            kind = ("failed" if tx["status"] == "CANCELED" else
                    "pending" if tx["status"] not in TX_TERMINAL or tx["execution_success"] is None
                    else "succeeded" if tx["execution_success"] else "failed")
            for target in (totals, group):
                target["total"] += 1
                target[kind] += 1
        return totals

    def observe(self, run_id):
        with self._lock:
            return self._observation(self._runs[run_id])

    def get(self, run_id):
        with self._lock:
            run = self._runs[run_id]
            return {**self._observation(run), "title": run["spec"]["title"], "cleanup": run["cleanup"],
                    "manifest": copy.deepcopy(run["manifest"])}

    def list_runs(self):
        with self._lock:
            return [{"run_id": r["run_id"], "title": r["spec"]["title"], "status": r["status"], "profile": "project",
                     "created_at": r["created_at"], "cleanup": r["cleanup"]}
                    for r in reversed(list(self._runs.values()))]

    def reject_invalid_action(self, run_id):
        with self._lock:
            run = self._runs[run_id]
            if run["status"] not in TERMINAL:
                self._failure(run, "invalid_action_request")

    def _failure(self, run, code):
        if len(run["behavior_failures"]) < MAX_OPERATIONS:
            run["behavior_failures"].append(code)
        self._event(run, "action_rejected", code=code)
        self._save(run)

    def invoke(self, run_id, operation, arguments, idempotency_key, expected_decision_id=None):
        from .bindings import bounded_json
        from .project_bindings import normalize_project_arguments

        bounded_json(arguments)
        if (type(operation) is not str or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,95}", operation)
                or type(arguments) is not dict or len(arguments) > 32
                or type(idempotency_key) is not str or not 1 <= len(idempotency_key) <= 128
                or idempotency_key.startswith("$")
                or expected_decision_id is not None and
                (type(expected_decision_id) is not str or len(expected_decision_id) > 128)):
            raise ValueError("Invalid project operation")
        with self._lock:
            run = self._runs[run_id]
            if operation in run["spec"]["project_snapshot"]["definition"]["operations"]:
                try:
                    arguments = normalize_project_arguments(run["spec"]["project_snapshot"], operation, arguments)
                except ValueError:
                    # Preserve invalid attempts for the worker's policy/report checks.
                    pass
            fingerprint = _hash({"operation": operation, "arguments": arguments, "expected": expected_decision_id})
            previous = run["intents"].get(idempotency_key)
            if previous:
                if previous["fingerprint"] != fingerprint:
                    self._failure(run, "idempotency_conflict")
                    raise ValueError("Idempotency key was already used for different arguments")
                return {"run_id": run_id, **self._public_intent(previous)}
            if run["status"] not in {"running", "recovering"} or run["finish_requested"] or run["cancel_requested"]:
                raise ValueError("Project workflow is not accepting operations")
            if len(run["intents"]) >= MAX_OPERATIONS:
                self._failure(run, "operation_limit_exceeded")
                raise ValueError("Workflow operation limit exceeded")
            intent = {"operation": operation, "arguments": copy.deepcopy(arguments),
                      "idempotency_key": idempotency_key, "fingerprint": fingerprint,
                      "expected_decision_id": expected_decision_id, "status": "queued",
                      "tx_id": None, "target_tx_id": None, "result": None,
                      "execution_success": None, "fee": 0, "error_code": None,
                      "prepared": None, "submission_attempts": 0}
            run["intents"][idempotency_key] = intent
            self._event(run, "operation_queued", operation=operation)
            self._save(run)
            self._wake.set()
            return {"run_id": run_id, **self._public_intent(intent)}

    def appeal(self, run_id, key, expected_decision_id):
        return self.invoke(run_id, "appeal", {}, key, expected_decision_id)

    def finish(self, run_id):
        with self._lock:
            run = self._runs[run_id]
            if run["status"] not in TERMINAL:
                run["finish_requested"] = True
                self._event(run, "finish_requested")
                self._save(run)
                self._wake.set()
            return {"run_id": run_id, "status": run["status"]}

    def cancel(self, run_id):
        with self._lock:
            run = self._runs[run_id]
            if run["status"] not in TERMINAL:
                run["cancel_requested"] = True
                self._event(run, "cancel_requested", scope="Stop new actions and observe submitted transactions")
                self._save(run)
                self._wake.set()
            return {"run_id": run_id, "status": run["status"]}

    def _reject(self, run, intent, code):
        intent.update(status="rejected", error_code=code, execution_success=False)
        self._failure(run, code)

    def _fixture_save(self, run, state):
        with self._lock:
            run["fixture_state"] = copy.deepcopy(state)
            run["cleanup"] = "required"
            self._save(run)

    def _set_fixture(self, run, cohort, phase):
        fixtures = run["spec"]["fixtures"][phase]
        if not fixtures:
            return
        cohort.apply({f["prefix"]: f["response"] for f in fixtures})
        with self._lock:
            run["fixture_phase"] = phase
            self._event(run, "controlled_inputs_applied", phase=phase)
            self._save(run)

    def _journal_submission(self, run, intent, prepared, *, setup=False):
        if type(prepared) is not dict or not HEX64.fullmatch(prepared.get("tx_id", "")):
            raise StudioError("invalid_prepared_transaction")
        fee = prepared.get("fee_value")
        if type(fee) is not int or fee < 0:
            raise StudioError("invalid_prepared_fee")
        with self._lock:
            intent.update(prepared=ProjectVault(self.data_dir).seal(prepared),
                          status="prepared", tx_id=prepared["tx_id"], fee=fee,
                          target_tx_id=prepared.get("target_tx_id"))
            run["setup_fee_reserved" if setup else "fee_reserved"] += fee
            self._event(run, "submission_prepared", operation=intent["operation"],
                        tx_id=intent["tx_id"], fee_reserved=fee)
            self._save(run)  # Commit signed identity BEFORE any submission.

    def _send(self, run, intent, client):
        prepared = ProjectVault(self.data_dir).open(intent["prepared"])
        with self._lock:
            intent["submission_attempts"] += 1
            intent["status"] = "submitting"
            self._save(run)
        try:
            result = client.submit(prepared)
            tx_id = result.get("tx_id") if type(result) is dict else result
            if tx_id != intent["tx_id"]:
                raise StudioError("submission_identity_mismatch")
        except Exception as exc:
            # Keep the same envelope for reconciliation; no new nonce or signature.
            with self._lock:
                intent["status"] = "uncertain"
                intent["error_code"] = _safe_error(exc)
                self._event(run, "submission_needs_reconciliation", tx_id=intent["tx_id"])
                self._save(run)
            return
        with self._lock:
            intent["status"] = "submitted"
            intent["error_code"] = None
            self._track(run, intent["tx_id"], intent["operation"], intent.get("contract"))
            self._save(run)

    def _track(self, run, tx_id, operation, contract=None, parent_tx_id=None):
        if not HEX64.fullmatch(tx_id) or len(run["transactions"]) >= 256:
            raise StudioError("transaction_tracking_limit")
        run["transactions"].setdefault(tx_id, {
            "tx_id": tx_id, "operation": operation, "contract": contract,
            "parent_tx_id": parent_tx_id, "status": "PENDING", "execution_success": None,
            "result": None, "rounds": [], "round_count": 0,
        })

    def _reconcile(self, run, client):
        for intent in list(run["intents"].values()):
            if intent["status"] not in {"prepared", "submitting", "uncertain"}:
                continue
            try:
                raw = self._transaction(client, intent["tx_id"], intent["operation"])
            except StudioError as exc:
                if exc.code != "transaction_not_found":
                    raise
                if intent["submission_attempts"] >= 3:
                    raise StudioError("submission_outcome_unknown") from None
                self._send(run, intent, client)
            else:
                with self._lock:
                    self._track(run, intent["tx_id"], intent["operation"], intent.get("contract"))
                    intent.update(status="submitted", error_code=None)
                    self._event(run, "submission_reconciled", tx_id=intent["tx_id"])
                    self._save(run)
                self._record_receipt(run, intent["tx_id"], raw)

    @staticmethod
    def _transaction(client, tx_id, operation):
        if operation != "appeal":
            return client.transaction(tx_id)
        envelope = client.envelope(tx_id)
        if envelope is None:
            raise StudioError("transaction_not_found")
        # This is the Studio-recorded EVM submission outcome. It is deliberately
        # separate from the target's later appeal consensus round.
        return {"tx_id": tx_id, "status": "FINALIZED", "raw_result": None,
                "execution_success": envelope["success"], "rounds": [],
                "result_code": envelope.get("error_code"), "envelope_only": True}

    def _record_receipt(self, run, tx_id, raw):
        from .project_bindings import validate_project_result

        if (type(raw) is not dict or raw.get("tx_id") != tx_id or raw.get("status") not in STATUSES
                or raw.get("execution_success") is not None
                and type(raw["execution_success"]) is not bool):
            raise StudioError("malformed_receipt")
        with self._lock:
            previous = run["transactions"][tx_id]
            operation = previous["operation"]
            result = raw.get("raw_result")
            if operation in run["spec"]["project_snapshot"]["definition"]["operations"] and raw.get("execution_success"):
                result = validate_project_result(run["spec"]["project_snapshot"], operation, result)
            rounds = [{"kind": str(r.get("kind", "Unknown"))[:96],
                       "execution_success": r.get("execution_success")}
                      for r in raw.get("rounds", [])[:128]]
            receipt = {**previous, "status": raw["status"], "observed": True,
                       "execution_success": raw.get("execution_success"), "result": result,
                       "rounds": rounds, "round_count": len(rounds),
                       "appealed": raw.get("appealed") is True,
                       "contract_address": raw.get("contract_address"),
                       "envelope_only": raw.get("envelope_only") is True,
                       "fees": copy.deepcopy(raw.get("fees")),
                       "appeal_eligible": raw["status"] in APPEALABLE and not raw.get("appealed"),
                       "result_code": raw.get("result_code")}
            receipt["decision_id"] = _hash({"tx_id": tx_id, "result": result, "rounds": rounds,
                                             "execution_success": receipt["execution_success"]})
            if operation.startswith("$deploy:") and receipt["status"] == "FINALIZED":
                address = receipt["contract_address"]
                if not receipt["execution_success"] or type(address) is not str or not HEX40.fullmatch(address):
                    raise StudioError("deployment_not_successfully_finalized")
                run["contracts"][operation.split(":", 1)[1]] = address
            run["transactions"][tx_id] = receipt
            for intent in run["intents"].values():
                if intent["tx_id"] != tx_id:
                    continue
                intent.update(result=copy.deepcopy(result), decision_id=receipt["decision_id"],
                              execution_success=receipt["execution_success"],
                              transaction_status=receipt["status"], receipt=copy.deepcopy(receipt))
                if receipt["status"] in TX_TERMINAL:
                    intent["status"] = "completed" if receipt["execution_success"] else "failed"
                    if operation == "appeal" and receipt["execution_success"]:
                        intent["status"] = "submitted"
                        intent["execution_success"] = None
                    if not receipt["execution_success"]:
                        intent["error_code"] = "contract_execution_failed"
                else:
                    intent["status"] = "submitted"
            children = raw.get("child_transactions", [])
            if type(children) is not list or len(children) > 128:
                raise StudioError("malformed_child_transactions")
            for child in children:
                child_id = child if type(child) is str else child.get("tx_id")
                if child_id not in run["transactions"]:
                    self._track(run, child_id, "$child", parent_tx_id=tx_id)
                    self._event(run, "child_transaction_discovered", tx_id=child_id, parent_tx_id=tx_id)
            if receipt != previous:
                self._event(run, "transaction_observed", tx_id=tx_id, operation=operation,
                            status=receipt["status"], execution_success=receipt["execution_success"],
                            round_count=len(rounds))
            self._save(run)

    def _poll(self, run, client):
        for tx_id, receipt in list(run["transactions"].items()):
            if receipt["status"] not in TX_TERMINAL:
                self._record_receipt(run, tx_id, self._transaction(client, tx_id, receipt["operation"]))
        # An appeal's EVM submission and appealed GenLayer transaction are distinct.
        # Completion requires observing the target's actual new consensus round.
        with self._lock:
            for intent in run["intents"].values():
                if intent["operation"] != "appeal" or not intent.get("target_tx_id"):
                    continue
                target = run["transactions"].get(intent["target_tx_id"])
                request = run["transactions"].get(intent["tx_id"])
                if not target or not request:
                    continue
                new_rounds = target.get("rounds", [])[intent.get("baseline_rounds", 0):]
                completed = [r for r in new_rounds if "Appeal" in r["kind"]
                             and r["kind"].endswith(("Successful", "Failed"))]
                if completed and request.get("execution_success") is not False:
                    intent.update(status="completed", execution_success=True, error_code=None,
                                  result={"appeal_rounds": completed, "target": copy.deepcopy(target)})
                elif target["status"] == "FINALIZED" and request["status"] in TX_TERMINAL:
                    intent.update(status="failed", execution_success=False,
                                  error_code="appeal_round_not_observed")
            self._save(run)

    def _read_states(self, run, client):
        from .project_bindings import resolve_operation, validate_project_result

        project = run["spec"]["project_snapshot"]
        states = {}
        for alias, operation in project["definition"]["state_reads"].items():
            target = project["definition"]["operations"][operation]["contract"]
            if target not in run["contracts"]:
                continue
            resolved = resolve_operation(project, operation, {}, run["contracts"])
            raw = client.read(resolved["address"], resolved["method"], resolved["args"], finalized=True)
            states[alias] = validate_project_result(project, operation, raw)
        with self._lock:
            if states != run["state"]:
                run["state"] = states
                self._event(run, "finalized_state_observed", state_sha256=_hash(states))
                self._save(run)

    def _latest_decision(self, run, decision_id):
        matched = next((r for r in run["transactions"].values() if r.get("decision_id") == decision_id
                        and not r["operation"].startswith("$") and r["operation"] != "appeal"), None)
        if matched:
            latest = next((r for r in reversed(list(run["transactions"].values()))
                           if r["operation"] == matched["operation"]), None)
            if latest is matched:
                return matched
        return None

    def _policy(self, run, intent, fee=0):
        from .project_scenarios import check_operation_policy

        observation = self._observation(run)
        operations = [copy.deepcopy(i) for i in run["intents"].values() if i is not intent
                      and not i["operation"].startswith("$")]
        return check_operation_policy(run["spec"], intent["operation"], intent["arguments"],
                                      state=run["state"], observation=observation,
                                      operations=operations, fee=fee,
                                      expected_decision_id=intent["expected_decision_id"])

    def _execute(self, run, intent, client, cohort):
        from .project_bindings import resolve_operation, validate_project_result
        from .project_scenarios import read_scenario_evidence

        self._poll(run, client)
        self._read_states(run, client)
        with self._lock:
            if self._policy(run, intent):
                self._reject(run, intent, "operation_policy_violated")
                return
            expected = intent["expected_decision_id"]
            decision = self._latest_decision(run, expected) if expected else None
            if expected and decision is None:
                self._reject(run, intent, "stale_or_unknown_decision")
                return
        operation, arguments = intent["operation"], intent["arguments"]
        if operation == "submit_investigation":
            from .project_investigation import build_investigation

            try:
                result = build_investigation(arguments, intent_key=intent["idempotency_key"],
                    decision=decision, operations=list(run["intents"].values()),
                    declared_evidence=run["spec"]["evidence"])
            except (ValueError, TypeError):
                self._reject(run, intent, "invalid_investigation_submission")
                return
            with self._lock:
                self._complete_read(run, intent, result)
                self._event(run, "investigation_submitted", submission_id=intent["idempotency_key"],
                            decision_id=decision["decision_id"], disposition=result["disposition"])
                self._save(run)
            return
        if operation == "read_evidence":
            if set(arguments) != {"id"}:
                self._reject(run, intent, "invalid_evidence_request")
                return
            try:
                result = read_scenario_evidence(run["spec"], arguments["id"])
            except ValueError:
                self._reject(run, intent, "unknown_evidence")
                return
            self._complete_read(run, intent, result)
            return
        if operation in {"inspect_appeal", "appeal"}:
            if operation == "inspect_appeal":
                decision = self._latest_decision(run, arguments.get("decision_id"))
            if not decision:
                self._reject(run, intent, "stale_or_unknown_decision")
                return
            quote = client.appeal_quote(decision["tx_id"])
            if operation == "inspect_appeal":
                self._complete_read(run, intent, quote)
                return
            if not decision["appeal_eligible"]:
                self._reject(run, intent, "transaction_not_appealable")
                return
            # Keep unrelated pending executions on their original controlled inputs.
            others = [tx for tx in run["transactions"].values() if tx["tx_id"] != decision["tx_id"]
                      and tx["status"] not in TX_TERMINAL]
            if others and run["spec"]["fixtures"]["after_appeal"]:
                self._reject(run, intent, "fixture_change_requires_other_transactions_finalized")
                return
            prepared = client.prepare_appeal(decision["tx_id"], quote=quote)
            if self._policy(run, intent, prepared["fee_value"]):
                self._reject(run, intent, "fee_policy_violated")
                return
            self._set_fixture(run, cohort, "after_appeal")
            intent["baseline_rounds"] = decision["round_count"]
        else:
            inspect = operation == "inspect_fees"
            name = arguments.get("operation") if inspect else operation
            supplied = arguments.get("arguments", {}) if inspect else arguments
            try:
                resolved = resolve_operation(run["spec"]["project_snapshot"], name, supplied, run["contracts"])
            except (ValueError, KeyError):
                self._reject(run, intent, "invalid_operation_arguments")
                return
            if inspect:
                if resolved["readonly"]:
                    self._complete_read(run, intent, {"fee_value": 0, "readonly": True})
                else:
                    self._complete_read(run, intent, client.estimate_write(
                        resolved["address"], resolved["method"], resolved["args"]))
                return
            intent["contract"] = resolved["contract"]
            if resolved["readonly"]:
                result = client.read(resolved["address"], resolved["method"], resolved["args"], finalized=True)
                self._complete_read(run, intent, validate_project_result(run["spec"]["project_snapshot"], name, result))
                return
            prepared = client.prepare_write(resolved["address"], resolved["method"], resolved["args"])
            if self._policy(run, intent, prepared["fee_value"]):
                self._reject(run, intent, "fee_policy_violated")
                return
        self._journal_submission(run, intent, prepared)
        self._send(run, intent, client)

    def _complete_read(self, run, intent, result):
        with self._lock:
            intent.update(status="completed", result=copy.deepcopy(result), execution_success=True)
            self._event(run, "read_completed", operation=intent["operation"])
            self._save(run)

    def _deploy_next(self, run, client):
        from .project_bindings import deployment_order, project_code, resolve_constructor

        snapshot = run["spec"]["project_snapshot"]
        for alias in deployment_order(snapshot):
            if alias in run["contracts"]:
                continue
            key = "$deploy:" + alias
            intent = run["intents"].get(key)
            if intent and intent["status"] != "queued":
                return False
            if intent is None:
                intent = {"operation": key, "arguments": {}, "idempotency_key": key,
                          "status": "queued", "prepared": None, "tx_id": None,
                          "target_tx_id": None, "contract": alias, "submission_attempts": 0,
                          "result": None, "error_code": None, "execution_success": None, "fee": 0}
                with self._lock:
                    run["intents"][key] = intent
                    self._save(run)
            args = resolve_constructor(snapshot, alias, run["contracts"], run["spec"]["context"])
            prepared = client.prepare_deploy(project_code(snapshot, alias), args)
            self._journal_submission(run, intent, prepared, setup=True)
            self._send(run, intent, client)
            return False
        return True

    def _worker(self, run_id):
        run = self._runs[run_id]
        client = cohort = None
        opened = False
        failure = None
        try:
            account = ProjectVault(self.data_dir).open(run["private_account"])
            client = self._client_factory(self.data_dir, account["private_key"])
            provenance = copy.deepcopy(getattr(client, "workflow_provenance", {}))
            previous = run["manifest"].get("backend")
            identity_keys = ("owner", "source_commit", "image_id", "json_fixture_wire", "chain_id", "endpoint")
            if previous and any(previous.get(k) != provenance.get(k) for k in identity_keys):
                raise StudioError("workflow_backend_identity_changed")
            with self._lock:
                run["manifest"]["backend"] = provenance
                self._save(run)
            cohort = self._cohort_factory(self.data_dir, client, run["fixture_state"],
                                           lambda state: self._fixture_save(run, state))
            cohort.__enter__()
            opened = True
            if run.get("cleanup_retry_error"):
                self._verify_deployments(run, client)
                failure = run["cleanup_retry_error"]
                return  # finally reconciles original identities and restores fixtures.
            if run["fixture_phase"] is None:
                self._set_fixture(run, cohort, "initial")
            if not run.get("funded"):
                # Local test-account setup is distinct from agent spending decisions.
                funding = client.ensure_test_balance(10**24)
                with self._lock:
                    run["funded"] = True
                    self._event(run, "local_test_account_ready", funding=funding)
                    self._save(run)
            self._reconcile(run, client)
            self._verify_deployments(run, client)
            while not self._stop.is_set():
                self._reconcile(run, client)
                self._poll(run, client)
                if run["finish_requested"] or run["cancel_requested"]:
                    break
                if time.time() >= run["deadline_at"]:
                    raise StudioError("deadline_exceeded")
                ready = self._deploy_next(run, client)
                if ready:
                    self._read_states(run, client)
                    with self._lock:
                        if run["status"] != "running":
                            run["status"] = "running"
                            self._event(run, "ready")
                            self._save(run)
                        queued = next((i for i in run["intents"].values()
                                       if i["status"] == "queued" and not i["operation"].startswith("$")), None)
                    if queued:
                        self._execute(run, queued, client, cohort)
                self._wake.wait(self._poll_interval)
                self._wake.clear()
        except Exception as exc:
            failure = _safe_error(exc)
            with self._lock:
                run["backend_failures"].append(failure)
                run["error_code"] = failure
                self._event(run, "runtime_error", code=failure)
                self._save(run)
        finally:
            if self._stop.is_set() and not failure:
                if cohort and opened:
                    cohort.abandon()
                with self._lock:
                    run["status"] = "paused"
                    self._event(run, "paused_for_service_restart")
                    self._save(run)
            else:
                self._cleanup(run, client, cohort, opened, failure)
            if client:
                client.close()
            with self._lock:
                self._active_id = None

    @staticmethod
    def _verify_deployments(run, client):
        from .project_bindings import project_code

        for alias, address in run["contracts"].items():
            client.verify_contract(address, project_code(run["spec"]["project_snapshot"], alias))

    def _cleanup(self, run, client, cohort, opened, failure):
        restored = not opened and run["fixture_state"] is None
        with self._lock:
            run["status"] = "closing"
            for intent in run["intents"].values():
                if intent["status"] == "queued":
                    intent.update(status="cancelled", error_code="workflow_ended_before_submission")
            self._save(run)
        if opened:
            try:
                deadline = time.monotonic() + self._cleanup_timeout
                while True:
                    self._reconcile(run, client)
                    self._poll(run, client)
                    unsettled = (any(t["status"] not in TX_TERMINAL for t in run["transactions"].values())
                                 or any(i["status"] in {"prepared", "submitting", "uncertain", "submitted"}
                                        for i in run["intents"].values()))
                    if not unsettled:
                        break
                    if time.monotonic() >= deadline:
                        raise StudioError("cleanup_deadline_exceeded")
                    self._wake.wait(self._poll_interval)
                    self._wake.clear()
                self._read_states(run, client)
                balance = client.balance()
                with self._lock:
                    run["final_balance"] = balance
                    self._save(run)
                cohort.close()
                restored = True
            except Exception as exc:
                failure = failure or _safe_error(exc)
                cohort.abandon()
        with self._lock:
            run["cleanup"] = "restored" if restored else "unresolved"
            run["status"] = ("inconclusive" if failure or not restored else
                             "cancelled" if run["cancel_requested"] else "completed")
            run["error_code"] = failure or (None if restored else "fixture_cleanup_unresolved")
            self._event(run, "ended", status=run["status"], cleanup=run["cleanup"])
            self._save(run)

    def report(self, run_id):
        from .project_scenarios import evaluate_project_report

        with self._lock:
            run = self._runs[run_id]
            observation = self._observation(run)
            observation["operations"] = [self._public_intent(i) for i in run["intents"].values()
                                         if not i["operation"].startswith("$")]
            checks = evaluate_project_report(run["spec"], observation)
            checks.append({"id": "behavior", "label": "Agent respected allowed actions",
                           "outcome": "fail" if run["behavior_failures"] else "pass",
                           "detail": list(run["behavior_failures"])})
            checks.append({"id": "explicit_completion", "label": "Agent completed the task",
                           "outcome": "pass" if run["finish_requested"] else "fail", "detail": None})
            unknown_execution = [tx["tx_id"] for tx in run["transactions"].values()
                                 if tx["status"] == "FINALIZED" and tx["execution_success"] is None]
            checks.append({"id": "execution_evidence", "label": "Finalized execution outcomes are known",
                           "outcome": "inconclusive" if unknown_execution else "pass",
                           "detail": unknown_execution})
            children = [t for t in run["transactions"].values() if t.get("parent_tx_id")]
            checks.append({"id": "child_effects", "label": "Dependent transaction outcomes are known",
                           "outcome": "pass" if all(t["status"] == "CANCELED"
                                                     or t["status"] == "FINALIZED"
                                                     and t["execution_success"] is not None
                                                     for t in children) else "inconclusive",
                           "detail": len(children)})
            inconclusive = (run["status"] != "completed" or run["cleanup"] != "restored"
                            or any(c["outcome"] == "inconclusive" for c in checks))
            return {**self._observation(run), "title": run["spec"]["title"], "checks": checks,
                    "verification": "inconclusive" if inconclusive else
                    "pass" if all(c["outcome"] == "pass" for c in checks) else "fail",
                    "events": copy.deepcopy(run["events"]), "manifest": copy.deepcopy(run["manifest"]),
                    "expectations": copy.deepcopy(run["spec"]["expectations"]),
                    "cleanup": run["cleanup"], "final_balance": run.get("final_balance"),
                    "setup_fee_reserved": run["setup_fee_reserved"],
                    "fee_accounting_note": "Reserved deposits are distinct from observed settled fees and refunds",
                    "signer_persistence": "encrypted_local_journal",
                    "recovery_scope": "Lab interruption with intact Studio state"}

    def close(self):
        self._stop.set()
        self._wake.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(50)
            if self._thread.is_alive():
                raise RuntimeError("Project worker has not paused; keep its database open")
