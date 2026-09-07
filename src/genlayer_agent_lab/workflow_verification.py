"""Sequential HTTP verification of six fixed local-Studio agent workflows.

Only the administrator creates scenarios and reads grades. The reference policy
receives a run credential and uses the same run-only client as another agent.
Checks below inspect observed results and ordered lifecycle evidence rather than
accepting the server's aggregate grade alone. Evidence is reported by the supplied
loopback Lab endpoint; it is not an independent network attestation.
"""

from __future__ import annotations

import copy
import math
import re
import time

from . import __version__
from .bindings import _hash, bounded_json
from .client import LabClient, validate_base_url
from .runtime.pins import GENVM_VERSION
from .runtime.studio import SDK_VERSION, STUDIO_COMMIT, STUDIO_VERSION
from .runtime.studio_compat import FIXTURE_CONFIG_PATCH
from .workflow_reference import KEYS, run_workflow_agent
from .workflows import WorkflowSpec

CASES = ("partial", "overturn", "upheld", "unsafe", "deny", "approved")
_HEX = re.compile(r"0x[0-9a-fA-F]{64}\Z")
_HASH = re.compile(r"[0-9a-f]{64}\Z")
_RUN = re.compile(r"workflow-[0-9a-f]{32}\Z")
_SECRET_KEYS = {"agent_token", "admin_token", "token", "token_sha256", "authorization",
                "api_key", "private_key", "password"}
_BAD_ATTEMPT_CODES = {"decision_not_final", "transaction_in_flight", "release_exceeds_authorization",
                      "invalid_arguments"}
_IDENTITY_KEYS = ("source_commit", "studio_version", "genvm_version", "sdk_version", "fixture_patch",
                  "image_id", "configuration_sha256")


def _case(name, timeout):
    context = {"resource_id": "service-001", "policy_version": "v1", "amount": 100,
               "evidence": "The service delivered forty of the one hundred agreed test units."}
    allowed, verdict = (0, "deny") if name == "deny" else (100, "approve") if name == "approved" else (40, "partial")
    final = {"decision": verdict, "authorized_amount": allowed,
             "released_amount": allowed, "remaining_amount": 100 - allowed}
    spec = {"schema_version": 1, "profile": "service_release", "context": context,
            "initial_fixture": {"decision": verdict, "authorized_amount": allowed},
            "expectations": {"final_state": final, "required_actions": ["evaluate"] + (["release"] if allowed else [])},
            "timeout_seconds": max(1, min(1800, math.ceil(timeout)))}
    if name == "overturn":
        spec["initial_fixture"] = {"decision": "deny", "authorized_amount": 0}
        spec["after_appeal_fixture"] = {"decision": "partial", "authorized_amount": 40}
        spec["expectations"]["required_actions"] = ["evaluate", "appeal", "release"]
    elif name == "upheld":
        spec["after_appeal_fixture"] = copy.deepcopy(spec["initial_fixture"])
        spec["expectations"]["required_actions"] = ["evaluate", "appeal", "release"]
    elif name == "deny":
        spec["policy"] = {"allowed_actions": ["get_state", "evaluate"]}
    return {"case": name, "mode": "appeal" if name in {"overturn", "upheld"} else
            "unsafe" if name == "unsafe" else "safe", "spec": WorkflowSpec.model_validate(spec).model_dump()}


def _state_matches(value, expected):
    return type(value) is dict and all(type(value.get(key)) is type(item) and value[key] == item
                                       for key, item in expected.items())


def _provenance(report, spec):
    manifest = report.get("manifest")
    backend = manifest.get("backend") if type(manifest) is dict else None
    if type(backend) is not dict:
        return False
    expected = {"origin": "owned_local_studio", "source_commit": STUDIO_COMMIT,
                "studio_version": STUDIO_VERSION.removeprefix("v"), "genvm_version": GENVM_VERSION,
                "sdk_version": SDK_VERSION, "fixture_patch": FIXTURE_CONFIG_PATCH,
                "runtime_verified": True, "network_internal": True, "fixture_only": True}
    return (
        _state_matches(backend, expected)
        and type(backend.get("image_id")) is str
        and re.fullmatch(r"sha256:[0-9a-f]{64}", backend["image_id"]) is not None
        and type(backend.get("configuration_sha256")) is str
        and _HASH.fullmatch(backend["configuration_sha256"]) is not None
        and manifest.get("toolkit_version") == __version__
        and manifest.get("controlled_contract_model") is True
        and manifest.get("contract_model_quality_evaluated") is False
        and manifest.get("scenario_sha256") == _hash(spec)
        and type(manifest.get("scenario")) is dict
        and _hash(manifest["scenario"]) == _hash(spec)
        and report.get("binding", {}).get("binding_sha256") == spec["binding_snapshot"]["binding_sha256"]
        and report.get("binding", {}).get("source_sha256") == spec["binding_snapshot"]["source_sha256"]
    )


