"""Run the prediction reference through the same four scoped MCP tools.

Set LAB_URL, LAB_TOKEN (the run credential) and LAB_RUN_ID, then execute this
script. It does not create a run, access a scenario or fetch grading reports.
"""

import argparse
import asyncio
import json
import os

from genlayer_agent_lab.project_reference import run_project_agent

if __package__:
    from .mcp_workflow_agent import run_mcp_workflow_agent
else:
    from mcp_workflow_agent import run_mcp_workflow_agent


async def run_mcp_project_agent(url, token, run_id, *, unsafe=False, timeout_seconds=900,
                                poll_interval=.5):
    if type(unsafe) is not bool:
        raise ValueError("unsafe must be a boolean")
    result = await run_mcp_workflow_agent(
        url, token, run_id, timeout_seconds=timeout_seconds, poll_interval=poll_interval,
        driver=run_project_agent,
        driver_options={"unsafe": unsafe, "timeout_seconds": timeout_seconds,
                        "poll_interval": poll_interval},
    )
    return {**result, "driver": "scripted_prediction_mcp"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--unsafe", action="store_true", help="Demonstrate a premature record attempt")
    parser.add_argument("--timeout", type=float, default=900)
    args = parser.parse_args()
    token, run_id = os.environ.get("LAB_TOKEN", ""), os.environ.get("LAB_RUN_ID", "")
    if not token or not run_id:
        parser.error("Set LAB_RUN_ID and its run-scoped LAB_TOKEN")
    try:
        result = asyncio.run(run_mcp_project_agent(
            os.environ.get("LAB_URL", "http://127.0.0.1:8765"), token, run_id,
            unsafe=args.unsafe, timeout_seconds=args.timeout,
        ))
    except ValueError as exc:
        parser.error(str(exc))
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
