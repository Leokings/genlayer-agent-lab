"""Explicit, quiescent Studio restart verification; never a backup or host reboot.

The Lab and this installation's lifecycle remain locked throughout the drill.
Other direct Studio clients must be paused by the operator. No resource is deleted
except the owned Compose containers/networks; named volumes are always retained.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import time
from pathlib import Path

from .bindings import bounded_json, extract_verdict
from .recovery import _exclusive_lock, _path
from .runtime import studio_stack as stack
from .runtime.studio import StudioClient, StudioError
from .runtime.studio_evaluator import bundled_snapshot
from .runtime.studio_fixtures import virtual_validators
from .studio_conformance import SAFE_ERRORS, _hex, _receipt, run_studio_conformance


class _Failure(RuntimeError):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


OBSERVED_FAILURES = {
    "persistent_volume_changed", "persistent_volume_unavailable", "runtime_identity_changed", "containers_not_recreated",
    "contract_state_changed", "finalized_receipt_changed", "post_restart_execution_failed",
    "new_contract_state_not_persisted", "transaction_not_successfully_finalized",
    "owned_stack_not_stopped",
}


def _remaining(deadline, maximum=3600):
    left = min(maximum, deadline - time.monotonic())
    if left <= 0:
        raise _Failure("deadline_exceeded")
    return left


def _digest(value):
    bounded_json(value)
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                     ensure_ascii=True).encode()).hexdigest()


def _identities(state, inventory):
    project = "gl-agent-lab-" + state["owner"]
    containers = {}
    for item in inventory["containers"]:
        identifier = item.get("Id", "")
        if not re.fullmatch(r"[0-9a-f]{64}", identifier):
            raise _Failure("container_identity_invalid")
        containers[item["Config"]["Labels"][stack.SERVICE_LABEL]] = identifier
    volumes = {}
    for name in ("database", "vm-cache"):
        volume = inventory["volumes"].get(name)
        if (not isinstance(volume, dict) or volume.get("Name") != project + "_" + name
                or not isinstance(volume.get("CreatedAt"), str)
                or not re.fullmatch(r"[0-9TZ:.+\-]{10,64}", volume["CreatedAt"])):
            raise _Failure("volume_identity_invalid")
        volumes[name] = {"name": volume["Name"], "created_at": volume["CreatedAt"]}
    return {"project": project, "image_id": state["image_id"],
            "source_commit": state["source_commit"],
            "configuration_sha256": _digest(stack.compose_config(state)),
            "containers": containers, "volumes": volumes}


def _inspect(endpoint, state, deadline, *, ready=True):
    inventory = stack._inventory(endpoint, state, deadline=deadline)
    stack._assert_owned(inventory, state)
    if ready and not stack._verify_runtime(endpoint, state, inventory, deadline=deadline):
        raise _Failure("owned_runtime_not_verified")
    return inventory


def _sql(endpoint, inventory, query, deadline):
    postgres = [item for item in inventory["containers"]
                if item["Config"]["Labels"][stack.SERVICE_LABEL] == "postgres"]
    if len(postgres) != 1 or not re.fullmatch(r"[0-9a-f]{64}", postgres[0]["Id"]):
        raise _Failure("postgres_identity_invalid")
    # Always read-only, no psql startup file, no shell, and no raw output in evidence.
    result = stack._command(endpoint, ["exec", "--env",
        "PGOPTIONS=-c statement_timeout=5000 -c default_transaction_read_only=on",
        postgres[0]["Id"], "psql", "--no-psqlrc", "--username=postgres",
        "--dbname=genlayer_state", "--tuples-only", "--no-align", "--set=ON_ERROR_STOP=1",
        "--command", query], timeout=_remaining(deadline, 10), output_limit=4096)
    if result.returncode:
        raise _Failure("database_observation_failed")
    return result.stdout.decode("ascii").strip()


def _quiescent(endpoint, inventory, deadline):
    value = _sql(endpoint, inventory,
        "SELECT CASE WHEN EXISTS (SELECT 1 FROM transactions WHERE status NOT IN "
        "('FINALIZED', 'CANCELED')) THEN 1 ELSE 0 END;", deadline)
    if value != "0":
        raise _Failure("pending_studio_transactions" if value == "1"
                       else "database_observation_invalid")


def _state_digest(endpoint, inventory, address, deadline):
    address = _hex(address, 40)
    value = _sql(endpoint, inventory,
        "SELECT encode(sha256(convert_to((data->'state'->'finalized')::text, 'UTF8')), 'hex') "
        "FROM current_state WHERE lower(id) = lower('" + address + "') "
        "AND jsonb_typeof(data->'state'->'finalized') = 'object' "
        "AND data->'state'->'finalized' <> '{}'::jsonb;", deadline)
    if not re.fullmatch(r"[0-9a-f]{64}", value):
        raise _Failure("contract_state_unavailable")
    return value


def _call(client, deadline, method, *args, **kwargs):
    client.timeout = _remaining(deadline)
    return method(*args, **kwargs)


def _final_receipt(client, tx_id, deadline):
    observed = _call(client, deadline, client.transaction, tx_id)
    safe = _receipt(observed, tx_id)
    if safe["status"] != "FINALIZED" or safe["execution_success"] is not True:
        raise _Failure("transaction_not_successfully_finalized")
    return {**safe, "result_sha256": _digest(observed.get("raw_result"))}


def _require_volumes(state, inventory, expected_volumes):
    try:
        volumes = _identities(state, inventory)["volumes"]
    except _Failure as exc:
        if exc.code == "volume_identity_invalid":
            raise _Failure("persistent_volume_unavailable") from None
        raise
    if volumes != expected_volumes:
        raise _Failure("persistent_volume_changed")


def _start(data_dir, endpoint, state, deadline, expected_volumes):
    inventory = _inspect(endpoint, state, deadline, ready=False)
    # Compose creates missing named volumes. Never allow an error-recovery
    # attempt to replace a missing original database with a fresh empty one.
    _require_volumes(state, inventory, expected_volumes)
    left = _remaining(deadline, 330)
    stack._compose(data_dir, ["up", "--detach", "--wait", "--wait-timeout",
                            str(max(1, int(min(300, left))))], timeout=left, endpoint=endpoint)
    inventory = _inspect(endpoint, state, deadline)
    with StudioClient(f"http://127.0.0.1:{state['port']}", timeout=_remaining(deadline)) as client:
        if _call(client, deadline, client.doctor).get("ready") is not True:
            raise _Failure("rpc_not_ready")
    return inventory


def run_studio_recovery(data_dir, timeout=600, progress=None):
    """Verify preserved finalized state and new work after a controlled restart.

    Timeout includes a reserve (up to 90 seconds) for a best-effort restart if
    the drill fails after shutdown starts. Pending transactions are never reset.
    A failed cleanup is reported. Missing/replaced storage requires restoring the
    original volumes; other startup failures may be retried with ``studio up``.
    """
    started = time.monotonic()
    evidence = {"schema_version": 1, "verification": "inconclusive",
                "scope": "Controlled owned Studio container restart with preserved named volumes",
                "host_reboot": False, "disaster_recovery": False,
                "pending_transaction_recovery": False, "live_models": False,
                "public_chain": False, "stage": "validation", "checks": {},
                "transactions": {}, "cleanup": {"attempted": False}}
    stop_attempted = False
    state = endpoint = None

    def stage(name):
        evidence["stage"] = name
        if progress:
            progress("Studio recovery verification: " + name.replace("_", " ") + "...")

    try:
        if type(timeout) not in (int, float) or not math.isfinite(timeout) or not 0 < timeout <= 3600:
            raise _Failure("invalid_timeout")
        reserve = min(90.0, timeout / 4)
        evidence["timeout_seconds"] = timeout
        evidence["cleanup_reserve_seconds"] = reserve
        deadline = started + timeout
        work_deadline = deadline - reserve
        root = _path(Path(data_dir))
        if not root.is_dir():
            raise _Failure("data_directory_missing")
        stage("lab_lock")
        with _exclusive_lock(root):
            stage("studio_lock")
            with stack._operation_lock(root):
                try:
                    stage("preflight")
                    state = stack._load(stack._root(root))
                    endpoint = stack._endpoint(timeout=_remaining(work_deadline, 8))
                    inventory = _inspect(endpoint, state, work_deadline)
                    before = _identities(state, inventory)
                    _quiescent(endpoint, inventory, work_deadline)
                    evidence["checks"]["initially_quiescent"] = True
                    rpc_endpoint = f"http://127.0.0.1:{state['port']}"
                    snapshot = bundled_snapshot()
                    definition = snapshot["definition"]
                    context = {"evidence": "A signed delivery receipt confirms delivery.",
                        "resource_id": "studio-recovery-drill", "policy_version": "v1",
                        "amount": 1, "fixture_verdict": "approve"}
                    def fixtures(verdict):
                        return {"validators": virtual_validators(verdict,
                            definition["llm_pattern"], definition["llm_response"], count=5)}
                    stage("baseline_execution")
                    baseline = run_studio_conformance(rpc_endpoint, snapshot, context,
                        sim_config=fixtures("approve"), expected_verdict="approve",
                        timeout=_remaining(work_deadline), stack_pins={**before, "endpoint": rpc_endpoint})
                    for name in ("deployment", "execution"):
                        if name in baseline.get("transactions", {}):
                            evidence["transactions"][name] = _hex(baseline["transactions"][name], 64)
                    if baseline.get("verification") != "pass":
                        raise _Failure("baseline_execution_failed")
                    address = _hex(baseline["contract_address"], 40)
                    evidence["contract_address"] = address
                    with StudioClient(rpc_endpoint, timeout=_remaining(work_deadline)) as client:
                        receipts = {name: _final_receipt(client, identifier, work_deadline)
                                    for name, identifier in evidence["transactions"].items()}
                    inventory = _inspect(endpoint, state, work_deadline)
                    if _identities(state, inventory) != before:
                        raise _Failure("runtime_changed_before_restart")
                    _quiescent(endpoint, inventory, work_deadline)
                    state_hash = _state_digest(endpoint, inventory, address, work_deadline)
                    evidence["before"] = {**before, "receipts": receipts,
                                          "contract_state_sha256": state_hash}
                    evidence["checks"]["quiescent_before_shutdown"] = True
                    stage("stopping")
                    stop_attempted = True
                    stack._compose(root, ["down", "--timeout", "15"],
                                   timeout=_remaining(work_deadline, 45), endpoint=endpoint)
                    stopped = _inspect(endpoint, state, work_deadline, ready=False)
                    if stopped["containers"] or stopped["network"] or stopped.get("access_network"):
                        raise _Failure("owned_stack_not_stopped")
                    _require_volumes(state, stopped, before["volumes"])
                    evidence["checks"]["stopped_with_volumes_preserved"] = True
                    stage("restarting")
                    inventory = _start(root, endpoint, state, work_deadline, before["volumes"])
                    after = _identities(stack._load(stack._root(root)), inventory)
                    for key in ("project", "image_id", "source_commit", "configuration_sha256", "volumes"):
                        if after[key] != before[key]:
                            raise _Failure("runtime_identity_changed")
                    if set(after["containers"]) != set(before["containers"]) or any(
                            identifier == before["containers"][name]
                            for name, identifier in after["containers"].items()):
                        raise _Failure("containers_not_recreated")
                    if _state_digest(endpoint, inventory, address, work_deadline) != state_hash:
                        raise _Failure("contract_state_changed")
                    evidence["after"] = after
                    evidence["checks"]["runtime_and_contract_state_preserved"] = True
                    stage("receipt_reconciliation")
                    with StudioClient(rpc_endpoint, timeout=_remaining(work_deadline)) as client:
                        for name, receipt in receipts.items():
                            if _final_receipt(client, receipt["tx_id"], work_deadline) != receipt:
                                raise _Failure("finalized_receipt_changed")
                        evidence["checks"]["finalized_receipts_preserved"] = True
                        stage("new_execution")
                        context["fixture_verdict"] = "deny"
                        context["evidence"] = "The delivery receipt cannot be authenticated."
                        tx_id = _hex(_call(client, work_deadline, client.write, address, snapshot,
                                          context, sim_config=fixtures("deny")), 64)
                        evidence["transactions"]["post_restart_execution"] = tx_id
                        observed = _call(client, work_deadline, client.wait, tx_id, until="finalized")
                        safe = _receipt(observed, tx_id)
                        try:
                            verdict = extract_verdict(observed.get("raw_result"), definition)
                        except (ValueError, TypeError, KeyError):
                            raise _Failure("post_restart_execution_failed") from None
                        if (safe["status"] != "FINALIZED" or safe["execution_success"] is not True
                                or safe["contract_address"] != address
                                or verdict != "deny"):
                            raise _Failure("post_restart_execution_failed")
                        evidence["post_restart_receipt"] = {**safe,
                            "result_sha256": _digest(observed.get("raw_result")), "verdict": "deny"}
                    inventory = _inspect(endpoint, state, work_deadline)
                    _quiescent(endpoint, inventory, work_deadline)
                    if _state_digest(endpoint, inventory, address, work_deadline) == state_hash:
                        raise _Failure("new_contract_state_not_persisted")
                    evidence["checks"]["new_write_on_preserved_contract"] = True
                    stage("complete")
                    evidence["verification"] = "pass"
                finally:
                    if stop_attempted and evidence["verification"] != "pass":
                        evidence["cleanup"] = {"attempted": True, "ready": False}
                        try:
                            _start(root, endpoint, state, deadline, before["volumes"])
                            evidence["cleanup"]["ready"] = True
                        except _Failure as exc:
                            evidence["cleanup"]["error_code"] = exc.code
                        except (RuntimeError, ValueError, TypeError, KeyError, OSError):
                            evidence["cleanup"]["error_code"] = "studio_restart_failed"
    except _Failure as exc:
        evidence["error_code"] = exc.code
        if exc.code in OBSERVED_FAILURES:
            evidence["verification"] = "fail"
    except StudioError as exc:
        evidence["error_code"] = exc.code if exc.code in SAFE_ERRORS else "studio_operation_failed"
    except (RuntimeError, ValueError, TypeError, KeyError, OSError):
        evidence["error_code"] = {"lab_lock": "lab_must_be_stopped",
            "studio_lock": "studio_lifecycle_busy"}.get(evidence["stage"], "studio_recovery_operation_failed")
    evidence["elapsed_seconds"] = round(time.monotonic() - started, 3)
    evidence["submission_may_still_complete"] = bool(
        evidence["transactions"] and evidence["verification"] != "pass")
    return evidence
