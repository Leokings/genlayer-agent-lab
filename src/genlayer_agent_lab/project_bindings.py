"""Confined GenVM source packages and data-only multi-contract operation bindings.

Importing a project reads declared UTF-8 files; it never imports developer code,
installs packages or contacts a chain. Snapshots carry all deployment bytes and
are revalidated at execution boundaries. Supported runner pins are explicit.
"""

import base64
import copy
import hashlib
import io
import json
import re
import zipfile
from pathlib import Path, PurePosixPath
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, JsonValue, model_validator

from .bindings import MAX_DEFINITION_BYTES, MAX_SOURCE_BYTES, bounded_json

SINGLE_RUNNER = "py-genlayer:5jycge4q8k23462jtb0b9fyey1s9qz928sz2nbrd9mg4sxqg2qng"
MULTI_RUNNER = "py-genlayer-multi:faykzar6hr5ehfm07jatm69erx6nv96wmz1ab1dszjthbcgre3m0"
GENVM_EXECUTOR_VERSION = "v0.3.0"
# This package is loaded by the pinned multi runner itself. The manifest records
# a verifiable dependency lock; it does not install host packages or override it.
GENLAYER_STD_RUNNER = "py-lib-genlayer-std:kzr02ndm9et4qkmbqpq5djjt5sme2yt76n7sz1qbzax0knt6mam0"
SUPPORTED_DEPENDENCIES = frozenset({GENLAYER_STD_RUNNER})
MAX_PROJECT_BYTES = 524288
MAX_SNAPSHOT_BYTES = 2097152
NAME_PATTERN = r"^[A-Za-z][A-Za-z0-9_]{0,63}$"
_ADDRESS = re.compile(r"^0x[0-9a-fA-F]{40}$")


def _canonical(value) -> bytes:
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode()


def _hash(value) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


def _name(value: str):
    if not re.fullmatch(NAME_PATTERN, value):
        raise ValueError("Names must be ASCII identifiers of at most 64 characters")


def _relative(path: str) -> str:
    # Reject rather than normalize ambiguous paths, including Windows ADS/drives.
    parsed = PurePosixPath(path)
    if (not path or len(path) > 240 or "\\" in path or ":" in path
            or parsed.is_absolute() or any(p in ("", ".", "..") for p in path.split("/"))
            or not path.isascii() or parsed.suffix != ".py"):
        raise ValueError("Source files must be confined relative POSIX .py paths")
    for component in parsed.parts:
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*\.?[A-Za-z0-9_]*", component):
            raise ValueError("Invalid source module path")
        if component.split(".")[0].upper() in {"CON", "PRN", "AUX", "NUL", "COM1", "LPT1"}:
            raise ValueError("Reserved source module path")
    return path


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)


class ValueType(StrictModel):
    """Small recursive value schema. Every declared object field is required."""

    type: Literal["string", "integer", "boolean", "address", "object", "array", "null"]
    enum: list[str] | None = Field(default=None, min_length=1, max_length=32)
    minimum: int | None = None
    maximum: int | None = None
    max_length: int = Field(default=16000, ge=1, le=16000)
    fields: dict[str, "ValueType"] | None = Field(default=None, min_length=1, max_length=32)
    items: "ValueType | None" = None
    max_items: int = Field(default=64, ge=1, le=256)

    @model_validator(mode="after")
    def consistent(self):
        if self.enum is not None and (
            self.type != "string" or len(self.enum) != len(set(self.enum))
            or any(len(x) > self.max_length for x in self.enum)
        ):
            raise ValueError("enum requires unique bounded strings")
        if (self.minimum is not None or self.maximum is not None) and self.type != "integer":
            raise ValueError("Numeric limits require integer values")
        if self.minimum is not None and self.maximum is not None and self.minimum > self.maximum:
            raise ValueError("minimum exceeds maximum")
        if (self.fields is not None) != (self.type == "object"):
            raise ValueError("Object types require fields; other types cannot declare fields")
        if (self.items is not None) != (self.type == "array"):
            raise ValueError("Array types require items; other types cannot declare items")
        for name in self.fields or {}:
            _name(name)
        return self


