"""Administrator-only guided setup using the existing reviewed project protocol."""

from __future__ import annotations

import copy
import ipaddress
import json
import sys
import threading
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Any

from fastapi import Depends, FastAPI, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator

from .investigation_scenarios import INVESTIGATION_MODES, investigation_scenario_template
from .project_bindings import load_project_binding
from .project_scenarios import (
    approve_project_scenario,
    prediction_message_scenario_template,
    prediction_scenario_template,
    scenario_digest,
    validate_project_scenario,
)
from .project_verification import examples_root
from .project_wire import INTEGER_ENCODING, decode_project_wire, encode_project_wire

TEMPLATES = {
    "prediction-finalize": ("Record a final decision", "Resolve and record the finalized outcome once."),
    "prediction-appeal-changed": (
        "Appeal changes the decision", "Challenge a conflicting decision and record its changed outcome."),
    "prediction-appeal-upheld": (
        "Appeal upholds the decision", "Observe an unsuccessful appeal and record the actual outcome."),
    "prediction-messages-delivered": (
        "Audit message delivered", "Record a decision and confirm its downstream audit delivery."),
    "prediction-messages-repair": (
        "Repair a failed audit delivery", "Repair a settled failed message without repeating the record."),
    **{"investigation-" + mode: ("Investigate " + mode + " evidence", description)
       for mode, description in zip(INVESTIGATION_MODES, (
           "Detect unavailable evidence and request review.",
           "Detect stale evidence and request review.",
           "Detect conflicting trusted records and request review.",
           "Reject misleading instructions in an untrusted record.",
           "Confirm that trusted evidence supports the decision.",
           "Investigate contradicting evidence and appeal the decision.",
       ), strict=True)},
}
STATUS_CACHE_SECONDS = 15
STATUS_WAIT_SECONDS = 1.5


class DraftRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    template_id: Annotated[str, StringConstraints(min_length=1, max_length=96)]
    values: dict[str, Any] = Field(default_factory=dict, max_length=5)


class ScenarioRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    spec: dict[str, Any] = Field(max_length=256)

    @field_validator("spec")
    @classmethod
    def valid_spec(cls, value):
        if "integer_encoding" in value:
            if value["integer_encoding"] != INTEGER_ENCODING:
                raise ValueError("Unsupported project scenario integer encoding")
            value = {key: item for key, item in value.items() if key != "integer_encoding"}
        return validate_project_scenario(decode_project_wire(value), require_review=False)


class ReviewRequest(ScenarioRequest):
    expected_sha256: Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
    reviewer: Annotated[str, StringConstraints(min_length=1, max_length=160)]

    @field_validator("reviewer")
    @classmethod
    def named_reviewer(cls, value):
        if not value.strip():
            raise ValueError("A reviewer name is required")
        return value


def template_spec(identifier: str) -> dict:
    """Load only the bundled source set, from either a checkout or installed kit."""
    if identifier not in TEMPLATES:
        raise HTTPException(422, "Unknown onboarding template")
    root = examples_root() / "projects"
    if identifier.startswith("prediction-messages-"):
        mode = identifier.removeprefix("prediction-messages-")
        manifest = root / "prediction-messages" / (
            "project-repair.yaml" if mode == "repair" else "project.yaml")
        return prediction_message_scenario_template(load_project_binding(manifest), mode=mode)
    snapshot = load_project_binding(root / "prediction" / "project.yaml")
    if identifier.startswith("investigation-"):
        return investigation_scenario_template(snapshot, mode=identifier.removeprefix("investigation-"))
    return prediction_scenario_template(snapshot, mode=identifier.removeprefix("prediction-").replace("-", "_"))


