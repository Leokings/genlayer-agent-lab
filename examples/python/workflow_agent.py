"""Scripted service-release policy using an existing run-scoped Lab credential.

Set LAB_URL, LAB_TOKEN and LAB_RUN_ID, then run workflow_agent.py [safe|appeal|unsafe].
The returned outcome describes this agent's actions; grading is administrator-only.
"""

from __future__ import annotations

import argparse
import json
import os

from genlayer_agent_lab.client import LabClient
from genlayer_agent_lab.workflow_reference import run_workflow_agent


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", nargs="?", choices=("safe", "appeal", "unsafe"), default="safe")
    parser.add_argument("--timeout", type=float, default=600, help="Run deadline in seconds")
    parser.add_argument("--cleanup-timeout", type=float, default=45, help="Finish grace period in seconds")
    args = parser.parse_args()
    token, run_id = os.environ.get("LAB_TOKEN", ""), os.environ.get("LAB_RUN_ID", "")
    if not token or not run_id:
        parser.error("Set LAB_TOKEN to a run-scoped token and LAB_RUN_ID to its workflow")
    with LabClient(os.environ.get("LAB_URL", "http://127.0.0.1:8765"), token, timeout=5) as client:
        outcome = run_workflow_agent(client, run_id, mode=args.mode, timeout_seconds=args.timeout,
                                     cleanup_timeout=args.cleanup_timeout)
    print(json.dumps(outcome, indent=2))


if __name__ == "__main__":
    main()
