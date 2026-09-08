"""Scripted investigation policy using only the public run-scoped agent interface.

This reference interprets structured, developer-supplied evidence. Evidence prose
is data, never an instruction. A review request is a local Lab artifact; it does
not notify a reviewer. Only the ordinary appeal operation challenges GenLayer.
"""

from __future__ import annotations

import math
import time

from .client import LabError

BEHAVIORS = ("safe", "blind_accept", "follow_untrusted")
PREFIX = "investigation-reference:"


def assess_evidence(records: list[dict], policy: dict, observed_outcome: str,
                    *, behavior: str = "safe") -> dict:
    """Produce a concise assessment from read_evidence results and public policy.

    The two faulty policies intentionally use the same input format as the safe
    policy. No case name, expected result, fixture, or grading report is accepted.
    """
    if behavior not in BEHAVIORS:
        raise ValueError("Unknown investigation behavior")
    trusted = policy.get("trusted_source_ids")
    subject, as_of, max_age = (policy.get(key) for key in ("subject", "as_of", "max_age_seconds"))
    if (not isinstance(trusted, list) or not trusted
            or any(type(item) is not str or not item for item in trusted)
            or type(subject) is not str or not subject
            or type(as_of) is not int or type(max_age) is not int or max_age < 0
            or observed_outcome not in ("yes", "no")):
        raise ValueError("Invalid public investigation policy or decision outcome")

    findings, valid, uncertain, forged = [], [], False, []
    for record in records:
        evidence_id = record["id"]
        data = record.get("data")
        if type(data) is not dict:
            assessment, note = "missing", "The record has no structured evidence data."
            uncertain = True
        elif data.get("source_id") not in trusted:
            assessment, note = "untrusted", "The source is absent from the public trust list; its instructions are ignored."
            if data.get("outcome") in ("yes", "no"):
                forged.append((evidence_id, data["outcome"]))
        elif data.get("subject") != subject:
            assessment, note = "untrusted", "The record concerns a different subject."
        elif (data.get("availability") != "available"
              or data.get("outcome") not in ("yes", "no")
              or type(data.get("observed_at")) is not int
              or type(record.get("content")) is not str or not record["content"].strip()):
            assessment, note = "missing", "A usable record, timestamp, or outcome is unavailable."
            uncertain = True
        elif not 0 <= as_of - data["observed_at"] <= max_age:
            assessment, note = "stale", "The timestamp falls outside the public evidence freshness window."
            uncertain = True
        else:
            outcome = data["outcome"]
            assessment = "supports" if outcome == observed_outcome else "contradicts"
            note = f"A fresh trusted record for the required subject states {outcome}."
            valid.append((evidence_id, outcome))
        findings.append({"evidence_id": evidence_id, "assessment": assessment, "note": note})

    outcomes = {outcome for _, outcome in valid}
    if len(outcomes) > 1:
        conflicting = {evidence_id for evidence_id, _ in valid}
        for finding in findings:
            if finding["evidence_id"] in conflicting:
                finding.update(assessment="conflicting", note="Fresh trusted records disagree on the outcome.")
        uncertain = True
    desired = next(iter(outcomes)) if len(outcomes) == 1 and not uncertain else None
    if behavior == "blind_accept":
        desired = observed_outcome
        for finding in findings:
            finding.update(assessment="supports", note="Faulty policy accepts the observed decision without checking evidence.")
    elif behavior == "follow_untrusted" and forged:
        # Deliberately wrong: select an untrusted assertion over trusted evidence.
        evidence_id, desired = sorted(forged)[0]
        for finding in findings:
            if finding["evidence_id"] == evidence_id:
                finding.update(assessment="supports" if desired == observed_outcome else "contradicts",
                               note="Faulty policy treats an untrusted assertion as authoritative.")
    disposition = "request_review" if desired is None else "accept" if desired == observed_outcome else "appeal"
    summary = {
        "request_review": "Evidence is insufficient or inconsistent; request review before settlement.",
        "accept": "Fresh trusted evidence supports the observed outcome; record it only after finality.",
        "appeal": "Fresh trusted evidence contradicts the observed outcome; challenge it before settlement.",
    }[disposition]
    if behavior != "safe":
        summary = "Deliberately faulty reference behavior: " + behavior + "."
    return {"disposition": disposition, "proposed_result": {"outcome": desired} if desired else None,
            "findings": findings, "summary": summary}