def validate_value(shape: ValueType | dict, value, label="value"):
    """Check values strictly; in particular a boolean is not an integer."""
    bounded_json(value)
    shape = shape if isinstance(shape, ValueType) else ValueType.model_validate(shape)
    expected = {"string": str, "integer": int, "boolean": bool, "address": str,
                "object": dict, "array": list, "null": type(None)}[shape.type]
    if type(value) is not expected:
        raise ValueError(f"{label} must be {shape.type}")
    if shape.type in ("string", "address"):
        if len(value) > shape.max_length or (shape.enum is not None and value not in shape.enum):
            raise ValueError(f"{label} violates its string constraints")
        if shape.type == "address" and not _ADDRESS.fullmatch(value):
            raise ValueError(f"{label} must be a 20-byte hexadecimal address")
    if shape.type == "integer":
        if (shape.minimum is not None and value < shape.minimum
                or shape.maximum is not None and value > shape.maximum):
            raise ValueError(f"{label} violates its integer limits")
    if shape.type == "object":
        if set(value) != set(shape.fields):
            raise ValueError(f"{label} must contain exactly its declared fields")
        for name, field in shape.fields.items():
            validate_value(field, value[name], f"{label}.{name}")
    if shape.type == "array":
        if len(value) > shape.max_items:
            raise ValueError(f"{label} exceeds its item limit")
        for item in value:
            validate_value(shape.items, item, f"{label}[]")
    return copy.deepcopy(value)


class ValueSource(StrictModel):
    literal: JsonValue = None
    contract_ref: str | None = Field(default=None, pattern=NAME_PATTERN)
    from_context: str | None = Field(default=None, pattern=NAME_PATTERN)

    @model_validator(mode="after")
    def one_source(self):
        if len(self.model_fields_set) != 1:
            raise ValueError("A constructor value needs exactly one source")
        name = next(iter(self.model_fields_set))
        if name != "literal" and getattr(self, name) is None:
            raise ValueError("A constructor reference cannot be null")
        return self


class ConstructorArgument(StrictModel):
    type: ValueType
    value: ValueSource

    @model_validator(mode="after")
    def typed(self):
        if "literal" in self.value.model_fields_set:
            validate_value(self.type, self.value.literal, "constructor literal")
        if self.value.contract_ref and self.type.type not in ("string", "address"):
            raise ValueError("Contract references need string or address type")
        return self


class SourceProject(StrictModel):
    root: str = Field(default=".", min_length=1, max_length=160)
    entrypoint: str = Field(min_length=1, max_length=240)
    files: list[str] = Field(min_length=1, max_length=32)
    runner: str
    dependencies: list[str] = Field(default_factory=list, max_length=8)

    @model_validator(mode="after")
    def paths_and_pins(self):
        if self.root != ".":
            _relative(self.root + "/__init__.py")
        for path in self.files:
            _relative(path)
        if self.entrypoint not in self.files:
            raise ValueError("Entrypoint must be a declared source file")
        if len({p.casefold() for p in self.files}) != len(self.files):
            raise ValueError("Duplicate or case-colliding source paths")
        if self.runner == SINGLE_RUNNER:
            if len(self.files) != 1 or self.dependencies:
                raise ValueError("Single-file runner permits one file and no additional dependencies")
        elif self.runner == MULTI_RUNNER:
            if self.entrypoint != "__init__.py":
                raise ValueError("Multi-file GenVM packages require __init__.py as the entrypoint")
        else:
            raise ValueError("Runner is not a supported pinned GenVM runner")
        if (len(set(self.dependencies)) != len(self.dependencies)
                or any(dep not in SUPPORTED_DEPENDENCIES for dep in self.dependencies)):
            raise ValueError("Dependencies must be unique supported pinned runner dependencies")
        return self


class ProjectContract(StrictModel):
    source_project: SourceProject
    constructor_args: list[ConstructorArgument] = Field(default_factory=list, max_length=32)
    depends_on: list[str] = Field(default_factory=list, max_length=8)


class OperationArgument(StrictModel):
    name: str = Field(pattern=NAME_PATTERN)
    type: ValueType


class ProjectOperation(StrictModel):
    contract: str = Field(pattern=NAME_PATTERN)
    method: str = Field(pattern=NAME_PATTERN)
    readonly: bool
    arguments: list[OperationArgument] = Field(default_factory=list, max_length=32)
    result: ValueType

    @model_validator(mode="after")
    def unique_arguments(self):
        if len({a.name for a in self.arguments}) != len(self.arguments):
            raise ValueError("Operation argument names must be unique")
        return self