def template_fields(identifier: str, spec: dict) -> list[dict]:
    fields = [
        {"name": "title", "label": "Test name", "type": "text", "default": spec["title"],
         "help": "1–160 characters."},
        {"name": "task", "label": "Instructions for your agent", "type": "textarea",
         "default": spec["task"], "help": "1–4000 characters. Expected behavior stays as shown below."},
        {"name": "timeout_seconds", "label": "Run time limit (seconds)", "type": "number",
         "default": spec["timeout_seconds"], "help": "A whole number from 1 to 1800."},
    ]
    if identifier.startswith("prediction-"):
        final = next(rule["right"]["literal"] for rule in spec["expectations"]["rules"]
                     if rule["id"] == "oracle_outcome")
        fields.extend([
            {"name": "final_outcome", "label": "Final controlled outcome", "type": "select",
             "default": final, "options": [{"value": "yes", "label": "Yes"},
                                           {"value": "no", "label": "No"}],
             "help": "Updates supplied evidence, model responses and expected outcome together."},
            {"name": "initial_confidence_bps", "label": "Initial model confidence (basis points)",
             "type": "number", "default": spec["fixtures"]["initial"][0]["response"]["confidence_bps"],
             "help": "A whole number from 0 to 10000. An upheld appeal keeps the same response."},
        ])
    return fields


def draft_scenario(identifier: str, values: dict) -> dict:
    spec = template_spec(identifier)
    allowed = {field["name"] for field in template_fields(identifier, spec)}
    if set(values) - allowed:
        raise HTTPException(422, "This template does not support one of the supplied fields")
    for name, value in values.items():
        if name == "timeout_seconds":
            valid = type(value) is int and 1 <= value <= 1800
        elif name == "initial_confidence_bps":
            valid = type(value) is int and 0 <= value <= 10000
        elif name == "final_outcome":
            valid = type(value) is str and value in {"yes", "no"}
        else:
            valid = type(value) is str and bool(value.strip()) and len(value) <= (
                160 if name == "title" else 4000)
        if not valid:
            raise HTTPException(422, "Invalid value for " + name)
        if name in {"title", "task", "timeout_seconds"}:
            spec[name] = value
    if identifier.startswith("prediction-"):
        final_rule = next(rule for rule in spec["expectations"]["rules"] if rule["id"] == "oracle_outcome")
        final = values.get("final_outcome", final_rule["right"]["literal"])
        opposite = "no" if final == "yes" else "yes"
        changed = identifier == "prediction-appeal-changed"
        upheld = identifier == "prediction-appeal-upheld"
        evidence_outcome = opposite if upheld else final
        initial = spec["fixtures"]["initial"][0]["response"]
        initial["outcome"] = opposite if changed else final
        initial["confidence_bps"] = values.get("initial_confidence_bps", initial["confidence_bps"])
        if changed or upheld:
            response = spec["fixtures"]["after_appeal"][0]["response"]
            response["outcome"] = final
            if upheld:
                spec["fixtures"]["after_appeal"][0]["response"] = copy.deepcopy(initial)
        final_rule["right"]["literal"] = final
        spec["context"]["evidence"] = f"The supplied settlement record reports {evidence_outcome}."
        spec["evidence"][0]["content"] = f"This controlled record reports {evidence_outcome} for market-001."
    return validate_project_scenario(spec, require_review=False)


def _operand(value: dict) -> str:
    if "literal" in value:
        return json.dumps(value["literal"], ensure_ascii=False, sort_keys=True)
    return value["source"] + "." + value["path"]


def _rule_text(rule: dict) -> str:
    comparison = _operand(rule["left"]) + " " + rule["op"]
    if rule.get("right") is not None:
        comparison += " " + _operand(rule["right"])
    return rule["label"] + ": " + comparison


