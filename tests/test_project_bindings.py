import base64
import copy
import hashlib
import io
import json
import os
import shutil
import zipfile
from pathlib import Path

import pytest
import yaml

from genlayer_agent_lab.project_bindings import (
    GENLAYER_STD_RUNNER,
    MULTI_RUNNER,
    ProjectBinding,
    deployment_order,
    load_project_binding,
    project_binding_summary,
    project_code,
    resolve_constructor,
    resolve_operation,
    validate_project_result,
    validate_project_snapshot,
    validate_value,
)

EXAMPLE = Path(__file__).parents[1] / "examples/projects/prediction/project.yaml"
ADDRESSES = {"oracle": "0x" + "12" * 20, "recorder": "0x" + "34" * 20}


@pytest.fixture
def snapshot():
    return load_project_binding(EXAMPLE)


def local_project(tmp_path):
    shutil.copytree(EXAMPLE.parent, tmp_path / "project")
    return tmp_path / "project/project.yaml"


def rewrite(path, alter):
    definition = yaml.safe_load(path.read_text(encoding="utf-8"))
    alter(definition)
    path.write_text(yaml.safe_dump(definition), encoding="utf-8")


def test_real_multifile_package_is_reproducible_and_pinned(snapshot, tmp_path):
    clone = local_project(tmp_path)
    os.utime(clone.parent / "oracle/rules.py", (1700000000, 1700000000))
    assert load_project_binding(clone) == snapshot
    code = project_code(snapshot, "oracle")
    with zipfile.ZipFile(io.BytesIO(code)) as archive:
        assert set(archive.namelist()) == {"runner.json", "version", "contract/__init__.py", "contract/rules.py"}
        assert json.loads(archive.read("runner.json")) == {"Depends": MULTI_RUNNER}
        assert archive.read("version") == b"v0.3.0"
        assert b"from .rules import" in archive.read("contract/__init__.py")
        assert b"import hashlib" in archive.read("contract/rules.py")
        assert all(info.compress_type == zipfile.ZIP_STORED for info in archive.infolist())
        assert all(info.date_time == (1980, 1, 1, 0, 0, 0) for info in archive.infolist())
    assert snapshot["definition"]["contracts"]["oracle"]["source_project"]["dependencies"] == [GENLAYER_STD_RUNNER]


def test_constructor_references_are_ordered_and_not_agent_selected(snapshot):
    assert deployment_order(snapshot) == ["oracle", "recorder"]
    assert resolve_constructor(snapshot, "oracle", {}) == ["market-001"]
    assert resolve_constructor(snapshot, "recorder", ADDRESSES) == [ADDRESSES["oracle"], "market-001"]
    with pytest.raises(ValueError, match="deployed address"):
        resolve_constructor(snapshot, "recorder", {})
    with pytest.raises(ValueError, match="deployed address"):
        resolve_constructor(snapshot, "recorder", {"oracle": "http://external.example"})


def test_strict_operation_arguments_and_declared_target(snapshot):
    assert resolve_operation(snapshot, "record", {"expected_revision": 2, "outcome": "no"}, ADDRESSES) == {
        "operation": "record", "contract": "recorder", "address": ADDRESSES["recorder"],
        "method": "record", "readonly": False, "args": [2, "no"],
    }
    for args in ({"expected_revision": True, "outcome": "no"},
                 {"expected_revision": 0, "outcome": "no"},
                 {"expected_revision": 2, "outcome": "approve"},
                 {"expected_revision": 2, "outcome": "no", "address": ADDRESSES["oracle"]}):
        with pytest.raises(ValueError):
            resolve_operation(snapshot, "record", args, ADDRESSES)
    with pytest.raises(ValueError, match="Unknown"):
        resolve_operation(snapshot, "withdraw", {}, ADDRESSES)


@pytest.mark.parametrize("alias", [
    "appeal", "inspect_fees", "inspect_appeal", "read_evidence", "submit_investigation",
])
def test_contract_aliases_cannot_be_intercepted_by_lab_builtins(tmp_path, alias):
    path = local_project(tmp_path)

    def collide(definition):
        definition["operations"][alias] = definition["operations"].pop("record")

    rewrite(path, collide)
    with pytest.raises(ValueError, match="reserved Lab operation"):
        load_project_binding(path)