class ProjectBinding(StrictModel):
    schema_version: Literal[2] = 2
    kind: Literal["project"] = "project"
    id: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]{0,95}$")
    title: str = Field(min_length=1, max_length=160)
    contracts: dict[str, ProjectContract] = Field(min_length=1, max_length=8)
    operations: dict[str, ProjectOperation] = Field(min_length=1, max_length=64)
    state_reads: dict[str, str] = Field(min_length=1, max_length=32)

    @model_validator(mode="before")
    @classmethod
    def bounded(cls, value):
        bounded_json(value)
        if type(value) is dict and type(value.get("schema_version", 2)) is not int:
            raise ValueError("schema_version must be an integer")
        return value

    @model_validator(mode="after")
    def references(self):
        for name in (*self.contracts, *self.operations, *self.state_reads):
            _name(name)
        for alias, contract in self.contracts.items():
            refs = [a.value.contract_ref for a in contract.constructor_args if a.value.contract_ref]
            for dependency in (*refs, *contract.depends_on):
                if dependency not in self.contracts or dependency == alias:
                    raise ValueError("Invalid contract deployment dependency")
            if len(set(contract.depends_on)) != len(contract.depends_on):
                raise ValueError("Duplicate deployment dependencies")
        methods = set()
        for operation in self.operations.values():
            if operation.contract not in self.contracts:
                raise ValueError("Operation refers to an unknown contract")
            key = (operation.contract, operation.method)
            if key in methods:
                raise ValueError("A deployed contract method must have only one operation declaration")
            methods.add(key)
        for operation in self.state_reads.values():
            if operation not in self.operations or not self.operations[operation].readonly:
                raise ValueError("State reads must reference declared readonly operations")
            if self.operations[operation].arguments:
                raise ValueError("State reads must have no required arguments")
        _deployment_order(self)
        return self


def _definition(value) -> dict:
    model = ProjectBinding.model_validate(value)
    result = model.model_dump()
    for alias, contract in model.contracts.items():
        for index, argument in enumerate(contract.constructor_args):
            result["contracts"][alias]["constructor_args"][index]["value"] = (
                argument.value.model_dump(exclude_unset=True)
            )
    return result


def _deployment_order(model: ProjectBinding) -> list[str]:
    visited, active, order = set(), set(), []

    def visit(alias):
        if alias in active:
            raise ValueError("Cyclic contract deployment dependency")
        if alias in visited:
            return
        active.add(alias)
        contract = model.contracts[alias]
        refs = {a.value.contract_ref for a in contract.constructor_args if a.value.contract_ref}
        for dependency in sorted(refs | set(contract.depends_on)):
            visit(dependency)
        active.remove(alias)
        visited.add(alias)
        order.append(alias)

    for alias in sorted(model.contracts):
        visit(alias)
    return order


def _runner_config(project: SourceProject):
    # Supported library dependencies are already in this runner's immutable
    # dependency graph. Loading them again is unnecessary.
    return {"Depends": project.runner}


def _package(project: SourceProject, sources: dict[str, str]) -> bytes:
    if set(sources) != set(project.files):
        raise ValueError("Source contents do not match the project manifest")
    for source in sources.values():
        if type(source) is not str or not source or len(source.encode()) > MAX_SOURCE_BYTES:
            raise ValueError("Source files must contain 1 to 128 KiB of UTF-8")
        if "\x00" in source:
            raise ValueError("Source files cannot contain NUL bytes")
    entry = sources[project.entrypoint]
    try:
        lines = entry.splitlines()
        if lines[0] != f"# {GENVM_EXECUTOR_VERSION}":
            raise ValueError("Entrypoint requires the supported executor version header")
        line = lines[1]
        header = json.loads(line[1:].strip()) if line.startswith("#") else None
    except (ValueError, IndexError) as exc:
        raise ValueError("Entrypoint requires a pinned runner header") from exc
    if header != {"Depends": project.runner}:
        raise ValueError("Entrypoint header must match its declared pinned runner")
    if project.runner == SINGLE_RUNNER:
        return entry.encode()
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", compression=zipfile.ZIP_STORED) as archive:
        entries = {"runner.json": _canonical(_runner_config(project)),
                   "version": GENVM_EXECUTOR_VERSION.encode()}
        entries.update({"contract/" + path: source.encode() for path, source in sources.items()})
        for name, data in sorted(entries.items()):
            info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            info.create_system = 3
            info.external_attr = 0o100644 << 16
            archive.writestr(info, data)
    return stream.getvalue()