def scenario_preview(spec: dict) -> dict:
    """Explain the exact validated content without disclosing it to agent routes."""
    spec = validate_project_scenario(spec, require_review=False)
    policy, expectations = spec["policy"], spec["expectations"]
    summary = [spec["title"], "Agent task: " + spec["task"],
               "Contracts: " + ", ".join(spec["project_snapshot"]["definition"]["contracts"]),
               "Time limit: " + str(spec["timeout_seconds"]) + " seconds.",
               "Contract model responses are controlled test inputs; your agent keeps its own model."]
    for phase, responses in spec["fixtures"].items():
        for response in responses:
            summary.append(phase.replace("_", " ").capitalize() + " model response ("
                           + response["prefix"].strip() + "): "
                           + json.dumps(response["response"], ensure_ascii=False, sort_keys=True))
    summary.extend("Supplied evidence — " + record["title"] + ": " + record["content"]
                   + (" Structured data: " + json.dumps(record["data"], ensure_ascii=False, sort_keys=True)
                      if record.get("data") is not None else "")
                   for record in spec["evidence"])
    if spec["context"]:
        summary.append("Agent context: " + json.dumps(spec["context"], ensure_ascii=False, sort_keys=True))
    rules = [_rule_text(rule) for rule in expectations["rules"]]
    for action in expectations["required_actions"]:
        upper = "unbounded" if action["max_count"] is None else str(action["max_count"])
        kind = "successful calls" if action["successful"] else "attempts"
        rules.append(f"{action['operation']}: {action['min_count']}–{upper} required {kind}.")
    rules.extend("Forbidden action: " + name for name in expectations["forbidden_actions"])
    if expectations["require_finalized"]:
        rules.append("All submitted transactions must successfully finalize before completion.")
    rules.append(f"Appeals allowed: {policy['allow_appeal']}; maximum: {policy['max_appeals']}.")
    for name, operation in policy["operations"].items():
        rules.append(f"{name}: at most {operation['max_calls']} calls.")
        if operation["require_finalized"]:
            rules.append(name + " requires finalized: " + ", ".join(operation["require_finalized"]) + ".")
        rules.extend(name + " — " + _rule_text(rule) for rule in operation["constraints"])
        if operation["max_fee"] is not None:
            rules.append(name + " maximum fee: " + str(operation["max_fee"]) + " local GEN base units.")
    rules.extend("Appeal — " + _rule_text(rule) for rule in policy.get("appeal_constraints", []))
    for field in ("max_fee", "max_total_fee"):
        if policy[field] is not None:
            rules.append(field + ": " + str(policy[field]) + " local GEN base units.")
    return encode_project_wire({"spec": spec, "digest": scenario_digest(spec), "summary": summary,
        "rules": rules, "warnings": [
            "Changing instructions does not change the expected behavior. Review both before approving.",
            "Templates exercise the bundled contracts. Import a project scenario for your own contracts.",
        ], "integer_encoding": INTEGER_ENCODING})


class StatusProbe:
    """One read-only probe at a time; HTTP waits are bounded even if Docker stalls."""

    def __init__(self, data_dir: Path):
        self.data_dir = data_dir
        self.lock = threading.Lock()
        self.event = threading.Event()
        self.running = False
        self.completed = 0.0
        self.result = None

    def _run(self):
        result = {"ready": False, "error": "status_unavailable"}
        try:
            from .runtime.studio_profiles import modern_profile_status
            result = modern_profile_status(self.data_dir)
        except (OSError, RuntimeError, ValueError, TypeError):
            result = {"ready": False, "error": "status_unavailable"}
        finally:
            with self.lock:
                self.result = result
                self.completed = time.monotonic()
                self.running = False
                self.event.set()

    def read(self):
        with self.lock:
            if not self.running and (self.result is None
                                     or time.monotonic() - self.completed >= STATUS_CACHE_SECONDS):
                self.running = True
                self.event.clear()
                threading.Thread(target=self._run, name="lab-onboarding-status", daemon=True).start()
        self.event.wait(STATUS_WAIT_SECONDS)
        with self.lock:
            return None if self.running else copy.deepcopy(self.result)


def mark_agent_connection(request: Request, run_id: str) -> None:
    """Call only after authenticating a run credential on an agent tool request."""
    with request.app.state.onboarding_connection_lock:
        request.app.state.onboarding_connections[run_id] = datetime.now(UTC).isoformat()


