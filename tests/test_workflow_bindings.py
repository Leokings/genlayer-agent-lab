import copy
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from genlayer_agent_lab.bindings import _snapshot
from genlayer_agent_lab.runtime.pins import GENVM_VERSION, RUNNER_HASH
from genlayer_agent_lab.workflow_bindings import (
    CONTRACT_PATH,
    FIXTURE_PREFIX,
    ResultField,
    WorkflowArgument,
    WorkflowBinding,
    bundled_workflow_snapshot,
    load_workflow_binding,
    resolve_workflow_arguments,
    resolve_workflow_fixture,
    validate_workflow_result,
    validate_workflow_snapshot,
    workflow_binding_summary,
)

ROOT = Path(__file__).parents[1]
EXAMPLE = ROOT / "examples/contracts/service-workflow.yaml"


def sample(tmp_path, **changes):
    definition = bundled_workflow_snapshot()["definition"]
    definition["source"] = "workflow.py"
    definition.update(changes)
    (tmp_path / "workflow.py").write_bytes(CONTRACT_PATH.read_bytes())
    path = tmp_path / "workflow.yaml"
    path.write_text(yaml.safe_dump(definition), encoding="utf-8")
    return path


def state(**changes):
    return {"decision": "partial", "resource_id": "service-001", "policy_version": "v1",
            "evidence": "Delivery receipt", "unit": "test_units", "amount": 100,
            "authorized_amount": 40, "released_amount": 0, "remaining_amount": 100,
            "revision": 1, **changes}


def test_example_matches_bundled_snapshot_and_preserves_structured_partial():
    snapshot = bundled_workflow_snapshot()
    assert load_workflow_binding(EXAMPLE) == snapshot
    assert validate_workflow_result(snapshot, "evaluate", state()) == state()
    assert resolve_workflow_arguments(snapshot, "release", {
        "requested_amount": 40, "resource_id": "service-001", "policy_version": "v1",
        "secret": "must not enter arguments",
    }) == [40, "service-001", "v1"]
    assert snapshot["definition"]["operations"]["get_state"]["readonly"] is True
    assert snapshot["definition"]["operations"]["release"]["readonly"] is False


def test_import_does_not_execute_and_snapshot_is_independent_of_disk(tmp_path):
    path = sample(tmp_path)
    source = path.with_name("workflow.py")
    payload = f'# {{"Depends":"py-genlayer:{RUNNER_HASH}"}}\nraise RuntimeError("MUST NOT RUN")\n'
    source.write_bytes(payload.encode("utf-8"))
    old = load_workflow_binding(path)
    source.write_bytes((payload + "# changed\n").encode("utf-8"))
    assert old["source"] == payload
    assert validate_workflow_snapshot(old) == old
    assert load_workflow_binding(path)["binding_sha256"] != old["binding_sha256"]
    checked = validate_workflow_snapshot(old)
    checked["definition"]["constructor_args"].append("changed")
    assert old["definition"]["constructor_args"] == ["service-001", "v1", 100]


@pytest.mark.parametrize("target", ["source", "method", "readonly", "literal", "shape"])
def test_snapshot_detects_changed_source_method_permission_arguments_or_shape(target):
    changed = bundled_workflow_snapshot()
    operation = changed["definition"]["operations"]["release"]
    if target == "source":
        changed["source"] += "\n# tampered"
    elif target == "method":
        operation["method"] = "transfer_all"
    elif target == "readonly":
        operation["readonly"] = True
    elif target == "literal":
        operation["arguments"][0] = {"literal": 100}
    else:
        operation["result"]["fields"]["authorized_amount"]["maximum"] = 10
    with pytest.raises(ValueError, match="mismatch"):
        validate_workflow_snapshot(changed)


def test_reserved_builtin_cannot_name_different_source_even_with_recomputed_hashes():
    original = bundled_workflow_snapshot()
    changed = _snapshot(original["definition"], original["source"] + "\n# different source")
    with pytest.raises(ValueError, match="packaged contract"):
        validate_workflow_snapshot(changed)


@pytest.mark.parametrize("source", [
    "../escape.py", "nested/../../escape.py", "/tmp/a.py", "C:\\a.py", "C:a.py",
    "\\\\host\\share\\a.py", "file.txt", "bundled:other", "https://example.org/a.py",
])
def test_source_paths_are_confined(tmp_path, source):
    with pytest.raises(ValueError):
        load_workflow_binding(sample(tmp_path, source=source))