def _snapshot(definition: dict, source_sets: dict) -> dict:
    model = ProjectBinding.model_validate(definition)
    if set(source_sets) != set(model.contracts):
        raise ValueError("Snapshot contracts do not match the project definition")
    artifacts, total = {}, 0
    for alias, contract in model.contracts.items():
        sources = source_sets[alias]
        code = _package(contract.source_project, sources)
        total += sum(len(source.encode()) for source in sources.values())
        if total > MAX_PROJECT_BYTES:
            raise ValueError("Project sources exceed 512 KiB")
        artifacts[alias] = {
            "code_base64": base64.b64encode(code).decode(),
            "code_sha256": hashlib.sha256(code).hexdigest(),
            "sources": {name: {"content": content, "sha256": hashlib.sha256(content.encode()).hexdigest()}
                        for name, content in sorted(sources.items())},
        }
    result = {"definition": definition, "artifacts": artifacts,
              "deployment_order": _deployment_order(model)}
    result["project_sha256"] = _hash(result)
    if len(_canonical(result)) > MAX_SNAPSHOT_BYTES:
        raise ValueError("Project snapshot exceeds 2 MiB")
    return result


class _UniqueLoader(yaml.SafeLoader):
    pass


def _unique_mapping(loader, node, deep=False):
    mapping = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if type(key) is not str or key in mapping:
            raise ValueError("YAML mapping keys must be unique strings")
        mapping[key] = loader.construct_object(value_node, deep=deep)
    return mapping


_UniqueLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _unique_mapping)


def load_project_binding(path: Path) -> dict:
    """Read a confined manifest and build deterministic deployment artifacts."""
    path = Path(path).resolve(strict=True)
    with path.open("rb") as stream:
        raw = stream.read(MAX_DEFINITION_BYTES + 1)
    if len(raw) > MAX_DEFINITION_BYTES:
        raise ValueError("Project manifest exceeds 64 KiB")
    try:
        depth = 0
        for token in yaml.scan(raw):
            if isinstance(token, yaml.tokens.AliasToken):
                raise ValueError("YAML aliases are not supported")
            if isinstance(token, (yaml.tokens.BlockMappingStartToken, yaml.tokens.BlockSequenceStartToken,
                                  yaml.tokens.FlowMappingStartToken, yaml.tokens.FlowSequenceStartToken)):
                depth += 1
            if isinstance(token, (yaml.tokens.BlockEndToken, yaml.tokens.FlowMappingEndToken,
                                  yaml.tokens.FlowSequenceEndToken)):
                depth -= 1
            if depth > 20:
                raise ValueError("Manifest exceeds its structural limit")
        definition = _definition(yaml.load(raw, Loader=_UniqueLoader))
    except (yaml.YAMLError, RecursionError) as exc:
        raise ValueError("Invalid project manifest") from exc
    source_sets = {}
    for alias, contract in definition["contracts"].items():
        project = contract["source_project"]
        root = (path.parent / project["root"]).resolve(strict=True)
        if not root.is_relative_to(path.parent):
            raise ValueError("Source root escapes the project directory")
        sources = {}
        for name in project["files"]:
            target = (root / name).resolve(strict=True)
            if not target.is_relative_to(root):
                raise ValueError("Source file escapes the source project directory")
            with target.open("rb") as stream:
                raw = stream.read(MAX_SOURCE_BYTES + 1)
            if len(raw) > MAX_SOURCE_BYTES:
                raise ValueError("Source file exceeds 128 KiB")
            sources[name] = raw.decode("utf-8")
        source_sets[alias] = sources
    return _snapshot(definition, source_sets)


def validate_project_snapshot(snapshot: dict) -> dict:
    """Return a canonical detached copy, rejecting tampered artifacts or hashes."""
    if type(snapshot) is not dict or set(snapshot) != {
        "definition", "artifacts", "deployment_order", "project_sha256"
    }:
        raise ValueError("Invalid project snapshot")
    try:
        if len(_canonical(snapshot)) > MAX_SNAPSHOT_BYTES:
            raise ValueError("Project snapshot exceeds 2 MiB")
        definition = _definition(snapshot["definition"])
        sources = {alias: {name: file["content"] for name, file in artifact["sources"].items()}
                   for alias, artifact in snapshot["artifacts"].items()}
        checked = _snapshot(definition, sources)
    except (KeyError, TypeError, AttributeError, RecursionError) as exc:
        raise ValueError("Invalid project snapshot structure") from exc
    if checked != snapshot:
        raise ValueError("Project snapshot hashes, artifacts or canonical definition do not match")
    return checked


