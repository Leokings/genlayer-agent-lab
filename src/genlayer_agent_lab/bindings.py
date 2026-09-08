"""Bounded declarative contract adapters. Import reads source; it never executes it."""

import copy
import hashlib
import json
import math
from pathlib import Path, PureWindowsPath
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, JsonValue, StrictInt, StrictStr, model_validator

from .runtime.pins import RUNNER_HASH

CONTEXT_FIELDS = {"evidence", "resource_id", "policy_version", "amount", "fixture_verdict"}
MAX_DEFINITION_BYTES = 65536
MAX_SOURCE_BYTES = 131072


def bounded_json(value, max_nodes=10000, *, max_bytes=262144):
    """Reject cycles, alias expansion, non-JSON types and deep nesting before parsing."""
    remaining = max_nodes

    def visit(item, depth, ancestors):
        nonlocal remaining
        remaining -= 1
        if remaining < 0 or depth > 24:
            raise ValueError("Binding JSON exceeds the structural limit")
        if type(item) in (dict, list):
            if id(item) in ancestors:
                raise ValueError("Recursive YAML aliases are not allowed")
            nested = ancestors | {id(item)}
            if type(item) is dict:
                for key, child in item.items():
                    if type(key) is not str:
                        raise ValueError("JSON object keys must be strings")
                    visit(child, depth + 1, nested)
            else:
                for child in item:
                    visit(child, depth + 1, nested)
        elif type(item) not in (str, int, float, bool, type(None)):
            raise ValueError("Binding values must be JSON-compatible")
        elif type(item) is float and not math.isfinite(item):
            raise ValueError("Non-finite JSON numbers are not allowed")

    visit(value, 0, set())
    if len(json.dumps(value, ensure_ascii=True).encode()) > max_bytes:
        raise ValueError("Expanded binding JSON is too large")


class Argument(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    from_field: Literal["evidence", "resource_id", "policy_version", "amount", "fixture_verdict"] | None = None
    literal: JsonValue = None

    @model_validator(mode="after")
    def one_source(self):
        if self.model_fields_set not in ({"from_field"}, {"literal"}):
            raise ValueError("Argument must specify exactly one of from_field or literal")
        if "from_field" in self.model_fields_set and self.from_field is None:
            raise ValueError("from_field cannot be null")
        return self


class ContractBinding(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    schema_version: Literal[1] = 1
    id: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]{0,95}$")
    title: str = Field(min_length=1, max_length=160)
    source: str = Field(min_length=1, max_length=240)
    constructor_args: list[JsonValue] = Field(default_factory=list, max_length=32)
    method: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9_]{0,95}$")
    arguments: list[Argument] = Field(default_factory=list, max_length=32)
    result_path: list[StrictStr | StrictInt] = Field(default_factory=list, max_length=16)
    approve_values: list[StrictStr] = Field(default_factory=lambda: ["approve"], min_length=1, max_length=32)
    deny_values: list[StrictStr] = Field(default_factory=lambda: ["deny"], min_length=1, max_length=32)
    llm_pattern: str = Field(min_length=1, max_length=4096)
    llm_response: JsonValue

    @model_validator(mode="before")
    @classmethod
    def bounded(cls, value):
        bounded_json(value)
        return value

    @model_validator(mode="after")
    def valid_mapping(self):
        path = PureWindowsPath(self.source)
        if (path.root or path.drive or Path(self.source).is_absolute()
                or ".." in path.parts or ".." in Path(self.source).parts
                or path.suffix != ".py" or ":" in self.source):
            raise ValueError("source must be a relative .py path beneath the binding file")
        if set(self.approve_values) & set(self.deny_values):
            raise ValueError("Approve and deny values must be disjoint")
        if any(type(part) is int and part < 0 for part in self.result_path):
            raise ValueError("Result array indices must be nonnegative")
        return self


def _definition(value: dict) -> dict:
    model = ContractBinding.model_validate(value)
    result = model.model_dump()
    # Preserve the presence of literal:null and avoid serializing absent alternatives.
    result["arguments"] = [arg.model_dump(exclude_unset=True) for arg in model.arguments]
    return result


def _hash(value) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()
    return hashlib.sha256(encoded).hexdigest()


