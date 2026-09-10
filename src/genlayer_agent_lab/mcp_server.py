"""An MCP stdio bridge; all run state belongs to the HTTP service."""

from __future__ import annotations

import os
import sys
from typing import Any, Literal

from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

from genlayer_agent_lab import __version__
from genlayer_agent_lab.client import LabClient, LabError


def _scoped_workflow_call(action, *args):
    """Preserve only retry classification across MCP's expected tool errors."""
    try:
        return _workflow_tool_result(action(*args))
    except LabError as exc:
        # MCP 2 hides unexpected exception messages. Raising ToolError makes the
        # bounded classification available without exposing provider responses.
        code = exc.status_code
        if type(code) is int and 100 <= code <= 599:
            raise ToolError(f"Lab HTTP {code}: workflow request failed") from None
        raise ToolError("Lab connection failed (transport)") from None


def _workflow_tool_result(value):
    """MCP clients may parse JSON numbers through JavaScript's number type."""
    if type(value) is list:
        return [_workflow_tool_result(item) for item in value]
    if type(value) is dict and (value.get("profile") == "project"
                               or str(value.get("run_id", "")).startswith("project-")):
        from .project_wire import encode_project_wire
        return encode_project_wire(value)
    return value


def build_server(
    *,
    base_url: str = "http://127.0.0.1:8765",
    token: str,
    role: str = "agent",
    run_id: str | None = None,
    client: LabClient | None = None,
    mode: str = "scenario",
) -> MCPServer:
    if role not in {"admin", "agent"}:
        raise ValueError("LAB_ROLE must be admin or agent")
    if role == "agent" and not run_id:
        raise ValueError("LAB_RUN_ID is required for the agent role")
    if mode not in {"scenario", "workflow"}:
        raise ValueError("LAB_MODE must be scenario or workflow")
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

    if mode == "workflow":
        _add_workflow_tools(server, lab, role, run_id)
        return server

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


def _add_workflow_tools(server: MCPServer, lab: LabClient, role: str, run_id: str | None) -> None:
    if role == "admin":

        @server.tool(structured_output=True)
        def list_workflows() -> list[dict[str, Any]]:
            """List workflow run summaries without credentials."""
            return _scoped_workflow_call(lab.workflow_list)

        @server.tool(structured_output=True)
        def start_workflow(spec: dict[str, Any]) -> dict[str, Any]:
            """Create a workflow from its administrator-provided specification."""
            return _scoped_workflow_call(lab.workflow_create, spec)

        @server.tool(structured_output=True)
        def get_workflow(run_id: str) -> dict[str, Any]:
            """Read workflow progress without advancing it."""
            return _scoped_workflow_call(lab.workflow_get, run_id)

        @server.tool(structured_output=True)
        def get_workflow_report(run_id: str) -> dict[str, Any]:
            """Read workflow evidence, provenance and evaluator results."""
            return _scoped_workflow_call(lab.workflow_report, run_id)

        @server.tool(structured_output=True)
        def cancel_workflow(run_id: str) -> dict[str, Any]:
            """Cancel a workflow while retaining recorded evidence."""
            return _scoped_workflow_call(lab.workflow_cancel, run_id)

    else:
        assert run_id is not None

        @server.tool(structured_output=True)
        def observe() -> dict[str, Any]:
            """Observe this workflow's task, public state and operation contracts.

            Project builtin_operations provides argument schemas and examples for
            permitted Lab operations. binding.operations declares contract methods.
            """
            return _scoped_workflow_call(lab.workflow_observe, run_id)

        @server.tool(structured_output=True)
        def invoke_operation(
            operation: str, arguments: dict[str, Any], idempotency_key: str,
            expected_decision_id: str | None = None,
        ) -> dict[str, Any]:
            """Invoke a declared operation with the same key/arguments on retry.

            Project runs also declare inspect_fees, inspect_appeal and read_evidence
            in their policy. Read observe().builtin_operations for their argument
            schemas and examples, and binding.operations for contract methods.
            read_evidence takes arguments={"id": "<observe evidence ID>"}, not
            {"evidence_id": "..."}. Keep expected_decision_id outside arguments.
            Read observe() for current
            decision identities. Fees are local test balances; grading is private.
            If submit_investigation is permitted, observe() supplies its
            investigation_submission_schema and investigation_limit. Cite evidence
            already read in this run and supply the current successful decision's
            expected_decision_id. Submission records a Lab report artifact only;
            it does not submit a GenLayer appeal or contact a reviewer.
            Rejected operations retain error_code and actionable error_detail in
            observe().operations. Corrected arguments require a new idempotency
            key; retries of the same request must preserve the original key.
            """
            return _scoped_workflow_call(lab.workflow_invoke,
                run_id, operation, arguments, idempotency_key, expected_decision_id,
            )

        @server.tool(structured_output=True)
        def appeal_decision(idempotency_key: str, expected_decision_id: str) -> dict[str, Any]:
            """Appeal the identified active decision. This does not upload new evidence."""
            return _scoped_workflow_call(lab.workflow_appeal, run_id, idempotency_key, expected_decision_id)

        @server.tool(structured_output=True)
        def finish() -> dict[str, Any]:
            """Finish this workflow; evaluation remains available to its administrator."""
            return _scoped_workflow_call(lab.workflow_finish, run_id)


def main() -> None:
    try:
        server = build_server(
            base_url=os.environ.get("LAB_URL", "http://127.0.0.1:8765"),
            token=os.environ.get("LAB_TOKEN", ""),
            role=os.environ.get("LAB_ROLE", "agent"),
            run_id=os.environ.get("LAB_RUN_ID"),
            mode=os.environ.get("LAB_MODE", "scenario"),
        )
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(2) from None
    server.run(transport="stdio")


if __name__ == "__main__":
    main()