def deployment_order(snapshot: dict) -> list[str]:
    return validate_project_snapshot(snapshot)["deployment_order"]


def project_code(snapshot: dict, contract_alias: str) -> bytes:
    checked = validate_project_snapshot(snapshot)
    if contract_alias not in checked["artifacts"]:
        raise ValueError("Unknown project contract")
    return base64.b64decode(checked["artifacts"][contract_alias]["code_base64"], validate=True)


def _address(addresses: dict, alias: str) -> str:
    address = addresses.get(alias)
    if type(address) is not str or not _ADDRESS.fullmatch(address):
        raise ValueError(f"Missing or invalid deployed address for {alias}")
    return address


def resolve_constructor(snapshot: dict, alias: str, addresses: dict, context: dict | None = None) -> list:
    checked = validate_project_snapshot(snapshot)
    if alias not in checked["definition"]["contracts"]:
        raise ValueError("Unknown project contract")
    contract = checked["definition"]["contracts"][alias]
    for dependency in contract["depends_on"]:
        _address(addresses, dependency)
    values = []
    for argument in contract["constructor_args"]:
        source = argument["value"]
        if "contract_ref" in source:
            value = _address(addresses, source["contract_ref"])
        elif "from_context" in source:
            if context is None or source["from_context"] not in context:
                raise ValueError("Required constructor context is missing")
            value = context[source["from_context"]]
        else:
            value = source["literal"]
        values.append(validate_value(argument["type"], value, "constructor argument"))
    return values


def resolve_operation(snapshot: dict, alias: str, arguments: dict, addresses: dict) -> dict:
    checked = validate_project_snapshot(snapshot)
    operation = checked["definition"]["operations"].get(alias)
    if operation is None:
        raise ValueError("Unknown project operation")
    normalized = normalize_project_arguments(checked, alias, arguments)
    args = [normalized[a["name"]] for a in operation["arguments"]]
    return {"operation": alias, "contract": operation["contract"],
            "address": _address(addresses, operation["contract"]), "method": operation["method"],
            "readonly": operation["readonly"], "args": args}


def normalize_project_arguments(snapshot: dict, alias: str, arguments: dict) -> dict:
    """Accept exact decimals only at explicitly declared integer argument slots.

    The runtime uses this before computing idempotency fingerprints and checking
    policy, so integer 42 and decimal "42" identify the same typed operation.
    Ordinary strings, booleans, floats and backend results are not coerced.
    """
    from .project_wire import INTEGER_TAG, decimal_integer

    checked = validate_project_snapshot(snapshot)
    operation = checked["definition"]["operations"].get(alias)
    if operation is None:
        raise ValueError("Unknown project operation")
    if type(arguments) is not dict or set(arguments) != {a["name"] for a in operation["arguments"]}:
        raise ValueError("Operation arguments must exactly match the declared names")

    def normalize(shape, value):
        if shape["type"] == "integer":
            if type(value) is str:
                return decimal_integer(value)
            if type(value) is dict and set(value) == {INTEGER_TAG}:
                return decimal_integer(value[INTEGER_TAG])
        if shape["type"] == "object" and type(value) is dict:
            return {key: normalize(shape["fields"][key], child) if key in shape["fields"] else child
                    for key, child in value.items()}
        if shape["type"] == "array" and type(value) is list:
            return [normalize(shape["items"], child) for child in value]
        return value

    return {argument["name"]: validate_value(
        argument["type"], normalize(argument["type"], arguments[argument["name"]]), argument["name"])
        for argument in operation["arguments"]}


def validate_project_result(snapshot: dict, alias: str, value):
    checked = validate_project_snapshot(snapshot)
    operation = checked["definition"]["operations"].get(alias)
    if operation is None:
        raise ValueError("Unknown project operation")
    return validate_value(operation["result"], value, f"result of {alias}")


def project_binding_summary(snapshot: dict) -> dict:
    checked = validate_project_snapshot(snapshot)
    definition = checked["definition"]
    return {"schema_version": 2, "kind": "project", "id": definition["id"],
            "title": definition["title"], "project_sha256": checked["project_sha256"],
            "contracts": list(checked["deployment_order"]),
            "operations": copy.deepcopy(definition["operations"]),
            "state_reads": copy.deepcopy(definition["state_reads"]),
            "source_bytes": sum(len(file["content"].encode()) for artifact in checked["artifacts"].values()
                                for file in artifact["sources"].values())}
