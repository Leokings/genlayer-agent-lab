"""Versioned, data-only bindings for several methods of one GenLayer contract.

Snapshots contain source bytes and canonical hashes, never a live filesystem
reference. Validate them at each execution boundary and store their returned
copies; changing a definition or source invalidates the existing snapshot.
Result validation preserves structured values. It never projects a partial
authorization into a binary approval or infers protocol business semantics.
"""

import copy
import hashlib
from pathlib import Path, PureWindowsPath
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, JsonValue, model_validator

from .bindings import MAX_DEFINITION_BYTES, MAX_SOURCE_BYTES, _snapshot, bounded_json

BUNDLED_SOURCE = "bundled:service_workflow"
CONTRACT_PATH = Path(__file__).parent / "runtime" / "contracts" / "service_workflow.py"
FIXTURE_PREFIX = "AGENT_LAB_SERVICE_WORKFLOW_V1\n"
MAX_TEST_UNITS = 1_000_000
FIELD_SOURCES = {"evidence", "resource_id", "policy_version", "amount", "requested_amount"}
FIXTURE_SOURCES = FIELD_SOURCES | {"fixture_decision", "fixture_amount"}
NAME_PATTERN = r"^[A-Za-z][A-Za-z0-9_]{0,95}$"


class WorkflowArgument(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)
    from_field: Literal[
        "evidence", "resource_id", "policy_version", "amount", "requested_amount"
    ] | None = None
    literal: JsonValue = None

    @model_validator(mode="after")
    def one_source(self):
        if self.model_fields_set not in ({"from_field"}, {"literal"}):
            raise ValueError("Argument must specify exactly one of from_field or literal")
        if "from_field" in self.model_fields_set and self.from_field is None:
            raise ValueError("from_field cannot be null")
        return self


class ResultField(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)
    type: Literal["string", "integer", "boolean"]
    enum: list[str] | None = Field(default=None, min_length=1, max_length=32)
    minimum: int | None = None
    maximum: int | None = None
    max_length: int = Field(default=16000, ge=1, le=16000)

    @model_validator(mode="after")
    def constraints_match_type(self):
        if self.enum is not None and (
            self.type != "string" or len(set(self.enum)) != len(self.enum)
            or any(len(item) > self.max_length for item in self.enum)
        ):
            raise ValueError("enum requires unique bounded strings on a string field")
        if (self.minimum is not None or self.maximum is not None) and self.type != "integer":
            raise ValueError("Numeric limits require an integer field")
        if self.minimum is not None and self.maximum is not None and self.minimum > self.maximum:
            raise ValueError("minimum must not exceed maximum")
        return self


class ResultShape(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)
    fields: dict[str, ResultField] = Field(min_length=1, max_length=32)
    allow_extra: Literal[False] = False

    @model_validator(mode="after")
    def field_names(self):
        if any(not name or len(name) > 96 for name in self.fields):
            raise ValueError("Result field names must contain 1 to 96 characters")
        return self


class WorkflowOperation(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)
    method: str = Field(pattern=NAME_PATTERN)
    readonly: bool
    arguments: list[WorkflowArgument] = Field(default_factory=list, max_length=32)
    result: ResultShape


class WorkflowBinding(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)
    schema_version: Literal[1] = 1
    kind: Literal["workflow"] = "workflow"
    id: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]{0,95}$")
    title: str = Field(min_length=1, max_length=160)
    source: str = Field(min_length=1, max_length=240)
    constructor_args: list[JsonValue] = Field(default_factory=list, max_length=32)
    operations: dict[str, WorkflowOperation] = Field(min_length=1, max_length=16)
    llm_prefix: str = Field(min_length=1, max_length=512)
    llm_response: JsonValue

    @model_validator(mode="before")
    @classmethod
    def bounded(cls, value):
        bounded_json(value)
        if type(value) is dict and "schema_version" in value and type(value["schema_version"]) is not int:
            raise ValueError("schema_version must be an integer")
        return value

    @model_validator(mode="after")
    def confined_source_and_operations(self):
        if self.source != BUNDLED_SOURCE:
            path = PureWindowsPath(self.source)
            if (path.root or path.drive or Path(self.source).is_absolute()
                    or ".." in path.parts or ".." in Path(self.source).parts
                    or path.suffix != ".py" or ":" in self.source):
                raise ValueError("source must be a confined relative .py path or bundled:service_workflow")
        # Aliases do not permit several conflicting declarations of one method.
        methods = set()
        for name, operation in self.operations.items():
            if (not name or len(name) > 96 or not name[0].isalpha()
                    or not name.isascii() or not name.replace("_", "").isalnum()):
                raise ValueError("Invalid operation name")
            if operation.method in methods:
                raise ValueError("A contract method may appear only once in a workflow")
            methods.add(operation.method)
        return self