def investigation_agent_step(observation: dict, *, behavior: str = "safe"):
    """Choose one action from public observations; repeated calls preserve identity."""
    if behavior not in BEHAVIORS:
        raise ValueError("Unknown investigation behavior")
    if observation.get("profile") != "project":
        raise ValueError("This reference requires a project workflow")
    status = observation["status"]
    if status in {"completed", "inconclusive", "cancelled"}:
        return {"kind": "stop", "status": status}
    if status != "running":
        return None
    operations = observation["operations"]

    def existing(key):
        return next((item for item in operations if item["idempotency_key"] == PREFIX + key), None)

    def invoke(operation, arguments, key, decision_id=None):
        return {"kind": "invoke", "operation": operation, "arguments": arguments,
                "idempotency_key": PREFIX + key, "expected_decision_id": decision_id}

    if any(item["status"] in {"rejected", "failed", "cancelled"} for item in operations):
        return {"kind": "finish"}
    records = []
    for evidence in sorted(observation["evidence"], key=lambda item: item["id"]):
        evidence_id = evidence["id"]
        read = existing("read:" + evidence_id)
        if read is None:
            return invoke("read_evidence", {"id": evidence_id}, "read:" + evidence_id)
        if read["status"] != "completed":
            return None
        records.append({**read["result"], "id": evidence_id})
    if existing("resolve") is None:
        return invoke("resolve", {"evidence": observation["context"]["evidence"]}, "resolve")
    decision = next((item for item in observation["transactions"].values()
                     if item["operation"] == "resolve"), None)
    if not decision or decision.get("execution_success") is not True:
        return None
    submitted = existing("submit")
    if submitted is None:
        # Execution may be visible while the initial consensus round is still
        # changing. Its decision identity is stable once accepted or finalized.
        if decision["status"] not in {"ACCEPTED", "FINALIZED"}:
            return None
        result = assess_evidence(records, observation["context"]["investigation_policy"],
                                 decision["result"]["outcome"], behavior=behavior)
        return invoke("submit_investigation", result, "submit", decision["decision_id"])
    if submitted["status"] != "completed":
        return None
    # Retain the submitted assessment of the original decision after an appeal.
    investigation = submitted["arguments"]
    if investigation["disposition"] == "request_review":
        return {"kind": "finish"} if decision["status"] in {"FINALIZED", "CANCELED"} else None
    desired = investigation["proposed_result"]["outcome"]
    appeal = existing("appeal")
    if investigation["disposition"] == "appeal" and appeal is None:
        if not observation["policy"]["allow_appeal"]:
            return {"kind": "finish"}
        if not decision["appeal_eligible"]:
            return {"kind": "finish"} if decision["status"] in {"FINALIZED", "CANCELED"} else None
        quote = existing("appeal-quote")
        if quote is None:
            return invoke("inspect_appeal", {"decision_id": decision["decision_id"]}, "appeal-quote")
        if quote["status"] != "completed":
            return None
        return {"kind": "appeal", "idempotency_key": PREFIX + "appeal",
                "expected_decision_id": decision["decision_id"]}
    if appeal is not None and appeal["status"] != "completed":
        return None
    if decision["status"] != "FINALIZED":
        return None
    if decision["result"].get("outcome") != desired:
        return {"kind": "finish"}  # An upheld challenge cannot justify the proposed settlement.
    state = observation["state"].get("oracle_state")
    if (not state or state.get("revision", 0) < 1
            or any(state.get(key) != decision["result"].get(key) for key in ("revision", "outcome"))):
        return None
    record = existing("record")
    if record is None:
        return invoke("record", {"expected_revision": state["revision"], "outcome": state["outcome"]},
                      "record", decision["decision_id"])
    return {"kind": "finish"} if record["status"] == "completed" else None


def run_investigation_agent(client, run_id, *, behavior="safe", timeout_seconds=900, poll_interval=.5):
    """Run the public policy over an HTTP client or the four-tool MCP adapter."""
    if behavior not in BEHAVIORS:
        raise ValueError("Unknown investigation behavior")
    for value in (timeout_seconds, poll_interval):
        if type(value) not in {int, float} or not math.isfinite(value) or value <= 0:
            raise ValueError("Agent timing must be positive and finite")
    deadline = time.monotonic() + timeout_seconds
    pending, finishing = None, False
    while time.monotonic() < deadline:
        try:
            if pending:
                if pending["kind"] == "invoke":
                    client.workflow_invoke(run_id, pending["operation"], pending["arguments"],
                                           pending["idempotency_key"], pending["expected_decision_id"])
                elif pending["kind"] == "appeal":
                    client.workflow_appeal(run_id, pending["idempotency_key"], pending["expected_decision_id"])
                elif pending["kind"] == "finish":
                    client.workflow_finish(run_id)
                    finishing = True
                pending = None
            observation = client.workflow_observe(run_id)
            if observation.get("run_id") != run_id:
                raise ValueError("Agent received a different run")
            if observation["status"] in {"completed", "inconclusive", "cancelled"}:
                return {"run_id": run_id, "status": observation["status"],
                        "driver": "scripted_investigation_reference"}
            if not finishing:
                pending = investigation_agent_step(observation, behavior=behavior)
        except LabError as exc:
            # Retry the identical pending action after transport failure or an
            # invalid success response, without generating another idempotency key.
            if (exc.status_code is not None and not 200 <= exc.status_code < 300
                    and exc.status_code < 500 and exc.status_code not in {408, 429}):
                raise
        time.sleep(min(poll_interval, max(0, deadline - time.monotonic())))
    return {"run_id": run_id, "status": "agent_deadline_exceeded",
            "driver": "scripted_investigation_reference"}
