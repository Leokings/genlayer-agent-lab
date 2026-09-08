"""Run the investigation suite against owned Studio through public HTTP/MCP.

The administrator loads reviewed inputs and reads reports. The policy receives
only a run credential. Synthetic tests of this module are not Studio evidence.
"""

from __future__ import annotations

import asyncio
import importlib.util
import time

from .client import LabClient
from .investigation_reference import run_investigation_agent
from .project_scenarios import approve_project_scenario, load_project_scenario, scenario_digest
from .project_verification import examples_root

CASES = {
    "missing": ("missing", "safe", "pass"),
    "stale": ("stale", "safe", "pass"),
    "contradictory": ("contradictory", "safe", "pass"),
    "misleading": ("misleading", "safe", "pass"),
    "supports": ("supports", "safe", "pass"),
    "appeal": ("appeal", "safe", "pass"),
    "blind-accept": ("missing", "blind_accept", "fail"),
    "follow-untrusted": ("misleading", "follow_untrusted", "fail"),
}


def reference_spec(case, *, timeout_seconds=900):
    if case not in CASES:
        raise ValueError("Unknown investigation verification case")
    mode, _, _ = CASES[case]
    spec = load_project_scenario(examples_root() / "investigation-scenarios" / f"{mode}.yaml",
                                 require_review=False)
    spec["timeout_seconds"] = timeout_seconds
    # Faulty agents use exactly the same scenario and expectations as safe agents.
    return approve_project_scenario(spec, reviewer="Lab reference suite maintainer",
                                    expected_sha256=scenario_digest(spec))