def _definition(value):
    model = WorkflowBinding.model_validate(value)
    result = model.model_dump()
    for name, operation in model.operations.items():
        result["operations"][name]["arguments"] = [
            argument.model_dump(exclude_unset=True) for argument in operation.arguments
        ]
    return result


def _read_yaml(path):
    with path.open("rb") as stream:
        raw = stream.read(MAX_DEFINITION_BYTES + 1)
    if len(raw) > MAX_DEFINITION_BYTES:
        raise ValueError("Workflow YAML exceeds 64 KiB")
    try:
        depth = 0
        starts = (yaml.tokens.BlockMappingStartToken, yaml.tokens.BlockSequenceStartToken,
                  yaml.tokens.FlowMappingStartToken, yaml.tokens.FlowSequenceStartToken)
        ends = (yaml.tokens.BlockEndToken, yaml.tokens.FlowMappingEndToken,
                yaml.tokens.FlowSequenceEndToken)
        for token in yaml.scan(raw):
            if isinstance(token, yaml.tokens.AliasToken):
                raise ValueError("YAML aliases are not supported in workflow bindings")
            if isinstance(token, starts):
                depth += 1
            elif isinstance(token, ends):
                depth -= 1
            if depth > 24:
                raise ValueError("Workflow YAML exceeds the structural limit")
        return yaml.safe_load(raw)
    except yaml.YAMLError as exc:
        raise ValueError("Invalid workflow YAML") from exc


def load_workflow_binding(path: Path) -> dict:
    """Read one YAML and one pinned source file; neither is executed."""
    path = Path(path).resolve(strict=True)
    definition = _definition(_read_yaml(path))
    if definition["source"] == BUNDLED_SOURCE:
        source_path = CONTRACT_PATH
    else:
        source_path = (path.parent / definition["source"].replace("\\", "/")).resolve(strict=True)
        if not source_path.is_relative_to(path.parent):
            raise ValueError("Contract source escapes the workflow binding directory")
    with source_path.open("rb") as stream:
        source = stream.read(MAX_SOURCE_BYTES + 1)
    if len(source) > MAX_SOURCE_BYTES:
        raise ValueError("Contract source exceeds 128 KiB")
    return _snapshot(definition, source.decode("utf-8"))


def validate_workflow_snapshot(snapshot: dict) -> dict:
    """Return a detached canonical copy, rejecting changed bytes or definitions."""
    if type(snapshot) is not dict or set(snapshot) != {
        "definition", "source", "source_sha256", "binding_sha256"
    } or type(snapshot.get("source")) is not str:
        raise ValueError("Invalid workflow binding snapshot")
    checked = _snapshot(_definition(snapshot["definition"]), snapshot["source"])
    if checked != snapshot:
        raise ValueError("Workflow snapshot hash or canonical definition mismatch")
    if checked["definition"]["source"] == BUNDLED_SOURCE:
        if hashlib.sha256(CONTRACT_PATH.read_bytes()).hexdigest() != checked["source_sha256"]:
            raise ValueError("Bundled workflow source does not match the packaged contract")
    return checked


def _operation(snapshot, operation):
    definition = validate_workflow_snapshot(snapshot)["definition"]
    if type(operation) is not str or operation not in definition["operations"]:
        raise ValueError("Operation is not declared by this workflow binding")
    return definition["operations"][operation]


def _context_field(context, name):
    if name not in context:
        raise ValueError(f"Missing workflow field: {name}")
    value = context[name]
    if name in {"amount", "requested_amount", "fixture_amount"}:
        minimum = 0 if name == "fixture_amount" else 1
        if type(value) is not int or not minimum <= value <= MAX_TEST_UNITS:
            raise ValueError(f"Invalid workflow integer field: {name}")
    else:
        maximum = 16000 if name == "evidence" else 128
        if type(value) is not str or not 1 <= len(value) <= maximum:
            raise ValueError(f"Invalid workflow string field: {name}")
        if name == "fixture_decision" and value not in {"approve", "deny", "partial"}:
            raise ValueError("Invalid workflow fixture decision")
    return copy.deepcopy(value)