def test_nested_results_are_preserved_without_binary_projection(snapshot):
    value = {"market_id": "market-001", "outcome": "void", "confidence_bps": 4100,
             "revision": 2, "evidence_hash": "a" * 64}
    assert validate_project_result(snapshot, "resolve", value) == value
    for changes in ({"confidence_bps": False}, {"confidence_bps": 10001},
                    {"decision": "approve"}, {"outcome": "unknown"}):
        with pytest.raises(ValueError):
            validate_project_result(snapshot, "resolve", {**value, **changes})
    with pytest.raises(ValueError, match="exactly"):
        validate_project_result(snapshot, "resolve", {"outcome": "yes"})
    schema = {"type": "object", "fields": {"items": {"type": "array", "max_items": 2,
              "items": {"type": "object", "fields": {"allowed": {"type": "boolean"}}}}}}
    nested = {"items": [{"allowed": True}, {"allowed": False}]}
    copied = validate_value(schema, nested)
    copied["items"][0]["allowed"] = False
    assert nested["items"][0]["allowed"] is True
    with pytest.raises(ValueError, match="item limit"):
        validate_value(schema, {"items": [{"allowed": True}] * 3})


def test_source_import_is_data_only_and_snapshot_survives_disk_change(tmp_path):
    path = local_project(tmp_path)
    source = path.parent / "oracle/rules.py"
    source.write_text("raise RuntimeError('DO NOT EXECUTE')\n", encoding="utf-8")
    snapshot = load_project_binding(path)
    source.unlink()
    assert validate_project_snapshot(snapshot) == snapshot
    assert b"PK" == project_code(snapshot, "oracle")[:2]


@pytest.mark.parametrize("target", ["code", "source", "source_hash", "code_hash", "definition", "order", "extra"])
def test_tampering_is_rejected_at_resolution_boundaries(snapshot, target):
    changed = copy.deepcopy(snapshot)
    artifact = changed["artifacts"]["oracle"]
    if target == "code":
        artifact["code_base64"] = base64.b64encode(b"evil").decode()
    elif target == "source":
        artifact["sources"]["rules.py"]["content"] += "\n# changed\n"
    elif target == "source_hash":
        artifact["sources"]["rules.py"]["sha256"] = "0" * 64
    elif target == "code_hash":
        artifact["code_sha256"] = "0" * 64
    elif target == "definition":
        changed["definition"]["operations"]["record"]["method"] = "withdraw"
    elif target == "order":
        changed["deployment_order"].reverse()
    else:
        artifact["extra"] = "ignored?"
    with pytest.raises(ValueError):
        resolve_operation(changed, "read_oracle", {}, ADDRESSES)


def test_every_source_contributes_to_hash(snapshot, tmp_path):
    path = local_project(tmp_path)
    source = path.parent / "oracle/rules.py"
    source.write_text(source.read_text(encoding="utf-8") + "\n# version 2\n", encoding="utf-8")
    changed = load_project_binding(path)
    assert changed["project_sha256"] != snapshot["project_sha256"]
    assert changed["artifacts"]["oracle"]["code_sha256"] != snapshot["artifacts"]["oracle"]["code_sha256"]
    assert changed["artifacts"]["recorder"] == snapshot["artifacts"]["recorder"]
    checked = validate_project_snapshot(changed)
    checked["definition"]["title"] = "detached"
    assert changed["definition"]["title"] != "detached"


@pytest.mark.parametrize("path", ["../out.py", "/tmp/out.py", "C:/out.py", "foo\\out.py", "./out.py",
                                  "oracle//out.py", "out.py:stream", "CON.py"])
def test_escaping_and_ambiguous_source_names_rejected(tmp_path, path):
    manifest = local_project(tmp_path)
    rewrite(manifest, lambda d: d["contracts"]["oracle"]["source_project"]["files"].append(path))
    with pytest.raises(ValueError):
        load_project_binding(manifest)


def test_symlink_escape_rejected_when_host_supports_symlinks(tmp_path):
    path = local_project(tmp_path)
    source = path.parent / "oracle/rules.py"
    outside = tmp_path / "outside.py"
    outside.write_text("# outside", encoding="utf-8")
    source.unlink()
    try:
        source.symlink_to(outside)
    except OSError:
        pytest.skip("Host does not permit file symlinks")
    with pytest.raises(ValueError, match="escapes"):
        load_project_binding(path)


