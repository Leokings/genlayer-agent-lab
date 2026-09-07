"""Workflow routes using the service's existing administrator/run credentials.

The manager owns workflow semantics and agent-visible projections. Source code,
fixtures and grading expectations belong to administrator-only views.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Annotated, Any

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator

MAX_WORKFLOW_JSON_BYTES = 49_152
MAX_WORKFLOW_JSON_DEPTH = 16
MAX_WORKFLOW_JSON_ITEMS = 4096
WorkflowId = Annotated[str, StringConstraints(min_length=1, max_length=256)]
OperationName = Annotated[str, StringConstraints(min_length=1, max_length=160)]
IdempotencyKey = Annotated[str, StringConstraints(min_length=1, max_length=128)]
DecisionId = Annotated[str, StringConstraints(min_length=1, max_length=256)]


def bounded_json(value: dict[str, Any]) -> dict[str, Any]:
    """Bound opaque JSON before handing domain validation to the manager."""
    pending: list[tuple[Any, int]] = [(value, 0)]
    count = 0
    while pending:
        item, depth = pending.pop()
        count += 1
        if depth > MAX_WORKFLOW_JSON_DEPTH or count > MAX_WORKFLOW_JSON_ITEMS:
            raise ValueError("Workflow JSON exceeds structural limits")
        if isinstance(item, dict):
            if len(item) > 256 or any(not isinstance(key, str) or len(key) > 256 for key in item):
                raise ValueError("Workflow JSON object exceeds limits")
            pending.extend((child, depth + 1) for child in item.values())
        elif isinstance(item, list):
            if len(item) > MAX_WORKFLOW_JSON_ITEMS:
                raise ValueError("Workflow JSON array exceeds limits")
            pending.extend((child, depth + 1) for child in item)
        elif isinstance(item, str):
            if len(item) > 32_768:
                raise ValueError("Workflow JSON string exceeds limits")
        elif item is not None and type(item) not in {bool, int, float}:
            raise ValueError("Workflow data must be JSON")
    try:
        encoded = json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
        size = len(encoded.encode("utf-8"))
    except (ValueError, TypeError, OverflowError, UnicodeError):
        raise ValueError("Workflow data must be valid JSON") from None
    if size > MAX_WORKFLOW_JSON_BYTES:
        raise ValueError("Workflow JSON exceeds size limit")
    return value


class CreateWorkflow(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    spec: dict[str, Any] = Field(max_length=256)

    _validate_spec = field_validator("spec")(bounded_json)


class WorkflowOperation(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    operation: OperationName
    arguments: dict[str, Any] = Field(default_factory=dict, max_length=256)
    idempotency_key: IdempotencyKey
    expected_decision_id: DecisionId | None = None

    _validate_arguments = field_validator("arguments")(bounded_json)


class WorkflowAppeal(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    idempotency_key: IdempotencyKey
    expected_decision_id: DecisionId


def mount_workflow_routes(
    app: FastAPI,
    manager_getter: Callable[[Request], Any],
    administrator: Callable[..., Any],
    bearer: Callable[[str | None], str],
) -> None:
    """Mount workflow routes without changing service authentication or error handling."""

    def agent_access(
        run_id: WorkflowId,
        request: Request,
        authorization: Annotated[str | None, Header()] = None,
    ) -> None:
        token = bearer(authorization)
        try:
            valid = manager_getter(request).authenticate(run_id, token)
        except KeyError:
            valid = False
        if not valid:
            raise HTTPException(401, "Invalid run credentials")

    @app.post("/v1/workflows", status_code=201, dependencies=[Depends(administrator)])
    def create_workflow(payload: CreateWorkflow, request: Request) -> dict:
        return manager_getter(request).create(payload.spec)

    @app.get("/v1/workflows", dependencies=[Depends(administrator)])
    def list_workflows(request: Request) -> list[dict]:
        return manager_getter(request).list_runs()

    @app.get("/v1/workflows/{run_id}", dependencies=[Depends(administrator)])
    def get_workflow(run_id: WorkflowId, request: Request) -> dict:
        return manager_getter(request).get(run_id)

    @app.get("/v1/workflows/{run_id}/report", dependencies=[Depends(administrator)])
    def workflow_report(run_id: WorkflowId, request: Request) -> dict:
        return manager_getter(request).report(run_id)

    @app.post("/v1/workflows/{run_id}/cancel", dependencies=[Depends(administrator)])
    def cancel_workflow(run_id: WorkflowId, request: Request) -> dict:
        return manager_getter(request).cancel(run_id)

    @app.post("/v1/workflows/{run_id}/observe", dependencies=[Depends(agent_access)])
    def observe_workflow(run_id: WorkflowId, request: Request) -> dict:
        return manager_getter(request).observe(run_id)

    @app.post("/v1/workflows/{run_id}/operations", dependencies=[Depends(agent_access)])
    def invoke_operation(
        run_id: WorkflowId, payload: WorkflowOperation, request: Request,
    ) -> dict:
        return manager_getter(request).invoke(
            run_id, payload.operation, payload.arguments, payload.idempotency_key,
            expected_decision_id=payload.expected_decision_id,
        )

    @app.post("/v1/workflows/{run_id}/appeals", dependencies=[Depends(agent_access)])
    def appeal_decision(run_id: WorkflowId, payload: WorkflowAppeal, request: Request) -> dict:
        return manager_getter(request).appeal(
            run_id, payload.idempotency_key, payload.expected_decision_id,
        )

    @app.post("/v1/workflows/{run_id}/finish", dependencies=[Depends(agent_access)])
    def finish_workflow(run_id: WorkflowId, request: Request) -> dict:
        result = manager_getter(request).finish(run_id)
        # Finish may produce an administrator report; agents receive no grading oracle.
        return {key: result.get(key) for key in ("run_id", "status")}
