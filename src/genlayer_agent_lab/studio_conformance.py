"""Observed contract conformance on an independently owned local Studio stack.

This workflow never configures validators, resets a network, changes finality,
or reads account keys. The caller must establish stack ownership before using
its endpoint. Contract returns and raw RPC receipts are never exported.
"""

from __future__ import annotations

import copy
import math
import re
import time

from .bindings import bounded_json, extract_verdict, validate_snapshot
from .runtime.pins import GENVM_VERSION, RUNNER_HASH
from .runtime.studio import (
    CAPABILITIES,
    ROUND_NAMES,
    SDK_VERSION,
    STATUSES,
    STUDIO_COMMIT,
    STUDIO_VERSION,
    VOTES,
    StudioClient,
    StudioError,
)

CONTEXT_FIELDS = {"evidence", "resource_id", "policy_version", "amount", "fixture_verdict"}
SAFE_ERRORS = {
    "endpoint_must_be_literal_loopback_http", "invalid_timeout", "sdk_version_mismatch",
    "deadline_exceeded", "canceled", "rpc_http_error", "rpc_timeout", "rpc_transport_error",
    "rpc_response_too_large", "malformed_rpc_response", "rpc_method_unavailable", "rpc_rejected",
    "chain_id_mismatch", "consensus_abi_mismatch", "invalid_finality_window", "invalid_sim_config",
    "invalid_transaction_id", "invalid_contract_address", "public_write_method_required",
    "transaction_not_found", "receipt_identity_mismatch", "unknown_transaction_status",
    "normal_consensus_required", "malformed_receipt", "malformed_execution_result",
    "malformed_contract_address", "sdk_operation_failed", "transaction_not_appealable",
    "appeal_transaction_id_mismatch", "appeal_not_observed_before_finalization",
    "malformed_deployed_source", "deployed_source_mismatch", "client_busy", "client_closed",
}


class _ConformanceError(RuntimeError):
    def __init__(self, code: str, *, failed: bool = False):
        self.code, self.failed = code, failed
        super().__init__(code)


def _hex(value, size: int) -> str:
    if type(value) is not str or re.fullmatch(r"0x[0-9a-fA-F]{" + str(size) + r"}", value) is None:
        raise _ConformanceError("malformed_observed_identifier")
    return value


def _count(value):
    return value if type(value) is int and 0 <= value <= 2**63 - 1 else None


def _safe_rounds(value) -> list[dict]:
    if type(value) is not list or len(value) > 128:
        raise _ConformanceError("observed_rounds_invalid_or_excessive")
    result = []
    for index, item in enumerate(value):
        if type(item) is not dict:
            raise _ConformanceError("malformed_observed_round")
        votes = item.get("validator_votes", [])
        if type(votes) is not list or len(votes) > 256:
            raise _ConformanceError("observed_votes_invalid_or_excessive")
        result.append({"index": index, "kind": item.get("kind") if item.get("kind") in ROUND_NAMES
                       else "Unknown", "validator_votes": [vote if vote in VOTES else "unknown"
                                                            for vote in votes]})
    return result


def _receipt(value: dict, tx_id: str) -> dict:
    if type(value) is not dict or value.get("tx_id") != tx_id or value.get("status") not in STATUSES:
        raise _ConformanceError("malformed_observed_transaction")
    rounds = _safe_rounds(value.get("rounds", []))
    votes = value.get("votes", [])
    if type(votes) is not list or len(votes) > 256:
        raise _ConformanceError("observed_votes_invalid_or_excessive")
    address = value.get("contract_address")
    if address is not None:
        address = _hex(address, 40)
    success = value.get("execution_success")
    result_code = value.get("result_code")
    return {"tx_id": tx_id, "contract_address": address, "status": value["status"],
            "execution_success": success if type(success) is bool else None,
            "result_code": result_code if result_code in {
                "execution_error", "return", "rollback", "contract_error", "error", "none", "no_leaders"
            } else None,
            "votes": [vote if vote in VOTES else "unknown" for vote in votes],
            "rounds": rounds, "round_count": len(rounds), "appealed": value.get("appealed") is True,
            "timestamp_appeal": _count(value.get("timestamp_appeal")),
            "appeal_failed": _count(value.get("appeal_failed")),
            "appeal_processing_time": _count(value.get("appeal_processing_time"))}


