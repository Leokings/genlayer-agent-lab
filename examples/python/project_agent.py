"""Connect a separately installed Python reference agent to a reviewed project run."""

import argparse
import json
import os

from genlayer_agent_lab.client import LabClient
from genlayer_agent_lab.project_reference import run_project_agent


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--unsafe", action="store_true", help="Demonstrate a premature record attempt")
    args = parser.parse_args()
    with LabClient(os.environ.get("LAB_URL", "http://127.0.0.1:8765"), os.environ["LAB_TOKEN"]) as client:
        print(json.dumps(run_project_agent(client, os.environ["LAB_RUN_ID"], unsafe=args.unsafe)))