@pytest.mark.parametrize("alter", [
    lambda d: d["contracts"]["oracle"].update(depends_on=["recorder"]),
    lambda d: d["contracts"]["recorder"].update(depends_on=["missing"]),
    lambda d: d["contracts"]["oracle"]["source_project"].update(runner="py-genlayer-multi:latest"),
    lambda d: d["contracts"]["oracle"]["source_project"].update(dependencies=["requests==2.0"]),
    lambda d: d["contracts"]["oracle"]["source_project"]["files"].append("RULES.py"),
    lambda d: d["state_reads"].update(oracle_state="resolve"),
    lambda d: d["operations"]["record"].update(contract="other"),
    lambda d: d["operations"].update(other=d["operations"]["record"]),
    lambda d: d.update(schema_version=True),
])
def test_invalid_declarations_rejected_before_deployment(tmp_path, alter):
    path = local_project(tmp_path)
    rewrite(path, alter)
    with pytest.raises(ValueError):
        load_project_binding(path)


def test_yaml_aliases_and_duplicate_keys_rejected(tmp_path):
    path = local_project(tmp_path)
    original = path.read_text(encoding="utf-8")
    path.write_text(original + "\nid: overwritten\n", encoding="utf-8")
    with pytest.raises(ValueError, match="unique"):
        load_project_binding(path)
    path.write_text("x: &a []\ny: *a\n", encoding="utf-8")
    with pytest.raises(ValueError, match="aliases"):
        load_project_binding(path)


def test_sizes_are_bounded_before_deployment(tmp_path):
    path = local_project(tmp_path)
    (path.parent / "oracle/rules.py").write_text("#" * 131073, encoding="utf-8")
    with pytest.raises(ValueError, match="128 KiB"):
        load_project_binding(path)
    path.write_text("#" * 65537, encoding="utf-8")
    with pytest.raises(ValueError, match="64 KiB"):
        load_project_binding(path)


def test_context_constructor_requires_declared_typed_value(snapshot):
    model = ProjectBinding.model_validate(snapshot["definition"])
    assert model.contracts["oracle"].constructor_args[0].value.literal == "market-001"
    summary = project_binding_summary(snapshot)
    assert summary["operations"]["record"]["arguments"][0]["type"]["minimum"] == 1
    assert summary["state_reads"] == {"oracle_state": "read_oracle", "record_state": "read_record"}
    assert summary["project_sha256"] == snapshot["project_sha256"]
    assert hashlib.sha256(project_code(snapshot, "oracle")).hexdigest() == snapshot["artifacts"]["oracle"]["code_sha256"]


def test_message_project_has_real_separate_audit_target_and_typed_setup_context():
    snapshot = load_project_binding(EXAMPLE.parent.parent / "prediction-messages/project.yaml")
    addresses = {**ADDRESSES, "audit": "0x" + "56" * 20}
    assert deployment_order(snapshot) == ["audit", "oracle", "recorder"]
    assert resolve_constructor(snapshot, "audit", {}) == ["market-001", False]
    repair = load_project_binding(EXAMPLE.parent.parent / "prediction-messages/project-repair.yaml")
    assert resolve_constructor(repair, "audit", {}) == ["market-001", True]
    assert resolve_constructor(snapshot, "recorder", addresses, {"emission_stage": "finalized"}) == [
        addresses["oracle"], addresses["audit"], "market-001", "finalized",
    ]
    with pytest.raises(ValueError, match="context is missing"):
        resolve_constructor(snapshot, "recorder", addresses)
    with pytest.raises(ValueError, match="string constraints"):
        resolve_constructor(snapshot, "recorder", addresses, {"emission_stage": "accepted"})
    assert b".emit(on=self.emission_stage).append" in project_code(snapshot, "recorder")
    assert resolve_operation(snapshot, "repair_audit", {"revision": 1, "outcome": "yes"}, addresses)["address"] == addresses["audit"]
    # The child-only append method is not exposed as an agent tool.
    with pytest.raises(ValueError, match="Unknown project operation"):
        resolve_operation(snapshot, "append", {"revision": 1, "outcome": "yes"}, addresses)