def test_symlink_source_escape_is_rejected(tmp_path):
    child = tmp_path / "binding"
    child.mkdir()
    path = sample(child)
    outside = tmp_path / "outside.py"
    outside.write_bytes(CONTRACT_PATH.read_bytes())
    source = path.with_name("workflow.py")
    source.unlink()
    try:
        source.symlink_to(outside)
    except OSError:
        pytest.skip("This platform cannot create test symlinks")
    with pytest.raises(ValueError, match="escapes"):
        load_workflow_binding(path)


def test_yaml_alias_depth_size_and_unpinned_source_are_rejected(tmp_path):
    path = sample(tmp_path)
    path.write_text("loop: &loop [*loop]", encoding="utf-8")
    with pytest.raises(ValueError, match="aliases"):
        load_workflow_binding(path)
    path.write_text("value: " + "[" * 30 + "0" + "]" * 30, encoding="utf-8")
    with pytest.raises(ValueError, match="structural"):
        load_workflow_binding(path)
    path.write_bytes(b"x" * 65537)
    with pytest.raises(ValueError, match="64 KiB"):
        load_workflow_binding(path)
    path = sample(tmp_path)
    path.with_name("workflow.py").write_bytes(b"x" * 131073)
    with pytest.raises(ValueError, match="128 KiB"):
        load_workflow_binding(path)
    path.with_name("workflow.py").write_text('# {"Depends":"py-genlayer:latest"}\n')
    with pytest.raises(ValueError, match="pinned"):
        load_workflow_binding(path)


@pytest.mark.parametrize("argument", [
    {}, {"from_field": None}, {"from_field": "secret"}, {"from_field": "os.environ"},
    {"from_field": "amount", "literal": 1}, {"literal": 1, "eval": "anything"},
])
def test_argument_sources_are_explicit(argument):
    with pytest.raises(ValueError):
        WorkflowArgument.model_validate(argument)
    assert WorkflowArgument.model_validate({"literal": None}).model_fields_set == {"literal"}


@pytest.mark.parametrize("amount", [True, 40.0, "40", 0, -1, 1000001, None])
def test_requested_amount_is_strict_and_bounded(amount):
    with pytest.raises(ValueError, match="integer field"):
        resolve_workflow_arguments(bundled_workflow_snapshot(), "release", {
            "requested_amount": amount, "resource_id": "service-001", "policy_version": "v1",
        })


def test_missing_and_undeclared_operations_fail_without_context_fallback():
    snapshot = bundled_workflow_snapshot()
    with pytest.raises(ValueError, match="not declared"):
        resolve_workflow_arguments(snapshot, "withdraw_all", {})
    with pytest.raises(ValueError, match="Missing workflow field"):
        resolve_workflow_arguments(snapshot, "release", {"amount": 40})


@pytest.mark.parametrize("change", [
    {"authorized_amount": True}, {"authorized_amount": 40.0}, {"authorized_amount": "40"},
    {"authorized_amount": -1}, {"authorized_amount": 1000001},
    {"decision": "needs_more_evidence"}, {"decision": "partial_release"},
    {"unit": "USDC"}, {"evidence": "x" * 16001}, {"extra": "provider-secret"},
])
def test_result_shape_rejects_coercion_unknown_decisions_and_extra_fields(change):
    with pytest.raises(ValueError):
        validate_workflow_result(bundled_workflow_snapshot(), "evaluate", state(**change))


def test_result_returns_detached_copy_without_inventing_business_semantics():
    snapshot = bundled_workflow_snapshot()
    value = state()
    checked = validate_workflow_result(snapshot, "evaluate", value)
    checked["authorized_amount"] = 100
    assert value["authorized_amount"] == 40
    # The binding validates shape; the reference contract and agent policy own
    # cross-field semantics. Never turn an unknown outcome into approve/deny.
    assert "approve_values" not in snapshot["definition"]
    del value["revision"]
    with pytest.raises(ValueError, match="exactly"):
        validate_workflow_result(snapshot, "evaluate", value)


