"""Project authoring and persistent Studio run commands.

Authoring commands do not start Docker or contact a model. Generated files use
exclusive creation, and reviewed cases always carry their immutable snapshot.
"""

import json
import os
import sys
from pathlib import Path

from pydantic import ValidationError

from .api import read_admin_token
from .client import LabClient
from .project_bindings import (
    load_project_binding,
    project_binding_summary,
    validate_project_snapshot,
)
from .project_scenarios import (
    _read_yaml,
    approve_project_scenario,
    generate_scenario_variants,
    load_project_scenario,
    prediction_message_scenario_template,
    prediction_scenario_template,
    project_scenario_document,
    scenario_authoring_prompt,
    scenario_digest,
    scenario_json_schema,
)


def add_project_arguments(parser, common):
    commands = parser.add_subparsers(dest="project_operation", required=True)
    descriptions = {
        "snapshot": "Validate a local project and export its pinned source snapshot",
        "template": "Draft a supported prediction workflow scenario",
        "validate": "Validate a scenario draft and show the content digest for review",
        "schema": "Export the scenario JSON schema",
        "author-prompt": "Export drafting instructions for your own model; no model is called",
        "approve": "Record explicit developer review of the exact scenario digest",
        "variants": "Create bounded draft variations from a changes file",
        "create": "Start an approved project scenario on the persistent Lab service",
        "list": "List project workflow runs",
        "status": "Inspect one workflow run",
        "report": "Read a saved independent workflow report",
        "cancel": "Cancel observation of a project workflow, preserving its trace",
        "verify": "Verify project reference behavior on actual local Studio",
        "studio-build": "Build and start the separately owned fee-enabled Studio profile",
        "studio-up": "Start the already built project Studio profile",
        "studio-status": "Inspect the project Studio profile",
        "studio-down": "Stop project Studio services while preserving their data",
    }
    for name, description in descriptions.items():
        operation = commands.add_parser(name, help=description, description=description)
        common(operation, child=True)
        if name in {"snapshot", "template", "author-prompt"}:
            operation.add_argument("project", type=Path, help="Local manifest or exported snapshot")
        if name in {"validate", "approve", "variants", "create"}:
            operation.add_argument("spec", type=Path, help="Scenario YAML or JSON")
        if name == "template":
            operation.add_argument("--mode", choices=["finalize", "appeal_changed", "appeal_upheld", "delivered", "repair"],
                                   default="finalize")
        if name == "verify":
            operation.add_argument("--case", default="all", choices=["all", "prediction", "appeal-changed",
                                   "appeal-upheld", "unsafe", "messages", "message-repair", "fee-limit"])
            operation.add_argument("--timeout", type=int, default=900,
                                   help="Per-case deadline in seconds (maximum 1800)")
        if name == "approve":
            operation.add_argument("--reviewer", required=True, help="Developer who reviewed this content")
            operation.add_argument("--expected-sha256", required=True,
                                   help="Exact digest printed when reviewing the draft")
        if name == "variants":
            operation.add_argument("changes", type=Path, help="YAML/JSON array of explicit variations")
        if name in {"status", "report", "cancel"}:
            operation.add_argument("run_id")
        if name in {"snapshot", "template", "validate", "schema", "author-prompt", "approve",
                    "variants", "report", "verify"}:
            operation.add_argument("--output", type=Path, required=name in {"snapshot", "template", "approve", "variants"},
                                   help="New output file (new directory for variants); never overwritten")
        if name == "create":
            operation.add_argument("--show-agent-token", action="store_true",
                                   help="Explicitly print this run's scoped agent credential")
        if name == "studio-build":
            operation.add_argument("--port", type=int, default=8796,
                                   help="Fixed project RPC loopback port (default 8796)")


def _write(path, value, *, text=False):
    if not text and type(value) is dict and value.get("profile") == "project" and "project_snapshot" in value:
        value = project_scenario_document(value)
    with Path(path).open("x", encoding="utf-8") as stream:
        stream.write(value if text else json.dumps(value, indent=2, ensure_ascii=False))
        stream.write("\n")
    return str(Path(path).resolve())


def _project(path):
    value = _read_yaml(Path(path))
    if type(value) is dict and "definition" in value:
        return validate_project_snapshot(value)
    return load_project_binding(path)


def _summary(spec):
    return {"valid": True, "schema_version": 2, "profile": "project", "id": spec["id"],
            "review_status": spec["review"]["status"],
            "executable": spec["review"]["status"] == "approved",
            "content_sha256": scenario_digest(spec),
            "project": project_binding_summary(spec["project_snapshot"])}