def _owner_pins(pins: dict | None, endpoint: str) -> dict:
    result = {"provenance": "owner_reported", "rpc_release_verified": False}
    if pins is None:
        result["provided"] = False
        return result
    if type(pins) is not dict:
        raise _ConformanceError("invalid_stack_pins")
    if "endpoint" in pins and pins["endpoint"] != endpoint:
        raise _ConformanceError("owner_endpoint_mismatch")
    checks = {"source_commit": r"[0-9a-f]{40}", "image_id": r"sha256:[0-9a-f]{64}",
              "project": r"[a-z0-9][a-z0-9_-]{0,95}"}
    for key, pattern in checks.items():
        if key in pins:
            if type(pins[key]) is not str or re.fullmatch(pattern, pins[key]) is None:
                raise _ConformanceError("invalid_stack_pins")
            result[key] = pins[key]
    if "network_internal" in pins:
        if type(pins["network_internal"]) is not bool:
            raise _ConformanceError("invalid_stack_pins")
        result["network_internal"] = pins["network_internal"]
    result["provided"] = True
    return result


def run_studio_conformance(endpoint: str, snapshot: dict, context: dict, sim_config=None,
                           appeal: bool = False, timeout: float = 180, *,
                           stack_pins: dict | None = None, cancel_event=None,
                           expected_verdict: str | None = None) -> dict:
    """Deploy, execute and optionally appeal using actual observed local Studio receipts.

    The total deadline covers all operations. Studio submissions are never retried
    blindly or canceled by this helper. An inconclusive result retains known IDs
    for reconciliation, since a submitted transaction may still complete.
    The fixture response is never treated as an independent expected decision.
    Only an explicitly supplied expected_verdict adds a decision assertion.
    """
    started = time.monotonic()
    evidence = {
        "schema_version": 1, **CAPABILITIES, "verification": "inconclusive",
        "lifecycle": "Observed local Studio checkpoints; not scripted agent scenario events",
        "expected_pins": {"studio_version": STUDIO_VERSION, "studio_commit": STUDIO_COMMIT,
                          "sdk_version": SDK_VERSION, "genvm_version": GENVM_VERSION,
                          "runner_hash": RUNNER_HASH},
        "transactions": {}, "observations": [], "accepted_observed": False,
        "appeal": {"requested": appeal is True, "request_observed": False,
                   "completed": False, "completed_rounds": []},
        "verdict": None,
    }
    try:
        if (type(timeout) not in (int, float) or not math.isfinite(timeout) or not 0 < timeout <= 3600
                or type(appeal) is not bool or expected_verdict not in {None, "approve", "deny"}):
            raise _ConformanceError("invalid_conformance_options")
        checked = validate_snapshot(snapshot)
        bounded_json(context)
        if (type(context) is not dict or not CONTEXT_FIELDS <= context.keys()
                or context["fixture_verdict"] not in {"approve", "deny"}):
            raise _ConformanceError("invalid_conformance_context")
        clean_context = {key: copy.deepcopy(context[key]) for key in CONTEXT_FIELDS}
        definition = checked["definition"]
        evidence["binding"] = {"id": definition["id"], "method": definition["method"],
                               "source_sha256": checked["source_sha256"],
                               "binding_sha256": checked["binding_sha256"]}
        evidence["stack_pins"] = _owner_pins(stack_pins, endpoint)
        evidence["expected_verdict"] = expected_verdict
        deadline = started + timeout

        with StudioClient(endpoint, timeout=timeout, cancel_event=cancel_event) as client:
            def call(method, *args, **kwargs):
                # Coarse clocks may return the same sample twice, while float
                # addition/subtraction can round just above the original budget.
                remaining = min(timeout, deadline - time.monotonic())
                if remaining <= 0:
                    raise _ConformanceError("deadline_exceeded")
                client.timeout = remaining
                return method(*args, **kwargs)

            def observe(phase, transaction, tx_id):
                safe = _receipt(transaction, tx_id)
                evidence["observations"].append({"checkpoint": phase,
                    "elapsed_seconds": round(time.monotonic() - started, 3), **safe})
                return safe

            doctor = call(client.doctor)
            if doctor.get("ready") is not True:
                code = doctor.get("error_code")
                raise _ConformanceError(code if code in SAFE_ERRORS else "studio_preflight_failed")
            evidence["rpc"] = {"compatible": doctor.get("rpc_compatible") is True,
                               "chain_id": _count(doctor.get("chain_id")),
                               "finality_window_seconds": _count(doctor.get("finality_window_seconds")),
                               "release_verified": False}
            deployment = _hex(call(client.deploy, checked, sim_config=sim_config), 64)
            evidence["transactions"]["deployment"] = deployment
            observe("deployment_submitted", call(client.transaction, deployment), deployment)
            deployed = call(client.wait, deployment, until="finalized")
            deployed_safe = observe("deployment_finalization_result", deployed, deployment)
            if deployed_safe["status"] != "FINALIZED" or deployed_safe["execution_success"] is not True:
                raise _ConformanceError("deployment_not_successfully_finalized", failed=True)
            address = _hex(deployed_safe["contract_address"], 40)
            evidence["contract_address"] = address
            execution = _hex(call(client.write, address, checked, clean_context, sim_config=sim_config), 64)
            evidence["transactions"]["execution"] = execution
            observe("execution_submitted", call(client.transaction, execution), execution)
            accepted = call(client.wait, execution, until="accepted")
            accepted_safe = observe("execution_decided", accepted, execution)
            evidence["accepted_observed"] = accepted_safe["status"] == "ACCEPTED"
            baseline_rounds = accepted_safe["round_count"]
            if appeal:
                if accepted_safe["status"] in {"FINALIZED", "CANCELED"}:
                    raise _ConformanceError("appeal_window_missed")
                appealed = call(client.appeal, execution)
                evidence["appeal"]["request_observed"] = appealed.get("request_observed") is True
                if not evidence["appeal"]["request_observed"]:
                    raise _ConformanceError("appeal_request_not_observed")
                observe("appeal_request_observed", appealed["transaction"], execution)
            finalized = call(client.wait, execution, until="finalized")
            final_safe = observe("execution_finalization_result", finalized, execution)
            if appeal:
                completed = [item for item in final_safe["rounds"][baseline_rounds:]
                             if "Appeal" in item["kind"] and item["kind"].endswith(("Successful", "Failed"))]
                evidence["appeal"]["completed"] = bool(completed)
                evidence["appeal"]["completed_rounds"] = completed
                if not completed:
                    raise _ConformanceError("appeal_completion_not_observed")
            if final_safe["status"] != "FINALIZED" or final_safe["execution_success"] is not True:
                raise _ConformanceError("execution_not_successfully_finalized", failed=True)
            try:
                evidence["verdict"] = extract_verdict(finalized.get("raw_result"), definition)
            except ValueError:
                raise _ConformanceError("unmapped_contract_result", failed=True) from None
            if expected_verdict is not None and evidence["verdict"] != expected_verdict:
                raise _ConformanceError("unexpected_contract_verdict", failed=True)
            evidence["verification"] = "pass"
    except _ConformanceError as exc:
        evidence["verification"] = "fail" if exc.failed else "inconclusive"
        evidence["error_code"] = exc.code
    except StudioError as exc:
        evidence["error_code"] = exc.code if exc.code in SAFE_ERRORS else "studio_operation_failed"
    except (KeyError, TypeError, ValueError, RuntimeError, OSError):
        evidence["error_code"] = "invalid_input_or_studio_operation_failed"
    evidence["elapsed_seconds"] = round(time.monotonic() - started, 3)
    evidence["submission_may_still_complete"] = bool(
        evidence["transactions"] and evidence["verification"] == "inconclusive"
    )
    return evidence