def test_fixture_substitution_is_literal_bounded_and_not_exposed_in_summary():
    snapshot = bundled_workflow_snapshot()
    fixture = resolve_workflow_fixture(snapshot, {"fixture_decision": "partial", "fixture_amount": 40})
    assert fixture == {"prefix": FIXTURE_PREFIX,
                       "response": {"decision": "partial", "authorized_amount": 40}}
    changed = copy.deepcopy(snapshot["definition"])
    changed["llm_response"] = {"test": ["$evidence", "prefix $evidence", "$HOME"]}
    custom = _snapshot(changed, snapshot["source"])
    assert resolve_workflow_fixture(custom, {"evidence": "untrusted text"})["response"] == {
        "test": ["untrusted text", "prefix $evidence", "$HOME"],
    }
    public = json.dumps(workflow_binding_summary(custom))
    for forbidden in ("llm_response", "llm_prefix", "source\"", "$HOME", "constructor_args"):
        assert forbidden not in public
    with pytest.raises(ValueError, match="fixture decision"):
        resolve_workflow_fixture(snapshot, {"fixture_decision": "unknown", "fixture_amount": 40})


@pytest.mark.parametrize("change", [
    {"schema_version": 2}, {"schema_version": True}, {"schema_version": 1.0},
    {"kind": "binary"}, {"operations": {}}, {"unknown": True},
])
def test_unknown_versions_and_unbounded_extension_fields_rejected(change):
    with pytest.raises(ValueError):
        WorkflowBinding.model_validate({**bundled_workflow_snapshot()["definition"], **change})


def test_method_permissions_and_result_specs_are_strict():
    definition = bundled_workflow_snapshot()["definition"]
    duplicate = copy.deepcopy(definition)
    duplicate["operations"]["another"] = duplicate["operations"]["evaluate"]
    with pytest.raises(ValueError, match="only once"):
        WorkflowBinding.model_validate(duplicate)
    for value in ("false", 0, None):
        changed = copy.deepcopy(definition)
        changed["operations"]["release"]["readonly"] = value
        with pytest.raises(ValueError):
            WorkflowBinding.model_validate(changed)
    for field in ({"type": "integer", "enum": ["1"]},
                  {"type": "string", "minimum": 0},
                  {"type": "integer", "minimum": 10, "maximum": 0}):
        with pytest.raises(ValueError):
            ResultField.model_validate(field)


DIRECT_CHECKS = r'''
import json
from pathlib import Path
from genlayer_agent_lab.runtime.artifacts import prepare_sdk
from genlayer_agent_lab.runtime.compat import cleanup_windows_stdin_fix, install_windows_stdin_fix
from genlayer_agent_lab.workflow_bindings import CONTRACT_PATH

prepare_sdk(CONTRACT_PATH)
from glsim.engine import SimEngine
from glsim.state import StateStore

paths, original = install_windows_stdin_fix()
engine = SimEngine(StateStore(seed="workflow-binding-contract-tests"))
engine.vm._strict_mock_mode = True
engine.vm.strict_mocks = True
operator = "0x" + "11" * 20
foreign = "0x" + "22" * 20
checks = {}

def fixture(decision, amount):
    engine.vm.clear_mocks()
    engine.vm.mock_llm(r"^AGENT_LAB_SERVICE_WORKFLOW_V1\n", json.dumps({
        "decision": decision, "authorized_amount": amount,
    }))

def call(address, method, args=None, sender=operator):
    return engine.call_method(address, method, args or [], sender=sender)

def reject(address, method, args, expected, sender=operator):
    before = call(address, "get_state")
    try:
        call(address, method, args, sender)
    except Exception as exc:
        assert expected in str(exc), str(exc)
    else:
        raise AssertionError("Expected contract rejection: " + expected)
    assert call(address, "get_state") == before

engine.activate()
try:
    address, _ = engine.deploy(str(CONTRACT_PATH), ["service-001", "v1", 100], sender=operator)
    initial = call(address, "get_state")
    clean_state = engine.vm.snapshot()
    assert initial["unit"] == "test_units" and initial["decision"] == "pending"
    assert initial["remaining_amount"] == 100 and initial["released_amount"] == 0
    reject(address, "release", [1, "service-001", "v1"], "does not authorize")
    checks["initial_and_pending"] = True
    fixture("partial", 40)
    reject(address, "evaluate", ["receipt", "service-001", "v1"], "not the workflow operator", foreign)
    reject(address, "evaluate", ["receipt", "other", "v1"], "Resource or policy")
    reject(address, "evaluate", ["receipt", "service-001", "v2"], "Resource or policy")
    reject(address, "evaluate", ["", "service-001", "v1"], "Evidence must contain")
    decision = call(address, "evaluate", ["receipt", "service-001", "v1"])
    assert decision["decision"] == "partial" and decision["authorized_amount"] == 40
    assert decision["revision"] == 1 and decision["evidence"] == "receipt"
    checks["partial_and_evidence"] = True
    reject(address, "release", [40, "service-001", "v1"], "not the workflow operator", foreign)
    reject(address, "release", [40, "other", "v1"], "Resource or policy")
    reject(address, "release", [40, "service-001", "v2"], "Resource or policy")
    reject(address, "release", [100, "service-001", "v1"], "exceeds remaining authorization")
    reject(address, "release", [True, "service-001", "v1"], "Invalid requested amount")
    reject(address, "release", [0, "service-001", "v1"], "Invalid requested amount")
    checks["caller_scope_amount_guards"] = True
    partial = call(address, "release", [15, "service-001", "v1"])
    assert partial["released_amount"] == 15 and partial["remaining_amount"] == 85
    final = call(address, "release", [25, "service-001", "v1"])
    assert final["released_amount"] == 40 and final["remaining_amount"] == 60
    assert final["authorized_amount"] == 40
    reject(address, "release", [1, "service-001", "v1"], "exceeds remaining authorization")
    reject(address, "evaluate", ["new evidence", "service-001", "v1"], "Cannot revise after")
    checks["contract_ledger_cumulative_limit"] = True
    # Restore the explicit initial test snapshot between independent cases.
    # SimEngine 0.29.2's cached redeploy path uses an undecorated proxy class.
    engine.vm.revert(clean_state)
    assert call(address, "get_state") == initial
    address2 = address
    fixture("deny", 0)
    denied = call(address2, "evaluate", ["no receipt", "service-001", "v1"])
    assert denied["decision"] == "deny" and denied["authorized_amount"] == 0
    reject(address2, "release", [1, "service-001", "v1"], "does not authorize")
    fixture("approve", 100)
    approved = call(address2, "evaluate", ["new receipt", "service-001", "v1"])
    assert approved["revision"] == 2 and approved["authorized_amount"] == 100
    assert call(address2, "release", [100, "service-001", "v1"])["remaining_amount"] == 0
    checks["denial_and_pre_release_revision"] = True
    engine.vm.revert(clean_state)
    assert call(address, "get_state") == initial
    address3 = address
    for decision, amount in [("approve", 40), ("deny", 40), ("partial", 100),
                            ("partial", 0), ("partial", True), ("unknown", 10)]:
        fixture(decision, amount)
        reject(address3, "evaluate", ["receipt", "service-001", "v1"], "[LLM_ERROR]")
    checks["malformed_fixture_rejected_without_state_change"] = True
finally:
    try:
        engine.deactivate()
    finally:
        cleanup_windows_stdin_fix(paths, original)
print("WORKFLOW_DIRECT_CHECKS=" + json.dumps(checks, sort_keys=True))
'''