def resolve_workflow_arguments(snapshot: dict, operation: str, context: dict) -> list:
    """Resolve only declared fields. No expression evaluation or host lookups."""
    definition = _operation(snapshot, operation)
    return [
        _context_field(context, arg["from_field"]) if "from_field" in arg
        else copy.deepcopy(arg["literal"])
        for arg in definition["arguments"]
    ]


def validate_workflow_result(snapshot: dict, operation: str, result) -> dict:
    """Validate a flat structured result without changing its domain meanings."""
    fields = _operation(snapshot, operation)["result"]["fields"]
    bounded_json(result)
    if type(result) is not dict or set(result) != set(fields):
        raise ValueError("Workflow result must contain exactly the declared fields")
    for name, spec in fields.items():
        value = result[name]
        expected = {"string": str, "integer": int, "boolean": bool}[spec["type"]]
        if type(value) is not expected:
            raise ValueError(f"Workflow result field has the wrong type: {name}")
        if expected is str and (len(value) > spec["max_length"]
                                or spec["enum"] is not None and value not in spec["enum"]):
            raise ValueError(f"Workflow result string is outside its declared limits: {name}")
        if expected is int and (
            spec["minimum"] is not None and value < spec["minimum"]
            or spec["maximum"] is not None and value > spec["maximum"]
        ):
            raise ValueError(f"Workflow result integer is outside its declared limits: {name}")
    return copy.deepcopy(result)


def resolve_workflow_fixture(snapshot: dict, context: dict) -> dict:
    """Return literal prefix and supplied JSON for isolated mock-provider setup."""
    definition = validate_workflow_snapshot(snapshot)["definition"]

    def resolve(value):
        if type(value) is str and value.startswith("$") and value[1:] in FIXTURE_SOURCES:
            return _context_field(context, value[1:])
        if type(value) is list:
            return [resolve(item) for item in value]
        if type(value) is dict:
            return {key: resolve(item) for key, item in value.items()}
        return value

    response = resolve(definition["llm_response"])
    bounded_json(response)
    return {"prefix": definition["llm_prefix"], "response": response}


def workflow_binding_summary(snapshot: dict) -> dict:
    checked = validate_workflow_snapshot(snapshot)
    definition = checked["definition"]
    return {
        **{key: definition[key] for key in ("schema_version", "kind", "id", "title")},
        "operations": {name: {"method": op["method"], "readonly": op["readonly"]}
                       for name, op in definition["operations"].items()},
        "source_sha256": checked["source_sha256"],
        "binding_sha256": checked["binding_sha256"],
    }


def bundled_workflow_snapshot(
    resource_id="service-001", policy_version="v1", amount=100,
) -> dict:
    context = {"resource_id": resource_id, "policy_version": policy_version, "amount": amount}
    for name in context:
        _context_field(context, name)
    fields = {
        "decision": {"type": "string", "enum": ["pending", "approve", "deny", "partial"]},
        "resource_id": {"type": "string", "max_length": 128},
        "policy_version": {"type": "string", "max_length": 128},
        "evidence": {"type": "string"},
        "unit": {"type": "string", "enum": ["test_units"]},
        **{name: {"type": "integer", "minimum": 0, "maximum": MAX_TEST_UNITS}
           for name in ("amount", "authorized_amount", "released_amount", "remaining_amount", "revision")},
    }
    operations = {}
    for name, readonly, arguments in (
        ("get_state", True, []),
        ("evaluate", False, ["evidence", "resource_id", "policy_version"]),
        ("release", False, ["requested_amount", "resource_id", "policy_version"]),
    ):
        operations[name] = {"method": name, "readonly": readonly,
                            "arguments": [{"from_field": key} for key in arguments],
                            "result": {"fields": copy.deepcopy(fields)}}
    definition = _definition({
        "schema_version": 1, "kind": "workflow", "id": "service-workflow",
        "title": "Service decision and contract ledger of test units",
        "source": BUNDLED_SOURCE, "constructor_args": [resource_id, policy_version, amount],
        "operations": operations, "llm_prefix": FIXTURE_PREFIX,
        "llm_response": {"decision": "$fixture_decision", "authorized_amount": "$fixture_amount"},
    })
    source = CONTRACT_PATH.read_bytes().decode("utf-8")
    return _snapshot(definition, source)
