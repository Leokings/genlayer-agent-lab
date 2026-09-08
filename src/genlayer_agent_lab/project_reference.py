"""Reference prediction agent using only public, run-scoped workflow tools.

This is a scripted integration reference, not an LLM. It never reads a scenario
fixture, expected outcome or administrator report. Reusing the public operation
journal makes restarting the agent safe while the Lab continues a run.
"""

import math
import time

from .client import LabError


def project_agent_step(observation: dict, *, unsafe=False):
    """Choose at most one action from a public observation (also useful to SDK users)."""
    if observation.get("profile") != "project":
        raise ValueError("This reference requires the prediction project workflow")
    status = observation["status"]
    if status in {"completed", "inconclusive", "cancelled"}:
        return {"kind": "stop", "status": status}
    if status != "running":
        return None
    operations = observation["operations"]

    def existing(key):
        return next((i for i in operations if i["idempotency_key"] == key), None)

    def invoke(name, args, key, decision=None):
        return {"kind": "invoke", "operation": name, "arguments": args,
                "idempotency_key": key, "expected_decision_id": decision}

    failed = [i for i in operations if i["status"] in {"rejected", "failed", "cancelled"}]
    if failed:
        return {"kind": "finish"}
    evidence = existing("project-reference:evidence")
    if evidence is None:
        return invoke("read_evidence", {"id": "settlement_record"}, "project-reference:evidence")
    if evidence["status"] != "completed":
        return None
    resolve = existing("project-reference:resolve")
    if resolve is None:
        return invoke("resolve", {"evidence": observation["context"]["evidence"]}, "project-reference:resolve")
    if unsafe and not existing("project-reference:unsafe"):
        return invoke("record", {"expected_revision": 1, "outcome": "yes"}, "project-reference:unsafe")
    decision = next((t for t in observation["transactions"].values()
                     if t["operation"] == "resolve"), None)
    if not decision or decision.get("execution_success") is not True:
        return None
    desired = "yes" if "reports yes" in evidence["result"]["content"] else None
    if desired is None:
        raise ValueError("Reference agent cannot interpret this supplied evidence")
    appeal = existing("project-reference:appeal")
    if (decision.get("result", {}).get("outcome") != desired
            and observation["policy"]["allow_appeal"] and appeal is None):
        if not decision["appeal_eligible"]:
            if decision["status"] not in {"FINALIZED", "CANCELED"}:
                return None  # Execution can be visible before the appeal window opens.
            # The reference required a challenge but missed the window. Finishing
            # lets independent grading record the incomplete required behavior.
            return {"kind": "finish"}
        inspected = existing("project-reference:appeal-quote")
        if inspected is None:
            return invoke("inspect_appeal", {"decision_id": decision["decision_id"]},
                          "project-reference:appeal-quote")
        if inspected["status"] != "completed":
            return None
        return {"kind": "appeal", "idempotency_key": "project-reference:appeal",
                "expected_decision_id": decision["decision_id"]}
    if appeal is not None and appeal["status"] != "completed":
        return None
    if decision["status"] != "FINALIZED":
        return None
    state = observation["state"].get("oracle_state")
    if not state or state.get("revision", 0) < 1:
        return None
    if any(state.get(key) != decision["result"].get(key) for key in ("revision", "outcome")):
        return None
    messages = "authorize_audit" in observation["policy"]["operations"]
    if messages:
        authorized = existing("project-reference:authorize")
        if authorized is None:
            return invoke("authorize_audit", {"emitter": observation["contracts"]["recorder"]},
                          "project-reference:authorize")
        if authorized["status"] != "completed":
            return None
    record = existing("project-reference:record")
    if record is None:
        return invoke("record", {"expected_revision": state["revision"], "outcome": state["outcome"]},
                      "project-reference:record", decision["decision_id"])
    if record["status"] == "completed":
        if messages:
            children = observation["child_effects"]["by_operation"].get("record")
            if not children or children["pending"]:
                return None
            audit = observation["state"].get("audit_state", {})
            if not audit.get("recorded"):
                repaired = existing("project-reference:repair")
                if children["failed"] and repaired is None and "repair_audit" in observation["policy"]["operations"]:
                    return invoke("repair_audit", {"revision": state["revision"], "outcome": state["outcome"]},
                                  "project-reference:repair", decision["decision_id"])
                if repaired is not None and repaired["status"] != "completed":
                    return None
                if not audit.get("recorded"):
                    return None
        return {"kind": "finish"}
    return None


def run_project_agent(client, run_id, *, unsafe=False, timeout_seconds=900, poll_interval=.5):
    for value in (timeout_seconds, poll_interval):
        if type(value) not in {int, float} or not math.isfinite(value) or value <= 0:
            raise ValueError("Agent timing must be positive and finite")
    deadline = time.monotonic() + timeout_seconds
    pending = None
    finishing = False
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
                        "driver": "scripted_prediction_reference"}
            if not finishing:
                pending = project_agent_step(observation, unsafe=unsafe)
        except LabError as exc:
            # Preserve the exact pending action after a lost response.
            if (exc.status_code is not None and not 200 <= exc.status_code < 300
                    and exc.status_code < 500 and exc.status_code not in {408, 429}):
                raise
        time.sleep(min(poll_interval, max(0, deadline - time.monotonic())))
    return {"run_id": run_id, "status": "agent_deadline_exceeded",
            "driver": "scripted_prediction_reference"}
