"""Exercise the same scenarios through a real MCP stdio subprocess.

The reference policy is scripted. No OpenClaw or other agent framework is needed.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time

from mcp.client import Client
from mcp.client.stdio import StdioServerParameters

from genlayer_agent_lab.client import LabClient


async def run_agent(base_url: str, token: str, run_id: str) -> dict:
    parameters = StdioServerParameters(
        command=sys.executable,
        args=["-m", "genlayer_agent_lab.mcp_server"],
        env={"LAB_URL": base_url, "LAB_TOKEN": token, "LAB_ROLE": "agent", "LAB_RUN_ID": run_id},
    )
    async with Client(parameters, read_timeout_seconds=40) as client:

        async def call(name: str, arguments: dict | None = None) -> dict:
            result = await client.call_tool(name, arguments or {})
            if result.is_error:
                raise RuntimeError(f"MCP tool {name} failed: {result.content}")
            if isinstance(result.structured_content, dict):
                return result.structured_content
            for block in result.content:
                if block.type == "text":
                    return json.loads(block.text)
            raise RuntimeError(f"MCP tool {name} did not return structured evidence")

        observation = await call("observe")
        task = observation["task"]
        await call("request_decision", {"idempotency_key": f"{run_id}:decision"})
        for _ in range(20):
            await call("observe")
            decision = await call("read_decision")
            if decision.get("status") in {"pending", "provisional"}:
                continue
            if (
                decision.get("status") == "final"
                and decision.get("execution_result") == "success"
                and decision.get("verdict") == "approve"
                and decision.get("resource_id") == task["resource_id"]
                and decision.get("policy_version") == task["policy_version"]
                and isinstance(decision.get("revision"), int)
            ):
                action = {
                    "operation": task["operation"], "resource_id": task["resource_id"],
                    "policy_version": task["policy_version"], "amount": task["amount"],
                    "decision_id": decision["decision_id"], "revision": decision["revision"],
                    "idempotency_key": f"{run_id}:action",
                }
                result = await call("act", action)
                if result.get("status") == "unknown":
                    await call("act", action)
            break
        return await call("finish")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scenario", required=True)
    args = parser.parse_args()
    base_url = os.environ.get("LAB_URL", "http://127.0.0.1:8765")
    # Only orchestration uses the administrator credential. The MCP child gets
    # the run credential, with the SDK's minimal inherited system environment.
    with LabClient(base_url, os.environ.get("LAB_TOKEN", "")) as admin:
        created = admin.create_run(args.scenario)
        deadline = time.monotonic() + 180
        while time.monotonic() < deadline:
            status = admin.get_run(created["run_id"])["status"]
            if status == "running":
                break
            if status not in {"queued", "preparing"}:
                raise RuntimeError(f"Environment preparation ended with status {status}")
            time.sleep(0.25)
        else:
            raise TimeoutError("Environment preparation exceeded 180 seconds")
    result = asyncio.run(asyncio.wait_for(
        run_agent(base_url, created["agent_token"], created["run_id"]), timeout=120
    ))
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