def _source_valid(source: str):
    encoded = source.encode("utf-8")
    if not encoded or len(encoded) > MAX_SOURCE_BYTES:
        raise ValueError("Contract source must contain 1 to 128 KiB of UTF-8")
    first = source.splitlines()[0]
    try:
        header = json.loads(first.removeprefix("#").strip()) if first.startswith("#") else None
    except (ValueError, TypeError) as exc:
        raise ValueError("Contract must start with the supported pinned GenVM runner header") from exc
    if header != {"Depends": f"py-genlayer:{RUNNER_HASH}"}:
        raise ValueError("Contract must start with the supported pinned GenVM runner header")


def _snapshot(definition: dict, source: str) -> dict:
    _source_valid(source)
    source_hash = hashlib.sha256(source.encode("utf-8")).hexdigest()
    return {"definition": definition, "source": source, "source_sha256": source_hash,
            "binding_sha256": _hash({"definition": definition, "source_sha256": source_hash})}


def load_binding(path: Path) -> dict:
    path = Path(path).resolve(strict=True)
    with path.open("rb") as handle:
        raw = handle.read(MAX_DEFINITION_BYTES + 1)
    if len(raw) > MAX_DEFINITION_BYTES:
        raise ValueError("Binding YAML exceeds 64 KiB")
    # Reject aliases entirely: safe_load otherwise accepts recursive alias graphs.
    try:
        depth = 0
        starts = (yaml.tokens.BlockMappingStartToken, yaml.tokens.BlockSequenceStartToken,
                  yaml.tokens.FlowMappingStartToken, yaml.tokens.FlowSequenceStartToken)
        ends = (yaml.tokens.BlockEndToken, yaml.tokens.FlowMappingEndToken, yaml.tokens.FlowSequenceEndToken)
        for token in yaml.scan(raw):
            if isinstance(token, yaml.tokens.AliasToken):
                raise ValueError("YAML aliases are not supported in bindings")
            if isinstance(token, starts):
                depth += 1
            elif isinstance(token, ends):
                depth -= 1
            if depth > 24:
                raise ValueError("Binding YAML exceeds the structural limit")
        data = yaml.safe_load(raw)
    except yaml.YAMLError as exc:
        raise ValueError("Invalid binding YAML") from exc
    definition = _definition(data)
    source_path = (path.parent / definition["source"].replace("\\", "/")).resolve(strict=True)
    if not source_path.is_relative_to(path.parent):
        raise ValueError("Contract source escapes the binding directory")
    with source_path.open("rb") as handle:
        source_bytes = handle.read(MAX_SOURCE_BYTES + 1)
    if len(source_bytes) > MAX_SOURCE_BYTES:
        raise ValueError("Contract source exceeds 128 KiB")
    return _snapshot(definition, source_bytes.decode("utf-8"))


def validate_snapshot(snapshot: dict) -> dict:
    if type(snapshot) is not dict or set(snapshot) != {"definition", "source", "source_sha256", "binding_sha256"}:
        raise ValueError("Invalid binding snapshot")
    if type(snapshot["source"]) is not str:
        raise ValueError("Invalid contract source")
    checked = _snapshot(_definition(snapshot["definition"]), snapshot["source"])
    if checked != snapshot:
        raise ValueError("Binding snapshot hash or canonical definition mismatch")
    return checked


def binding_summary(snapshot: dict) -> dict:
    definition = snapshot["definition"]
    return {**{key: definition[key] for key in ("id", "title", "method")},
            "source_sha256": snapshot["source_sha256"], "binding_sha256": snapshot["binding_sha256"]}


def resolve_arguments(definition: dict, context: dict) -> list:
    return [copy.deepcopy(context[arg["from_field"]] if "from_field" in arg else arg["literal"])
            for arg in definition["arguments"]]


def resolve_template(template, context: dict):
    bounded_json(template)

    def resolve(value):
        if type(value) is str and value.startswith("$") and value[1:] in CONTEXT_FIELDS:
            return copy.deepcopy(context[value[1:]])
        if type(value) is list:
            return [resolve(item) for item in value]
        if type(value) is dict:
            return {key: resolve(item) for key, item in value.items()}
        return value

    return resolve(template)


def extract_verdict(result, definition: dict) -> str:
    for part in definition["result_path"]:
        if type(part) is int and type(result) is list and 0 <= part < len(result):
            result = result[part]
        elif type(part) is str and type(result) is dict and part in result:
            result = result[part]
        else:
            raise ValueError("Contract result does not match result_path")
    if type(result) is not str:
        raise ValueError("Mapped contract verdict must be a string")
    if result in definition["approve_values"]:
        return "approve"
    if result in definition["deny_values"]:
        return "deny"
    raise ValueError("Contract returned an unmapped verdict")