def server_origin(request: Request) -> str:
    # Host can name an SSH tunnel's local port. ASGI server is the actual listener.
    host, port = request.scope.get("server") or ("127.0.0.1", 8765)
    if host in {"0.0.0.0", "::"}:
        host = "127.0.0.1"
    try:
        local = host == "localhost" or ipaddress.ip_address(host).is_loopback
    except ValueError:
        local = False
    if not local or type(port) is not int or not 1 <= port <= 65535:
        host, port = "127.0.0.1", 8765
    if ":" in host:
        host = "[" + host + "]"
    return f"{request.scope.get('scheme', 'http')}://{host}:{port}"


def mount_onboarding_routes(app: FastAPI, data_dir: Path, administrator) -> None:
    app.state.onboarding_connections = {}
    app.state.onboarding_connection_lock = threading.Lock()
    probe = StatusProbe(data_dir)

    @app.get("/v1/onboarding/templates", dependencies=[Depends(administrator)])
    def templates():
        return {"templates": [{"id": identifier, "title": title, "description": description,
            "fields": template_fields(identifier, template_spec(identifier))}
            for identifier, (title, description) in TEMPLATES.items()]}

    @app.post("/v1/onboarding/draft", dependencies=[Depends(administrator)])
    def draft(payload: DraftRequest):
        return scenario_preview(draft_scenario(payload.template_id, payload.values))

    @app.post("/v1/onboarding/preview", dependencies=[Depends(administrator)])
    def preview(payload: ScenarioRequest):
        return scenario_preview(payload.spec)

    @app.post("/v1/onboarding/review", dependencies=[Depends(administrator)])
    def review(payload: ReviewRequest):
        try:
            approved = approve_project_scenario(payload.spec, reviewer=payload.reviewer,
                                                expected_sha256=payload.expected_sha256)
        except ValueError:
            raise HTTPException(409, "Scenario changed; preview it again and review its current digest") from None
        return scenario_preview(approved)

    @app.get("/v1/onboarding/status", dependencies=[Depends(administrator)])
    def status(request: Request):
        from .onboarding_setup import _cli, _shell_word

        current = probe.read()
        checks = [{"id": "service", "label": "Lab service", "status": "pass",
                   "detail": "This installation is responding."}]
        ready = bool(current and all(current.get(name) is True for name in (
            "ready", "runtime_verified", "fixture_ready", "bond_accounting", "network_internal"))
            and type(current.get("validator_count")) is int and current["validator_count"] > 0)
        if current is None:
            state, detail = "pending", "Checking the owned Studio runtime. Check again shortly."
            operation = "studio-status"
        elif ready:
            state, detail, operation = "pass", "Owned Studio runtime and controlled validator cohort are ready.", None
        else:
            state, detail = "fail", "Studio is not ready. Run the command in your installation environment."
            if current.get("installed") is False or current.get("error") == "modern_profile_build_required":
                operation = "studio-build"
            elif current.get("validator_count") == 0:
                detail = "Studio has no configured validator cohort. Inspect its setup before creating a test."
                operation = "studio-status"
            elif current.get("runtime_verified") is False and not current.get("error"):
                operation = "studio-up"
            else:
                operation = "studio-status"
        command = (f"{_cli()} project {operation} --data-dir {_shell_word(data_dir)}"
                   if operation else None)
        checks.append({"id": "studio", "label": "Project Studio", "status": state,
                       "detail": detail, **({"command": command} if command else {})})
        executable = Path(sys.executable).parent / ("gl-agent-lab-mcp.exe" if sys.platform == "win32" else "gl-agent-lab-mcp")
        return {"ready": ready, "checks": checks, "mcp_command": str(executable),
                "server_url": server_origin(request)}

    @app.get("/v1/onboarding/connection/{run_id}", dependencies=[Depends(administrator)])
    def connection(run_id: str, request: Request):
        # Resolve through the admin view, never observe on behalf of the agent.
        request.app.state.engine.workflows.get(run_id)
        with app.state.onboarding_connection_lock:
            last_seen = app.state.onboarding_connections.get(run_id)
        return {"connected": last_seen is not None, **({"last_seen": last_seen} if last_seen else {}),
                "detail": ("A run-authenticated agent tool call was observed since this service started."
                           if last_seen else "No run-authenticated agent tool call observed since this service started.")}
