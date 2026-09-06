"""An MCP stdio bridge; all run state belongs to the HTTP service."""

from __future__ import annotations

import os
import sys
from typing import Any, Literal

from mcp.server import MCPServer

from genlayer_agent_lab import __version__
from genlayer_agent_lab.client import LabClient


def build_server(
    *,
    base_url: str = "http://127.0.0.1:8765",
    token: str,
    role: str = "agent",
    run_id: str | None = None,
    client: LabClient | None = None,
) -> MCPServer:
    if role not in {"admin", "agent"}:
        raise ValueError("LAB_ROLE must be admin or agent")
    if role == "agent" and not run_id:
        raise ValueError("LAB_RUN_ID is required for the agent role")
    lab = client or LabClient(base_url, token)
    server = MCPServer(
        f"GenLayer Agent Lab ({role})",
        version=__version__,
        log_level="WARNING",
        instructions=(
            "These tools operate a private simulated testing environment. "
            "Runs and reports are owned by the separate Lab HTTP service."
        ),
    )

    if role == "admin":

        @server.tool(structured_output=True)
        def list_scenarios() -> list[dict[str, Any]]:
            """List scenario descriptions available to run."""
            return lab.list_scenarios()

        @server.tool(structured_output=True)
        def list_bindings() -> list[dict[str, Any]]:
            """List locally imported custom contract bindings; source stays on the server."""
            return lab.list_bindings()

        @server.tool(structured_output=True)
        def list_runs() -> list[dict[str, Any]]:
            """List run summaries without credentials."""
            return lab.list_runs()

        @server.tool(structured_output=True)
        def start_run(scenario_id: str, agent: str = "external",
                      backend: Literal["glsim", "fixture", "container-glsim", "studio"] = "glsim",
                      binding_id: str | None = None) -> dict[str, Any]:
            """Queue a test. Custom bindings use container-glsim or studio; no native fallback."""
            options = {}
            if backend != "glsim" or binding_id is not None:
                options["backend"] = backend
            if binding_id is not None:
                options["binding_id"] = binding_id
            return lab.create_run(scenario_id, agent, **options)

        @server.tool(structured_output=True)
        def get_run(run_id: str) -> dict[str, Any]:
            """Read progress for a run; does not advance its scenario."""
            return lab.get_run(run_id)

        @server.tool(structured_output=True)
        def get_report(run_id: str) -> dict[str, Any]:
            """Retrieve recorded evidence and independent evaluation results."""
            return lab.report(run_id)

        @server.tool(structured_output=True)
        def cancel_run(run_id: str) -> dict[str, Any]:
            """Cancel a run, retaining its evidence and marking it inconclusive."""
            return lab.cancel_run(run_id)

    else:
        assert run_id is not None

        @server.tool(structured_output=True)
        def observe() -> dict[str, Any]:
            """Observe this run's task and world; advances controlled scenario time."""
            return lab.observe(run_id)

        @server.tool(structured_output=True)
        def request_decision(idempotency_key: str) -> dict[str, Any]:
            """Request a decision. Keep the same key when retrying the same request."""
            return lab.request_decision(run_id, idempotency_key)

        @server.tool(structured_output=True)
        def read_decision() -> dict[str, Any]:
            """Read the latest decision, including status, scope and revision."""
            return lab.read_decision(run_id)

        @server.tool(structured_output=True)
        def act(
            operation: str,
            resource_id: str,
            policy_version: str,
            decision_id: str,
            revision: int,
            amount: int,
            idempotency_key: str,
        ) -> dict[str, Any]:
            """Attempt a simulated action using a decision; retry with identical arguments."""
            return lab.act(
                run_id,
                {
                    "operation": operation,
                    "resource_id": resource_id,
                    "policy_version": policy_version,
                    "decision_id": decision_id,
                    "revision": revision,
                    "amount": amount,
                    "idempotency_key": idempotency_key,
                },
            )

        @server.tool(structured_output=True)
        def finish() -> dict[str, Any]:
            """Finish this test; the evaluator grades recorded behavior, not a claimed score."""
            return lab.finish(run_id)

    return server


def main() -> None:
    try:
        server = build_server(
            base_url=os.environ.get("LAB_URL", "http://127.0.0.1:8765"),
            token=os.environ.get("LAB_TOKEN", ""),
            role=os.environ.get("LAB_ROLE", "agent"),
            run_id=os.environ.get("LAB_RUN_ID"),
        )
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(2) from None
    server.run(transport="stdio")


if __name__ == "__main__":
    main()
