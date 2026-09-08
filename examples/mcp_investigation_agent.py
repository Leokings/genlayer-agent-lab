"""Investigate through the same four run-scoped MCP tools as any external agent."""

import argparse
import asyncio
import json
import os

from genlayer_agent_lab.investigation_reference import BEHAVIORS, run_investigation_agent

if __package__:
    from .mcp_workflow_agent import run_mcp_workflow_agent
else:
    from mcp_workflow_agent import run_mcp_workflow_agent


async def run_mcp_investigation_agent(url, token, run_id, *, behavior="safe", timeout_seconds=900,
                                      poll_interval=.5):
    if behavior not in BEHAVIORS:
        raise ValueError("Unknown investigation behavior")
    result = await run_mcp_workflow_agent(
        url, token, run_id, timeout_seconds=timeout_seconds, poll_interval=poll_interval,
        driver=run_investigation_agent,
        driver_options={"behavior": behavior, "timeout_seconds": timeout_seconds,
                        "poll_interval": poll_interval},
    )
    return {**result, "driver": "scripted_investigation_mcp"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--behavior", choices=BEHAVIORS, default="safe")
    parser.add_argument("--timeout", type=float, default=900)
    args = parser.parse_args()
    token, run_id = os.environ.get("LAB_TOKEN", ""), os.environ.get("LAB_RUN_ID", "")
    if not token or not run_id:
        parser.error("Set LAB_RUN_ID and its run-scoped LAB_TOKEN")
    try:
        result = asyncio.run(run_mcp_investigation_agent(
            os.environ.get("LAB_URL", "http://127.0.0.1:8765"), token, run_id,
            behavior=args.behavior, timeout_seconds=args.timeout,
        ))
    except ValueError as exc:
        parser.error(str(exc))
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
