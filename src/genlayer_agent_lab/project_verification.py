"""Consolidated checks against actual owned Studio via the public HTTP boundary."""

import time
from pathlib import Path

from .client import LabClient
from .project_reference import run_project_agent
from .project_scenarios import approve_project_scenario, load_project_scenario, scenario_digest

CASES = {
    "prediction": ("prediction-finalize.yaml", False, "pass"),
    "appeal-changed": ("prediction-appeal-changed.yaml", False, "pass"),
    "appeal-upheld": ("prediction-appeal-upheld.yaml", False, "pass"),
    "unsafe": ("prediction-finalize.yaml", True, "fail"),
    "messages": ("prediction-messages-delivered.yaml", False, "pass"),
    "message-repair": ("prediction-messages-repair.yaml", False, "pass"),
    "fee-limit": ("prediction-finalize.yaml", False, "fail"),
}


def examples_root():
    installed = Path(__file__).parent / "_kit" / "examples"
    if installed.is_dir():
        return installed
    source = Path(__file__).resolve().parents[2] / "examples"
    if not source.is_dir():
        raise ValueError("Packaged project examples are unavailable")
    return source


def reference_spec(case, *, timeout_seconds=900):
    if case not in CASES:
        raise ValueError("Unknown project verification case")
    filename, _, _ = CASES[case]
    spec = load_project_scenario(examples_root() / "project-scenarios" / filename, require_review=False)
    spec["timeout_seconds"] = timeout_seconds
    if case == "fee-limit":
        spec["policy"]["max_fee"] = 0
        spec["id"] = "prediction-fee-limit"
        spec["title"] = "Detect a write attempted beyond its zero fee allowance"
    # These are maintainer-authored reference expectations checked into the kit.
    # The CLI still requires review for arbitrary developer/LLM-authored drafts.
    return approve_project_scenario(spec, reviewer="Lab reference suite maintainer",
                                    expected_sha256=scenario_digest(spec))


def verify_projects(base_url, admin_token, *, cases=None, timeout_seconds=900):
    selected = list(CASES) if cases is None else list(cases)
    if (not selected or len(selected) != len(set(selected)) or not set(selected) <= CASES.keys()
            or type(timeout_seconds) is not int or not 1 <= timeout_seconds <= 1800):
        raise ValueError("Invalid project verification cases or deadline")
    results, reports = [], {}
    with LabClient(base_url, admin_token) as admin:
        for case in selected:
            spec = reference_spec(case, timeout_seconds=timeout_seconds)
            created = admin.workflow_create(spec)
            run_id = created["run_id"]
            started = time.monotonic()
            with LabClient(base_url, created["agent_token"]) as agent:
                result = run_project_agent(agent, run_id, unsafe=CASES[case][1],
                                           timeout_seconds=timeout_seconds + 65)
            if result["status"] == "agent_deadline_exceeded":
                admin.workflow_cancel(run_id)
            report = admin.workflow_report(run_id)
            reports[case] = report
            expected = CASES[case][2]
            matched = report["status"] == "completed" and report["verification"] == expected
            results.append({"case": case, "run_id": run_id, "expected": expected,
                            "observed": report["verification"], "status": "pass" if matched else "fail",
                            "elapsed_seconds": round(time.monotonic() - started, 2)})
            if report["cleanup"] != "restored":
                break  # Never reconfigure validators below unresolved transactions.
    return {"verification": "pass" if len(results) == len(selected)
            and all(case["status"] == "pass" for case in results) else "fail",
            "backend": "owned_fee_enabled_local_studio", "agent": "scripted_public_http_reference",
            "contract_model": "developer_supplied_fixtures", "public_chain": False,
            "cases": results, "reports": reports}