def _execute(args):
    operation = args.project_operation
    if operation.startswith("studio-"):
        if args.url:
            raise ValueError("Project Studio commands manage this installation. Omit --url and LAB_URL.")
        from .runtime import studio_profiles

        if operation == "studio-build":
            if not 1024 <= args.port <= 65535:
                raise ValueError("Project Studio port must be between 1024 and 65535")
            return studio_profiles.setup_modern_profile(
                args.data_dir, port=args.port,
                progress=lambda message: print(message, file=sys.stderr, flush=True),
            )
        if operation == "studio-up":
            return studio_profiles.start_profile(args.data_dir)
        if operation == "studio-down":
            return studio_profiles.stop_profile(args.data_dir)
        return studio_profiles.modern_profile_status(args.data_dir)
    if operation == "schema":
        schema = scenario_json_schema()
        return {"schema": _write(args.output, schema)} if args.output else schema
    if operation == "snapshot":
        project = _project(args.project)
        return {"snapshot": _write(args.output, project), "project": project_binding_summary(project)}
    if operation == "author-prompt":
        prompt = scenario_authoring_prompt(_project(args.project))
        return {"prompt_file": _write(args.output, prompt, text=True)} if args.output else {"prompt": prompt}
    if operation == "template":
        template = (prediction_message_scenario_template if args.mode in {"delivered", "repair"}
                    else prediction_scenario_template)
        spec = template(_project(args.project), mode=args.mode)
        return {**_summary(spec), "draft": _write(args.output, spec)}
    if operation in {"validate", "approve", "variants"}:
        spec = load_project_scenario(args.spec, require_review=False)
        if operation == "validate":
            result = _summary(spec)
            if args.output:
                result["scenario"] = _write(args.output, spec)
            return result
        if operation == "approve":
            reviewed = approve_project_scenario(spec, reviewer=args.reviewer,
                                                expected_sha256=args.expected_sha256)
            return {**_summary(reviewed), "scenario": _write(args.output, reviewed)}
        variants = generate_scenario_variants(spec, _read_yaml(args.changes))
        # Validate every candidate before creating the new output directory.
        args.output.mkdir(parents=True, exist_ok=False)
        return {"review_required": True, "variants": [
            {**_summary(variant), "scenario": _write(args.output / (variant["id"] + ".json"), variant)}
            for variant in variants
        ]}
    if not args.url:
        raise ValueError("Project runs require a running Lab service. Supply --url so the session stays alive.")
    # Validate content before obtaining credentials or contacting the service.
    spec = load_project_scenario(args.spec) if operation == "create" else None
    token = os.environ.get("LAB_TOKEN") or read_admin_token(args.data_dir)
    if operation == "verify":
        from .project_verification import verify_projects

        if type(args.timeout) is not int or not 1 <= args.timeout <= 1800:
            raise ValueError("Verification timeout must be an integer from 1 to 1800 seconds")
        output = args.output.open("x", encoding="utf-8") if args.output else None
        try:
            result = verify_projects(args.url, token,
                                     cases=None if args.case == "all" else [args.case],
                                     timeout_seconds=args.timeout)
            if output:
                json.dump(result, output, indent=2, ensure_ascii=False)
            return {key: value for key, value in result.items() if key != "reports"}
        finally:
            if output:
                output.close()
    with LabClient(args.url, token) as client:
        if operation == "create":
            result = client.workflow_create(spec)
            if not args.show_agent_token:
                result.pop("agent_token", None)
                result["credential_note"] = (
                    "Use --show-agent-token when creating a run to give its scoped credential to your agent."
                )
            return result
        if operation == "list":
            return [entry for entry in client.workflow_list() if entry.get("profile") == "project"]
        if operation == "status":
            return client.workflow_get(args.run_id)
        if operation == "cancel":
            return client.workflow_cancel(args.run_id)
        if operation == "report":
            report = client.workflow_report(args.run_id)
            return {"run_id": args.run_id, "report": _write(args.output, report)} if args.output else report
    raise ValueError("Unknown project command")


def execute_project(args):
    try:
        return _execute(args)
    except ValidationError as exc:
        # CLI diagnostics must not echo contract sources, private fixtures or
        # credentials accidentally entered into invalid authoring fields.
        locations = [".".join(str(key) for key in error["loc"]) + ": " + error["type"]
                     for error in exc.errors(include_input=False, include_context=False)[:8]]
        raise ValueError("Invalid project scenario fields: " + "; ".join(locations)) from None


def project_exit_code(operation, result):
    if operation == "verify":
        return {"pass": 0, "fail": 1}.get(result.get("verification"), 2)
    if operation in {"studio-build", "studio-up", "studio-status"}:
        return 0 if result.get("ready") is True else 2
    if operation == "studio-down":
        return 0 if result.get("stopped") is True else 2
    return 0
