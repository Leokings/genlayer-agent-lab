"""Run the scripted workflow policy through an agent-only MCP stdio subprocess.

Set LAB_URL, LAB_TOKEN and LAB_RUN_ID, then run mcp_workflow_agent.py
[safe|appeal|unsafe]. The credential must already belong to the workflow.
"""

from __future__ import annotations

import argparse
import asyncio
import concurrent.futures
import json
import math
import os
import re
import sys
import threading

from mcp.client import Client
from mcp.client.stdio import StdioServerParameters

from genlayer_agent_lab.client import LabError, validate_base_url
from genlayer_agent_lab.workflow_reference import run_workflow_agent

TOOLS = {"observe", "invoke_operation", "appeal_decision", "finish"}
_FORBIDDEN_PARAMETERS = {"run_id", "spec", "source", "fixtures", "expectations", "grades"}
_REQUEST_TIMEOUT = 35.0


def _seconds(value: float, name: str) -> float:
    if (isinstance(value, bool) or not isinstance(value, (int, float))
            or not math.isfinite(value) or not 0 < value <= 86_400):
        raise ValueError(f"{name} must be finite and between 0 and 86400 seconds")
    return float(value)


def _validate_toolset(result) -> bool:
    tools = getattr(result, "tools", None)
    if not isinstance(tools, list) or len(tools) != len(TOOLS):
        return False
    if {getattr(tool, "name", None) for tool in tools} != TOOLS:
        return False
    for tool in tools:
        schema = getattr(tool, "input_schema", None)
        if not isinstance(schema, dict):
            return False
        properties = schema.get("properties", {})
        if not isinstance(properties, dict) or _FORBIDDEN_PARAMETERS & properties.keys():
            return False
    return True


class _RunBoundMCPClient:
    """Synchronous four-method adapter; call it from the policy's worker thread."""

    def __init__(self, connection, loop, run_id: str, request_timeout: float = _REQUEST_TIMEOUT):
        self.connection, self.loop, self.run_id = connection, loop, run_id
        self.request_timeout = _seconds(request_timeout, "request_timeout")
        self._stopping = threading.Event()
        self._finishing = False

    def request_stop(self) -> None:
        self._stopping.set()

    async def _call_async(self, name: str, arguments: dict) -> dict:
        try:
            result = await asyncio.wait_for(self.connection.call_tool(name, arguments),
                                            timeout=self.request_timeout)
        except Exception:
            raise LabError("MCP transport failed") from None
        if getattr(result, "is_error", False):
            # The bridge may wrap LabError in an MCP tool error. Preserve only
            # its HTTP retry classification, never its raw text or credentials.
            content = " ".join(str(getattr(block, "text", "")) for block in
                               getattr(result, "content", []))
            match = re.search(r"\bLab HTTP ([1-5][0-9]{2}):", content)
            code = int(match[1]) if match else 400
            if ("Lab connection failed (" in content
                    or "Lab returned an invalid JSON response" in content):
                code = None
            raise LabError("MCP workflow tool failed", code)
        payload = getattr(result, "structured_content", None)
        if not isinstance(payload, dict):
            payload = None
            for block in getattr(result, "content", []):
                if getattr(block, "type", None) == "text":
                    try:
                        payload = json.loads(block.text)
                    except (ValueError, TypeError):
                        continue
                    if isinstance(payload, dict):
                        break
        if not isinstance(payload, dict):
            raise LabError("MCP returned invalid workflow data")
        if payload.get("run_id") != self.run_id:
            raise LabError("MCP returned a different workflow", 400)
        return payload

    def _call(self, run_id: str, name: str, arguments: dict) -> dict:
        if run_id != self.run_id:
            raise ValueError("MCP adapter is bound to a different workflow")
        if name not in TOOLS:
            raise ValueError("Unsupported workflow MCP tool")
        if self._stopping.is_set() and not self._finishing and name != "finish":
            raise LabError("MCP workflow agent was stopped", 400)
        if name == "finish":
            self._finishing = True
        try:
            current_loop = asyncio.get_running_loop()
        except RuntimeError:
            current_loop = None
        if current_loop is self.loop:
            raise RuntimeError("Synchronous MCP adapter requires a worker thread")
        future = asyncio.run_coroutine_threadsafe(self._call_async(name, arguments), self.loop)
        try:
            return future.result(timeout=self.request_timeout + 1)
        except concurrent.futures.TimeoutError:
            future.cancel()
            raise LabError("MCP request exceeded its timeout") from None
        except LabError:
            raise
        except Exception:
            raise LabError("MCP transport failed") from None

    def workflow_observe(self, run_id: str) -> dict:
        return self._call(run_id, "observe", {})

    def workflow_invoke(self, run_id: str, operation: str, arguments: dict,
                        idempotency_key: str, expected_decision_id: str | None = None) -> dict:
        payload = {"operation": operation, "arguments": arguments, "idempotency_key": idempotency_key}
        if expected_decision_id is not None:
            payload["expected_decision_id"] = expected_decision_id
        return self._call(run_id, "invoke_operation", payload)

    def workflow_appeal(self, run_id: str, idempotency_key: str, expected_decision_id: str) -> dict:
        return self._call(run_id, "appeal_decision", {
            "idempotency_key": idempotency_key, "expected_decision_id": expected_decision_id,
        })

    def workflow_finish(self, run_id: str) -> dict:
        return self._call(run_id, "finish", {})