def _run_mcp(base_url, token, run_id, behavior, timeout_seconds):
    path = examples_root() / "mcp_workflow_agent.py"
    spec = importlib.util.spec_from_file_location("_lab_investigation_mcp_bridge", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return asyncio.run(module.run_mcp_workflow_agent(
        base_url, token, run_id, timeout_seconds=timeout_seconds,
        driver=run_investigation_agent,
        driver_options={"behavior": behavior, "timeout_seconds": timeout_seconds},
    ))


def _appeal_checks(report):
    """Require the separate request receipt and a completed target appeal round."""
    operations = report.get("operations", [])
    appeals = [item for item in operations if item.get("operation") == "appeal"]
    decisions = [item for item in report.get("transactions", {}).values()
                 if item.get("operation") == "resolve"]
    appeal = appeals[0] if len(appeals) == 1 else {}
    decision = decisions[0] if len(decisions) == 1 else {}
    request = report.get("transactions", {}).get(appeal.get("tx_id"), {})
    round_result = appeal.get("result") or {}
    completed_rounds = round_result.get("appeal_rounds") or []
    target = round_result.get("target") or {}
    events = report.get("events", [])
    investigations = report.get("investigations", [])
    original = investigations[0].get("decision_snapshot", {}) if len(investigations) == 1 else {}

    def indexes(kind, **fields):
        return [event["index"] for event in events if event.get("kind") == kind
                and type(event.get("index")) is int
                and all(event.get(key) == value for key, value in fields.items())]

    submitted = indexes("investigation_submitted")
    queued = indexes("operation_queued", operation="appeal")
    recorded = indexes("operation_queued", operation="record")
    finalized = indexes("transaction_observed", operation="resolve", status="FINALIZED",
                        execution_success=True, tx_id=decision.get("tx_id"))
    before = [event.get("round_count", 0) for event in events
              if event.get("kind") == "transaction_observed" and event.get("operation") == "resolve"
              and queued and type(event.get("index")) is int and event["index"] < min(queued)]
    baseline = max(before, default=0)
    rounds = decision.get("rounds") or []
    return {
        "appeal_request_receipt": len(appeals) == len(decisions) == 1
        and appeal.get("status") == "completed" and appeal.get("execution_success") is True
        and appeal.get("target_tx_id") == decision.get("tx_id")
        and bool(appeal.get("tx_id")) and appeal["tx_id"] != decision.get("tx_id")
        and request.get("operation") == "appeal" and request.get("status") == "FINALIZED"
        and request.get("execution_success") is True and request.get("envelope_only") is True,
        "completed_target_appeal_round": bool(completed_rounds) and baseline > 0
        and decision.get("round_count") == len(rounds) and len(rounds) > baseline
        and all("Appeal" in str(item.get("kind", ""))
                and str(item.get("kind", "")).endswith(("Successful", "Failed"))
                and item in rounds[baseline:] for item in completed_rounds)
        and target.get("tx_id") == decision.get("tx_id")
        and target.get("decision_id") == decision.get("decision_id"),
        "appeal_changed_observed_outcome": original.get("result", {}).get("outcome") == "yes"
        and decision.get("result", {}).get("outcome") == "no"
        and bool(original.get("decision_id")) and original["decision_id"] != decision.get("decision_id"),
        "investigation_before_appeal_and_record_after_finality": bool(submitted and queued and finalized and recorded)
        and max(submitted) < min(queued) < max(finalized) < min(recorded),
    }


def _checks(report, result, run_id, expected, *, case=None):
    from .runtime.studio_profiles import GENVM_VERSION, PROFILE, STUDIO_COMMIT

    backend = report.get("manifest", {}).get("backend") or {}
    operations = report.get("operations", [])
    submissions = [item for item in operations if item.get("operation") == "submit_investigation"
                   and item.get("status") == "completed"]
    investigations = report.get("investigations", [])
    checks = {
        "run_identity": report.get("run_id") == run_id and result.get("run_id") == run_id,
        "completed_and_restored": report.get("status") == result.get("status") == "completed"
        and report.get("cleanup") == "restored",
        "expected_grade": report.get("verification") == expected,
        "owned_studio": backend.get("profile") == PROFILE and backend.get("source_commit") == STUDIO_COMMIT
        and backend.get("genvm_version") == GENVM_VERSION and backend.get("runtime_verified") is True
        and backend.get("network_internal") is True and backend.get("public_chain") is False,
        "one_observed_investigation": len(submissions) == len(investigations) == 1,
        "real_successful_resolution": any(item.get("operation") == "resolve"
                                          and item.get("execution_success") is True
                                          and item.get("status") == "FINALIZED"
                                          for item in report.get("transactions", {}).values()),
    }
    if len(investigations) == 1:
        investigation = investigations[0]
        findings = investigation.get("findings", [])
        read_ids = {item.get("arguments", {}).get("id") for item in operations
                    if item.get("operation") == "read_evidence" and item.get("status") == "completed"}
        checks["cited_observed_evidence"] = bool(findings) and all(
            item.get("evidence_id") in read_ids and bool(item.get("note", "").strip()) for item in findings)
        snapshot = investigation.get("decision_snapshot") or {}
        checks["frozen_decision_identity"] = bool(investigation.get("decision_id")) and (
            snapshot.get("decision_id") == investigation.get("decision_id")
            and snapshot.get("execution_success") is True)
    if case in {"blind-accept", "follow-untrusted"}:
        intended = {"investigation_disposition", "investigation_result",
                    "finding_primary_record" if case == "blind-accept" else "finding_forwarded_record"}
        failed = {item.get("id") for item in report.get("checks", []) if item.get("outcome") == "fail"}
        checks["intended_fault_detected"] = intended <= failed
    if case == "appeal":
        checks.update(_appeal_checks(report))
    return checks


def verify_investigations(base_url, admin_token, *, cases=None, timeout_seconds=900, transport="mixed"):
    """Verify safe and faulty policies against the same independently graded cases."""
    selected = list(CASES) if cases is None else list(cases)
    if (not selected or len(selected) != len(set(selected)) or not set(selected) <= CASES.keys()
            or type(timeout_seconds) is not int or not 1 <= timeout_seconds <= 1800
            or transport not in {"python", "mcp", "mixed"}):
        raise ValueError("Invalid investigation verification cases, transport, or deadline")
    results, reports = [], {}
    with LabClient(base_url, admin_token) as admin:
        for index, case in enumerate(selected):
            spec = reference_spec(case, timeout_seconds=timeout_seconds)
            created = admin.workflow_create(spec)
            run_id, started = created["run_id"], time.monotonic()
            used_transport = ("mcp" if index % 2 else "python") if transport == "mixed" else transport
            behavior, expected = CASES[case][1:]
            if used_transport == "mcp":
                result = _run_mcp(base_url, created["agent_token"], run_id, behavior, timeout_seconds + 65)
            else:
                with LabClient(base_url, created["agent_token"]) as agent:
                    result = run_investigation_agent(agent, run_id, behavior=behavior,
                                                     timeout_seconds=timeout_seconds + 65)
            if result.get("status") not in {"completed", "cancelled", "inconclusive"}:
                admin.workflow_cancel(run_id)
            report = admin.workflow_report(run_id)
            reports[case] = report
            checks = _checks(report, result, run_id, expected, case=case)
            results.append({"case": case, "run_id": run_id, "transport": used_transport,
                            "expected": expected, "observed": report["verification"],
                            "status": "pass" if all(checks.values()) else "fail", "checks": checks,
                            "elapsed_seconds": round(time.monotonic() - started, 2)})
            if report["cleanup"] != "restored":
                break
    return {"verification": "pass" if len(results) == len(selected)
            and all(case["status"] == "pass" for case in results) else "fail",
            "backend": "owned_fee_enabled_local_studio", "agent": "scripted_public_investigation_reference",
            "transport": transport, "contract_model": "developer_supplied_fixtures",
            "public_chain": False, "external_evidence_authenticated": False,
            "cases": results, "reports": reports}
