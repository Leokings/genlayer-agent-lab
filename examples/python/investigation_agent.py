"""Investigate a reviewed run using only its public HTTP agent tools."""

import argparse
import json
import os

from genlayer_agent_lab.client import LabClient
from genlayer_agent_lab.investigation_reference import BEHAVIORS, run_investigation_agent


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--behavior", choices=BEHAVIORS, default="safe")
    parser.add_argument("--timeout", type=float, default=900)
    args = parser.parse_args()
    token, run_id = os.environ.get("LAB_TOKEN", ""), os.environ.get("LAB_RUN_ID", "")
    if not token or not run_id:
        parser.error("Set LAB_RUN_ID and its run-scoped LAB_TOKEN")
    with LabClient(os.environ.get("LAB_URL", "http://127.0.0.1:8765"), token) as client:
        print(json.dumps(run_investigation_agent(client, run_id, behavior=args.behavior,
                                                 timeout_seconds=args.timeout)))


if __name__ == "__main__":
    main()