@pytest.fixture(scope="module")
def direct_checks():
    archive = Path.home() / ".cache/genlayer-agent-lab/gltest-direct" / f"genvm-universal-{GENVM_VERSION}.tar.xz"
    if not archive.is_file():
        pytest.skip("Direct contract checks require the previously prepared pinned SDK cache")
    env = {key: value for key, value in os.environ.items()
           if key.upper() in {"PATH", "SYSTEMROOT", "WINDIR", "TEMP", "TMP", "HOME", "USERPROFILE",
                              "LOCALAPPDATA", "APPDATA", "COMSPEC", "PATHEXT"}}
    completed = subprocess.run(
        [sys.executable, "-c", DIRECT_CHECKS], cwd=ROOT, env=env,
        capture_output=True, text=True, timeout=60,
    )
    assert completed.returncode == 0, completed.stdout[-3000:] + completed.stderr[-5000:]
    marker = "WORKFLOW_DIRECT_CHECKS="
    lines = [line for line in completed.stdout.splitlines() if line.startswith(marker)]
    assert len(lines) == 1, completed.stdout[-3000:]
    return json.loads(lines[0][len(marker):])


@pytest.mark.runtime
@pytest.mark.parametrize("check", [
    "initial_and_pending", "partial_and_evidence", "caller_scope_amount_guards",
    "contract_ledger_cumulative_limit", "denial_and_pre_release_revision",
    "malformed_fixture_rejected_without_state_change",
])
def test_reference_contract_with_controlled_replies_and_actual_storage(direct_checks, check):
    assert direct_checks[check] is True


def test_pinned_contract_header_is_first_line():
    snapshot = bundled_workflow_snapshot()
    assert json.loads(snapshot["source"].splitlines()[0].removeprefix("#")) == {
        "Depends": f"py-genlayer:{RUNNER_HASH}",
    }
    assert snapshot["source_sha256"] == hashlib.sha256(CONTRACT_PATH.read_bytes()).hexdigest()
