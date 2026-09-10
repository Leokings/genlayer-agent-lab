"""Public built-in argument contracts shared by discovery and validation.

Examples describe request shapes, not scenario answers. Only public evidence
identifiers and declared contract argument types are used to construct them.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .project_investigation import InvestigationSubmission


class _Arguments(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class ReadEvidenceArguments(_Arguments):
    id: str = Field(pattern=r"^[a-zA-Z][a-zA-Z0-9_]{0,95}$",
                    description="An identifier from observe().evidence[].id. The field is id, not evidence_id.")


class InspectFeesArguments(_Arguments):
    operation: str = Field(pattern=r"^[a-zA-Z][a-zA-Z0-9_]{0,95}$",
                           description="A contract operation from observe().binding.operations.")
    arguments: dict[str, Any] = Field(default_factory=dict,
        description="Exactly the named arguments declared for that contract operation; use {} for none.")


class InspectAppealArguments(_Arguments):
    decision_id: str = Field(min_length=1, max_length=128,
                            description="A current decision_id from observe().transactions.")


ARGUMENT_MODELS = {
    "read_evidence": ReadEvidenceArguments,
    "inspect_fees": InspectFeesArguments,
    "inspect_appeal": InspectAppealArguments,
    "appeal": _Arguments,
    "submit_investigation": InvestigationSubmission,
}

DESCRIPTIONS = {
    "read_evidence": "Read one declared evidence item. Supply only {\"id\": \"<evidence ID>\"}.",
    "inspect_fees": "Inspect the fee for a declared contract call without executing it. "
                    "Nested arguments use that operation's public binding argument types and exact integers.",
    "inspect_appeal": "Inspect the appeal quote for a current decision without appealing it.",
    "appeal": "Appeal the identified current decision if public policy permits. "
              "Supply empty arguments and the observed expected_decision_id outside arguments. "
              "This does not upload new evidence.",
    "submit_investigation": "Record your own findings as a Lab report artifact; this does not submit an appeal. "
                            "Cite only evidence successfully read in this run, with unique evidence IDs. "
                            "Supply the current successfully executed decision's expected_decision_id. "
                            "Examples illustrate structure only; derive the assessment and result yourself.",
}


def _example_value(shape):
    kind = shape["type"]
    if kind == "integer":
        lower, upper = shape.get("minimum"), shape.get("maximum")
        return lower if lower is not None and lower > 0 else upper if upper is not None and upper < 0 else 0
    if kind == "boolean":
        return False
    if kind == "null":
        return None
    if kind == "array":
        return []
    if kind == "object":
        return {name: _example_value(child) for name, child in shape["fields"].items()}
    if kind == "address":
        return "0x" + "00" * 20
    return (shape.get("enum") or ["<value from the public task>"])[0][:shape.get("max_length", 4096)]


def builtin_operation_contracts(spec):
    """Return contracts only for built-ins declared in this run's visible policy."""
    allowed = set(spec["policy"]["operations"])
    if spec["policy"]["allow_appeal"]:
        allowed.add("appeal")
    evidence_id = next((item["id"] for item in spec["evidence"]), "evidence_id_from_observe")
    definitions = spec["project_snapshot"]["definition"]["operations"]
    operation = next((name for name in definitions if name in allowed), next(iter(definitions)))
    examples = {
        "read_evidence": {"id": evidence_id},
        "inspect_fees": {"operation": operation, "arguments": {
            item["name"]: _example_value(item["type"]) for item in definitions[operation]["arguments"]}},
        "inspect_appeal": {"decision_id": "<current decision_id from observe>"},
        "appeal": {},
        "submit_investigation": {
            "disposition": "request_review", "proposed_result": None,
            "findings": [{"evidence_id": evidence_id, "assessment": "untrusted",
                          "note": "Replace with your finding from evidence already read in this run."}],
            "summary": "Replace with your own evidence-based assessment and next step.",
        },
    }
    contracts = {}
    for name, model in ARGUMENT_MODELS.items():
        if name not in allowed:
            continue
        required = name in {"appeal", "submit_investigation"}
        example = {"operation": name, "arguments": examples[name],
                   "idempotency_key": "<unique key for this action; preserve unchanged on retry>"}
        if required:
            example["expected_decision_id"] = "<current decision_id from observe>"
        contracts[name] = {
            "description": DESCRIPTIONS[name], "arguments_schema": model.model_json_schema(),
            "expected_decision_id": "required; use the current observed decision" if required
                else "optional; omit unless binding this request to a current observed decision",
            "example": example,
        }
    return contracts


def builtin_argument_error(spec, operation, arguments):
    """Explain structural errors without returning submitted values or secrets."""
    model = ARGUMENT_MODELS.get(operation)
    if model is None:
        return None
    try:
        model.model_validate(arguments)
    except ValidationError as error:
        issues = []
        for item in error.errors(include_url=False, include_input=False, include_context=False)[:6]:
            location = ".".join(str(part) for part in item["loc"])
            issues.append("arguments" + ("." + location if location else "") + ": " + item["msg"])
        detail = "; ".join(issues) + "."
        if operation == "read_evidence":
            detail += ' Use exactly {"id": "<ID from observe().evidence>"}; the field is id, not evidence_id.'
        return detail + " See observe().builtin_operations for the schema. " \
            "Corrected arguments require a new idempotency key; retry an existing key only unchanged."
    except (ValueError, TypeError):
        return "Arguments exceed the supported structure or size. See observe().builtin_operations for the schema."
    if operation == "inspect_fees":
        from .project_bindings import normalize_project_arguments

        name = arguments["operation"]
        definition = spec["project_snapshot"]["definition"]["operations"].get(name)
        if definition is None:
            return "arguments.operation must name a contract operation from observe().binding.operations."
        supplied = arguments.get("arguments", {})
        expected = {item["name"] for item in definition["arguments"]}
        if set(supplied) != expected:
            return "arguments.arguments must contain exactly the declared fields: " + ", ".join(sorted(expected)) + "."
        try:
            normalize_project_arguments(spec["project_snapshot"], name, supplied)
        except ValueError:
            return "arguments.arguments must match the argument types and limits in observe().binding.operations for the chosen operation."
    return None
