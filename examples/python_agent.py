"""A framework-independent, scripted reference agent using only the public HTTP client.

Run an external test with LAB_TOKEN (admin) and --scenario, or join an existing
test with LAB_TOKEN (run credential) and LAB_RUN_ID. This is not an AI model.
"""

from __future__ import annotations

import argparse
import json
import os
import time

from genlayer_agent_lab.client import LabClient, LabError


def run_agent(client: LabClient, run_id: str, *, timeout: float = 180) -> dict:
    deadline = time.monotonic() + timeout
    observation = None
    while time.monotonic() < deadline:
        try:
            observation = client.observe(run_id)
            if observation.get("status") == "running":
                break
        except LabError as exc:
            if exc.status_code != 409:
                raise
        time.sleep(0.25)
    else:
        raise TimeoutError("Test did not become ready before the deadline")

    task = observation["task"]
    client.request_decision(run_id, f"{run_id}:decision")
    for _ in range(20):
        if time.monotonic() >= deadline:
            raise TimeoutError("Agent exceeded its wall-clock limit")
        observation = client.observe(run_id)
        decision = client.read_decision(run_id)
        if not decision or decision.get("status") in {"pending", "provisional"}:
            continue
        permitted = (
            decision.get("status") == "final"
            and decision.get("execution_result") == "success"
            and decision.get("verdict") == "approve"
            and decision.get("resource_id") == task["resource_id"]
            and decision.get("policy_version") == task["policy_version"]
            and isinstance(decision.get("revision"), int)
        )
        if permitted:
            action = {
                "operation": task["operation"],
                "resource_id": task["resource_id"],
                "policy_version": task["policy_version"],
                "decision_id": decision["decision_id"],
                "revision": decision["revision"],
                "amount": task["amount"],
                "idempotency_key": f"{run_id}:action",
            }
            result = client.act(run_id, action)
            if result.get("status") == "unknown":
                # An acknowledgement was lost. Do not create a second action.
                client.act(run_id, action)
        break
    return client.finish(run_id)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scenario", help="Create an external run using an admin token")
    parser.add_argument("--timeout", type=float, default=180)
    args = parser.parse_args()
    base_url = os.environ.get("LAB_URL", "http://127.0.0.1:8765")
    token = os.environ.get("LAB_TOKEN", "")
    run_id = os.environ.get("LAB_RUN_ID", "")
    if args.scenario:
        with LabClient(base_url, token) as admin:
            created = admin.create_run(args.scenario)
        run_id, token = created["run_id"], created["agent_token"]
    if not run_id:
        parser.error("Set LAB_RUN_ID and a run-scoped LAB_TOKEN, or supply --scenario and an admin token")
    with LabClient(base_url, token) as agent:
        report = run_agent(agent, run_id, timeout=args.timeout)
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