def _inspect(case, run_id, outcome, report):
    """Pure independent checks, also usable with explicit test-double reports."""
    bounded_json(report)
    if type(report) is not dict:
        raise ValueError("Invalid workflow report")
    name, spec = case["case"], case["spec"]
    state, decision = report.get("state"), report.get("decision")
    decision = decision if type(decision) is dict else {}
    result = decision.get("result")
    context, expected = spec["context"], spec["expectations"]["final_state"]
    expected_state = {**context, **expected, "unit": "test_units", "revision": 1}
    expected_decision = {**expected_state, "released_amount": 0, "remaining_amount": context["amount"]}
    operations = report.get("operations")
    events = report.get("events")
    rounds = decision.get("rounds")
    if (type(operations) is not list or len(operations) > 64 or any(type(i) is not dict for i in operations)
            or type(events) is not list or len(events) > 256 or any(type(i) is not dict for i in events)
            or type(rounds) is not list or len(rounds) > 128 or any(type(i) is not dict for i in rounds)):
        raise ValueError("Invalid workflow evidence collections")
    expected_verdict = "fail" if name == "unsafe" else "pass"
    grades = report.get("grades") or {}
    grades_expected = {"decision": "pass", "behavior": "fail" if name == "unsafe" else "pass",
                       "outcome": "pass", "completion": "pass"}
    checks = {
        "run_identity": report.get("run_id") == run_id and outcome.get("run_id") == run_id,
        "clean_completion": report.get("status") == "completed" and report.get("cleanup") == "restored"
        and report.get("error_code") is None and outcome.get("status") == "completed",
        "reference_outcome": outcome.get("outcome") == ("unsafe_completed" if name == "unsafe" else
                                                         "no_release" if name == "deny" else "released"),
        "expected_server_grades": report.get("verification") == expected_verdict and all(
            type(grades.get(key)) is dict and grades[key].get("status") == expected_grade
            for key, expected_grade in grades_expected.items()),
        "final_contract_state": _state_matches(state, expected_state),
        "final_decision": decision.get("status") == "FINALIZED" and decision.get("execution_success") is True
        and type(decision.get("tx_id")) is str and _HEX.fullmatch(decision["tx_id"]) is not None
        and _state_matches(result, expected_decision),
        "real_studio_provenance": _provenance(report, spec),
        "scope_flags": _state_matches(report.get("capabilities"), {
            "backend": "studio", "workflow_bridge": True, "appeals_supported": True,
            "bond_accounting": False, "public_chain": False, "test_units_only": True,
        }),
        "ordered_trace": all(type(event.get("index")) is int for event in events)
        and all(left["index"] < right["index"] for left, right in zip(events, events[1:])),
        "round_indices": type(decision.get("round_count")) is int and decision["round_count"] == len(rounds)
        and all(type(item.get("index")) is int and item["index"] == index for index, item in enumerate(rounds)),
    }
    evaluations = [i for i in operations if i.get("operation") == "evaluate"]
    releases = [i for i in operations if i.get("operation") == "release" and i.get("status") == "completed"]
    appeals = [i for i in operations if i.get("operation") == "appeal"]
    checks["evaluation_identity"] = len(evaluations) == 1 and evaluations[0].get("status") == "completed" \
        and evaluations[0].get("tx_id") == decision.get("tx_id")
    decision_final_events = [e for e in events if e.get("kind") == "transaction_observed"
                             and e.get("tx_id") == decision.get("tx_id") and e.get("status") == "FINALIZED"
                             and e.get("result_sha256") == _hash(result)]
    checks["final_decision_observed"] = bool(decision_final_events)
    if name == "deny":
        checks["release_effect"] = not any(i.get("operation") == "release" for i in operations)
    else:
        release = releases[0] if len(releases) == 1 else {}
        release_events = [e for e in events if e.get("kind") == "transaction_observed"
                          and e.get("tx_id") == release.get("tx_id") and e.get("status") == "FINALIZED"
                          and e.get("result_sha256") == _hash(state)]
        submissions = [e for e in events if e.get("kind") == "submission_intent" and e.get("operation") == "release"
                       and e.get("key") == release.get("idempotency_key")]
        checks["release_effect"] = len(releases) == 1 and _state_matches(release.get("result"), expected_state) \
            and type(release.get("tx_id")) is str and _HEX.fullmatch(release["tx_id"]) is not None \
            and release["tx_id"] != decision.get("tx_id") and bool(release_events)
        checks["release_after_finality"] = bool(decision_final_events and submissions and release_events) \
            and checks["ordered_trace"] and decision_final_events[-1]["index"] < submissions[0]["index"] < release_events[0]["index"]
    initial = {**expected_decision, **spec["initial_fixture"]}
    accepted = [(index, item) for index, item in enumerate(rounds) if item.get("kind") == "Accepted"
                and item.get("execution_success") is True]
    checks["initial_accepted_result"] = bool(accepted) and _state_matches(accepted[0][1].get("result"), initial)
    if name in {"overturn", "upheld"}:
        checks["appeal_identity"] = len(appeals) == 1 and appeals[0].get("status") == "completed" \
            and appeals[0].get("tx_id") == decision.get("tx_id")
        if name == "overturn":
            success = [index for index, item in enumerate(rounds) if item.get("kind") == "Validator Appeal Successful"]
            checks["observed_appeal_outcome"] = bool(accepted and success) and any(
                accepted[0][0] < appeal_index < accepted_index
                and _state_matches(item.get("result"), expected_decision)
                and item.get("result") != accepted[0][1].get("result")
                for appeal_index in success for accepted_index, item in accepted[1:]
            )
        else:
            failed = [index for index, item in enumerate(rounds) if item.get("kind") == "Validator Appeal Failed"]
            checks["observed_appeal_outcome"] = bool(accepted and failed) and accepted[0][0] < failed[0] \
                and not any(item.get("kind") == "Validator Appeal Successful" for item in rounds) \
                and all(_state_matches(item.get("result"), expected_decision) for _, item in accepted)
    else:
        checks["no_unrequested_appeal"] = not appeals and not any("Appeal" in str(item.get("kind")) for item in rounds)
    bad = [i for i in operations if i.get("idempotency_key") == KEYS["unsafe-release"]]
    if name == "unsafe":
        checks["faulty_behavior_detected"] = len(bad) == 1 and bad[0].get("operation") == "release" \
            and bad[0].get("status") == "rejected" and bad[0].get("tx_id") is None \
            and bad[0].get("error_code") in _BAD_ATTEMPT_CODES \
            and report.get("behavior_failures") == [bad[0]["error_code"]]
    else:
        checks["no_unexpected_behavior_failures"] = report.get("behavior_failures") == [] and not bad
    runtime_ok = checks["clean_completion"] and checks["real_studio_provenance"] and checks["scope_flags"]
    verdict = "inconclusive" if not runtime_ok else "pass" if all(checks.values()) else "fail"
    return {"case": name, "run_id": run_id, "verification": verdict, "checks": checks,
            "expected_report_verdict": expected_verdict, "actual_report_verdict": report.get("verification"),
            "agent": {key: outcome.get(key) for key in ("run_id", "status", "outcome")},
            "mode": "live_http_studio" if checks["real_studio_provenance"] else "unverified_http_backend"}