async def run_mcp_workflow_agent(url: str, token: str, run_id: str, mode: str = "safe",
                                 timeout_seconds: float = 600, *, cleanup_timeout: float = 45,
                                 poll_interval: float = 0.5) -> dict:
    """Start agent-only stdio MCP, run the common policy, and return its outcome.

    Run and cleanup deadlines are separate. MCP requests have a 35-second bound;
    the subprocess receives only the run credential, never an administrator token.
    ``tools`` contains the verified fixed tool names, with no raw server metadata.
    """
    url = validate_base_url(url)
    if (type(token) is not str or not token or not token.isascii() or len(token) > 256
            or any(char.isspace() for char in token)):
        raise ValueError("A run-scoped ASCII bearer token is required")
    if (type(run_id) is not str or not run_id or "/" in run_id or run_id in {".", ".."}
            or len(run_id) > 256):
        raise ValueError("Invalid workflow identifier")
    if mode not in {"safe", "appeal", "unsafe"}:
        raise ValueError("mode must be safe, appeal or unsafe")
    timeout_seconds = _seconds(timeout_seconds, "timeout_seconds")
    cleanup_timeout = _seconds(cleanup_timeout, "cleanup_timeout")
    poll_interval = _seconds(poll_interval, "poll_interval")
    parameters = StdioServerParameters(
        command=sys.executable, args=["-m", "genlayer_agent_lab.mcp_server"],
        env={"LAB_URL": url, "LAB_TOKEN": token, "LAB_RUN_ID": run_id,
             "LAB_ROLE": "agent", "LAB_MODE": "workflow"},
    )
    result = {"run_id": run_id, "status": "unknown", "outcome": "client_error", "tools": []}
    try:
        async with Client(parameters, read_timeout_seconds=_REQUEST_TIMEOUT) as connection:
            tools = await asyncio.wait_for(connection.list_tools(), timeout=_REQUEST_TIMEOUT)
            if not _validate_toolset(tools):
                return {**result, "outcome": "mcp_toolset_mismatch"}
            result["tools"] = sorted(TOOLS)
            adapter = _RunBoundMCPClient(connection, asyncio.get_running_loop(), run_id)
            task = asyncio.create_task(asyncio.to_thread(
                run_workflow_agent, adapter, run_id, mode=mode, timeout_seconds=timeout_seconds,
                poll_interval=poll_interval, cleanup_timeout=cleanup_timeout,
            ))
            try:
                outcome = await asyncio.shield(task)
            except asyncio.CancelledError:
                # Keep the live MCP session available while the worker asks for
                # finish. Do not abandon a policy thread using a closed session.
                adapter.request_stop()
                try:
                    await asyncio.wait_for(asyncio.shield(task),
                                           timeout=2 * _REQUEST_TIMEOUT + cleanup_timeout + 2)
                except Exception:
                    pass
                raise
            return {key: outcome[key] for key in ("run_id", "status", "outcome")} | {
                "tools": sorted(TOOLS),
            }
    except Exception:
        return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", nargs="?", choices=("safe", "appeal", "unsafe"), default="safe")
    parser.add_argument("--timeout", type=float, default=600)
    parser.add_argument("--cleanup-timeout", type=float, default=45)
    args = parser.parse_args()
    token, run_id = os.environ.get("LAB_TOKEN", ""), os.environ.get("LAB_RUN_ID", "")
    if not token or not run_id:
        parser.error("Set LAB_TOKEN to a run-scoped token and LAB_RUN_ID to its workflow")
    try:
        result = asyncio.run(run_mcp_workflow_agent(
            os.environ.get("LAB_URL", "http://127.0.0.1:8765"), token, run_id,
            mode=args.mode, timeout_seconds=args.timeout, cleanup_timeout=args.cleanup_timeout,
        ))
    except ValueError as exc:
        parser.error(str(exc))
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
