"""Administrator commands for persistent, agent-driven Studio sessions."""

import json
import os

from .api import read_admin_token
from .client import LabClient
from .workflow_api import bounded_json
from .workflow_bindings import load_workflow_binding


def _load_spec(args):
    with args.spec.open("rb") as stream:
        raw = stream.read(49_153)
    if len(raw) > 49_152:
        raise ValueError("Workflow specification exceeds 48 KiB")
    try:
        spec = json.loads(raw)
        if type(spec) is not dict:
            raise ValueError
        bounded_json(spec)
    except (ValueError, UnicodeError, RecursionError):
        raise ValueError("Workflow specification must be bounded JSON object") from None
    if args.binding:
        spec["binding_snapshot"] = load_workflow_binding(args.binding)
    return spec


def execute_workflow(args):
    if args.workflow_operation == "validate":
        from pydantic import ValidationError

        from .workflow_bindings import workflow_binding_summary
        from .workflows import WorkflowSpec
        try:
            spec = WorkflowSpec.model_validate(_load_spec(args))
        except ValidationError as exc:
            locations = [".".join(str(key) for key in error["loc"]) + ": " + error["type"]
                         for error in exc.errors(include_input=False, include_context=False)[:8]]
            raise ValueError("Invalid workflow fields: " + "; ".join(locations)) from None
        return {"valid": True, "profile": spec.profile,
                "binding": workflow_binding_summary(spec.binding_snapshot)}
    if not args.url:
        raise ValueError("Workflows require a running Lab service. Supply --url so the session stays alive.")
    token = os.environ.get("LAB_TOKEN") or read_admin_token(args.data_dir)
    if args.workflow_operation == "verify":
        from .workflow_verification import verify_workflows
        output = args.output.open("x", encoding="utf-8") if args.output else None
        try:
            result = verify_workflows(args.url, token,
                                      cases=None if args.case == "all" else [args.case],
                                      timeout_seconds=args.timeout)
            if output:
                json.dump(result, output, ensure_ascii=False, indent=2)
            return {key: value for key, value in result.items() if key != "reports"}
        finally:
            if output:
                output.close()
    with LabClient(args.url, token) as client:
        operation = args.workflow_operation
        if operation == "create":
            spec = _load_spec(args)
            result = client.workflow_create(spec)
            if not args.show_agent_token:
                result.pop("agent_token", None)
                result["credential_note"] = "Use --show-agent-token when creating a run to give its credential to your agent."
            return result
        if operation == "list":
            return client.workflow_list()
        if operation == "status":
            return client.workflow_get(args.run_id)
        if operation == "cancel":
            return client.workflow_cancel(args.run_id)
        result = client.workflow_report(args.run_id)
        if args.output:
            with args.output.open("x", encoding="utf-8") as stream:
                json.dump(result, stream, ensure_ascii=False, indent=2)
            return {"run_id": args.run_id, "report": str(args.output.resolve())}
        return result