def _redact(value, secrets_to_remove):
    if type(value) is dict:
        return {key: "[redacted]" if key.lower() in _SECRET_KEYS else _redact(item, secrets_to_remove)
                for key, item in value.items()}
    if type(value) is list:
        return [_redact(item, secrets_to_remove) for item in value]
    if type(value) is str:
        for secret in secrets_to_remove:
            value = value.replace(secret, "[redacted]")
        return value
    return value


def verify_workflows(url, admin_token, *, cases=None, timeout_seconds=600, progress=None) -> dict:
    """Run fixed cases sequentially with a per-case deadline including cleanup.

    The reference driver's finite HTTP timeout also bounds an in-flight request.
    A failed create is never retried: the server may already have accepted it.
    Any unexpected result or incomplete cleanup stops the suite before creating
    another run. Unit tests may inject HTTP doubles; missing owned-Studio
    provenance is explicitly inconclusive, never a substitute backend success.
    """
    url = validate_base_url(url)
    if (isinstance(timeout_seconds, bool) or not isinstance(timeout_seconds, (int, float))
            or not math.isfinite(timeout_seconds) or not 1 <= timeout_seconds <= 1800):
        raise ValueError("Per-case timeout must be finite and between 1 and 1800 seconds")
    if (type(admin_token) is not str or not admin_token or len(admin_token) > 256
            or not admin_token.isascii() or any(char.isspace() for char in admin_token)):
        raise ValueError("Invalid administrator token")
    names = list(CASES) if cases is None else list(cases) if type(cases) in (list, tuple) else None
    if not names or any(type(name) is not str or name not in CASES for name in names) or len(set(names)) != len(names):
        raise ValueError("Select unique supported workflow cases")
    prepared = [_case(name, timeout_seconds) for name in names]  # Validate all before creating any run.
    output = {"schema_version": 1, "verification": "inconclusive", "selected_cases": names,
              "timeout_seconds_per_case": timeout_seconds, "mode": "unverified_http_backend",
              "scope": {"transport": "run_scoped_http", "agent": "scripted_reference_policy",
                        "evidence": "reported_by_loopback_lab", "contract_model_replies": "controlled",
                        "contract_model_quality_evaluated": False, "public_network": False},
              "cases": [], "reports": {}, "stopped_early": False, "error_code": None}
    secrets_to_remove = [admin_token]
    backend_identity = None
    request_timeout = min(5.0, timeout_seconds / 20)

    def notify(item):
        if progress is not None:
            try:
                progress(copy.deepcopy(item))
            except Exception:
                output["progress_callback_failed"] = True

    with LabClient(url, admin_token, timeout=request_timeout) as administrator:
        for case in prepared:
            name = case["case"]
            started = time.monotonic()
            deadline = started + timeout_seconds
            run_id = None
            summary = None
            error_code = "workflow_creation_unconfirmed"
            try:
                notify({"case": name, "stage": "starting"})
                created = administrator.workflow_create(case["spec"])
                if (type(created) is not dict or type(created.get("run_id")) is not str
                        or not _RUN.fullmatch(created["run_id"]) or type(created.get("agent_token")) is not str
                        or not 1 <= len(created["agent_token"]) <= 256 or not created["agent_token"].isascii()
                        or any(char.isspace() for char in created["agent_token"])):
                    raise ValueError("Invalid workflow creation response")
                run_id = created["run_id"]
                secrets_to_remove.append(created["agent_token"])
                notify({"case": name, "run_id": run_id, "stage": "created"})
                error_code = "reference_driver_failed"
                cleanup_budget = min(45.0, timeout_seconds / 4)
                execution_budget = deadline - time.monotonic() - cleanup_budget - 3 * request_timeout
                if execution_budget <= 0:
                    raise TimeoutError
                with LabClient(url, created["agent_token"], timeout=request_timeout) as agent:
                    outcome = run_workflow_agent(
                        agent, run_id, mode=case["mode"], timeout_seconds=execution_budget,
                        cleanup_timeout=cleanup_budget, poll_interval=min(.5, timeout_seconds / 100),
                    )
                error_code = "workflow_report_unavailable"
                if time.monotonic() >= deadline:
                    raise TimeoutError
                report = administrator.workflow_report(run_id)
                bounded_json(report)
                output["reports"][name] = _redact(copy.deepcopy(report), secrets_to_remove)
                error_code = "invalid_workflow_evidence"
                summary = _inspect(case, run_id, outcome, report)
                if summary["checks"]["real_studio_provenance"]:
                    identity = {key: report["manifest"]["backend"][key] for key in _IDENTITY_KEYS}
                    if backend_identity is None:
                        backend_identity = identity
                    summary["checks"]["stable_backend_identity"] = identity == backend_identity
                    if not summary["checks"]["stable_backend_identity"]:
                        summary["verification"] = "inconclusive"
                if time.monotonic() > deadline:
                    summary["verification"] = "inconclusive"
                    summary["checks"]["within_case_deadline"] = False
            except Exception:
                # Fixed messages only. Neither server errors nor provider text
                # can expose bearer credentials through returned diagnostics.
                summary = {"case": name, "run_id": run_id, "verification": "inconclusive",
                           "mode": "unverified_http_backend", "checks": {}, "error_code": error_code}
                if run_id is not None and time.monotonic() + request_timeout < deadline:
                    try:
                        administrator.workflow_cancel(run_id)
                        if time.monotonic() + request_timeout < deadline:
                            report = administrator.workflow_report(run_id)
                            bounded_json(report)
                            output["reports"][name] = _redact(report, secrets_to_remove)
                    except Exception:
                        pass  # Report remains inconclusive; the next case is never created.
            summary["elapsed_seconds"] = round(time.monotonic() - started, 3)
            output["cases"].append(summary)
            if summary["verification"] != "pass":
                output["error_code"] = summary.get("error_code", "workflow_checks_failed")
                output["stopped_early"] = len(output["cases"]) < len(prepared)
                break
            notify({"case": name, "run_id": run_id, "stage": "verified", "verification": "pass"})
    values = [item["verification"] for item in output["cases"]]
    output["verification"] = "fail" if "fail" in values else \
        "inconclusive" if "inconclusive" in values or len(values) != len(prepared) else "pass"
    if values and all(item["mode"] == "live_http_studio" for item in output["cases"]):
        output["mode"] = "live_http_studio"
    return _redact(output, secrets_to_remove)
