import copy
import json
import sqlite3
import threading
import time
from pathlib import Path

import pytest
import yaml

from genlayer_agent_lab.bindings import (
    Argument,
    ContractBinding,
    bounded_json,
    extract_verdict,
    load_binding,
    resolve_arguments,
    resolve_template,
    validate_snapshot,
)
from genlayer_agent_lab.engine import Engine
from genlayer_agent_lab.runtime.pins import RUNNER_HASH
from genlayer_agent_lab.store import Store

EXAMPLE = Path(__file__).parents[1] / "examples/contracts/delivery-binding.yaml"


def sample(tmp_path, **changes):
    definition = yaml.safe_load(EXAMPLE.read_text())
    definition.update(changes)
    (tmp_path / "delivery.py").write_bytes(EXAMPLE.with_name("delivery.py").read_bytes())
    path = tmp_path / "binding.yaml"
    path.write_text(yaml.safe_dump(definition))
    return path


def wait(engine, run_id, state="completed"):
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        if engine.get_run(run_id)["status"] == state:
            return engine.report(run_id)
        time.sleep(.01)
    raise AssertionError(engine.get_run(run_id))


def test_import_never_executes_source_and_snapshots_bytes(tmp_path):
    path = sample(tmp_path)
    source = path.with_name("delivery.py")
    payload = f'# {{"Depends":"py-genlayer:{RUNNER_HASH}"}}\nraise RuntimeError("MUST NOT EXECUTE")\n'
    source.write_bytes(payload.encode())
    snapshot = load_binding(path)
    assert snapshot["source"] == payload
    assert validate_snapshot(snapshot) == snapshot
    tampered = copy.deepcopy(snapshot)
    tampered["source"] += "# changed"
    with pytest.raises(ValueError, match="mismatch"):
        validate_snapshot(tampered)


@pytest.mark.parametrize("source", ["../escape.py", "/tmp/escape.py", "C:\\escape.py", "C:escape.py",
                                         "\\\\server\\escape.py", "nested/../../escape.py", "file.txt"])
def test_source_paths_are_confined(tmp_path, source):
    with pytest.raises(ValueError):
        load_binding(sample(tmp_path, source=source))


def test_symlink_escape_is_rejected(tmp_path):
    child = tmp_path / "adapter"
    child.mkdir()
    path = sample(child)
    outside = tmp_path / "outside.py"
    outside.write_bytes(path.with_name("delivery.py").read_bytes())
    path.with_name("delivery.py").unlink()
    try:
        path.with_name("delivery.py").symlink_to(outside)
    except OSError:
        pytest.skip("OS does not permit symlink creation")
    with pytest.raises(ValueError, match="escapes"):
        load_binding(path)


def test_size_and_runner_validation(tmp_path):
    path = sample(tmp_path)
    path.with_name("delivery.py").write_bytes(b"x" * 131073)
    with pytest.raises(ValueError, match="128 KiB"):
        load_binding(path)
    path.with_name("delivery.py").write_text('# {"Depends": "py-genlayer:latest"}\n')
    with pytest.raises(ValueError, match="pinned"):
        load_binding(path)
    path.write_bytes(b"x" * 65537)
    with pytest.raises(ValueError, match="64 KiB"):
        load_binding(path)


def test_aliases_deep_and_non_json_inputs_are_rejected(tmp_path):
    path = tmp_path / "binding.yaml"
    path.write_text("loop: &loop [*loop]")
    with pytest.raises(ValueError, match="aliases"):
        load_binding(path)
    cycle = []
    cycle.append(cycle)
    with pytest.raises(ValueError, match="Recursive"):
        bounded_json(cycle)
    nested = "value"
    for _ in range(30):
        nested = [nested]
    with pytest.raises(ValueError, match="structural"):
        bounded_json(nested)
    for value in [{1: "key"}, float("nan"), {"date": object()}]:
        with pytest.raises(ValueError):
            bounded_json(value)


def test_argument_presence_and_exact_substitution():
    assert Argument.model_validate({"literal": None}).model_fields_set == {"literal"}
    for value in [{}, {"from_field": None}, {"literal": None, "from_field": "evidence"}, {"from_field": "secrets"}]:
        with pytest.raises(ValueError):
            Argument.model_validate(value)
    context = {"amount": 123, "evidence": "untrusted"}
    assert resolve_arguments({"arguments": [{"literal": None}, {"from_field": "amount"}]}, context) == [None, 123]
    assert resolve_template({"x": ["$amount", "prefix $evidence", "$HOME"]}, context) == {"x": [123, "prefix $evidence", "$HOME"]}


def test_result_mapping_is_explicit_and_strict():
    definition = load_binding(EXAMPLE)["definition"]
    assert extract_verdict({"assessment": {"outcome": "approve"}}, definition) == "approve"
    for value in [True, "unknown", "APPROVE", 1, None]:
        with pytest.raises(ValueError):
            extract_verdict({"assessment": {"outcome": value}}, definition)
    for change in [{"deny_values": ["approve"]}, {"result_path": [True]}, {"result_path": [-1]}, {"method": "_private"}, {"unknown": 2}]:
        with pytest.raises(ValueError):
            ContractBinding.model_validate({**definition, **change})


def test_custom_run_preserves_snapshot_and_separate_grades(tmp_path):
    path = sample(tmp_path)
    ready, release = threading.Event(), threading.Event()
    seen = []

    def evaluator(snapshot, context, **kwargs):
        seen.append(snapshot)
        ready.set()
        assert release.wait(5)
        return {"verdict": "deny", "provenance": {"backend": "test-double"}}

    engine = Engine(tmp_path / "data", binding_evaluator=evaluator)
    try:
        old = engine.import_binding(path)
        created = engine.create_run("escrow-normal", "safe", "container-glsim", old["id"])
        assert ready.wait(5)
        path.with_name("delivery.py").write_bytes(path.with_name("delivery.py").read_bytes() + b"\n# changed")
        new = engine.import_binding(path)
        assert old["binding_sha256"] != new["binding_sha256"]
        release.set()
        report = wait(engine, created["run_id"])
        assert report["verdict"] == "fail"
        assert report["grades"]["decision"]["status"] == "fail"
        assert report["grades"]["behavior"]["status"] == "pass"
        assert report["manifest"]["binding"]["binding_sha256"] == old["binding_sha256"]
        assert seen[0]["binding_sha256"] == old["binding_sha256"]
        assert "source" not in report["manifest"]["binding"]
        assert "llm_response" not in json.dumps(engine.get_run(created["run_id"]))
        assert "llm_response" not in json.dumps(engine.list_bindings())
    finally:
        release.set()
        engine.close()


def test_binding_backend_requirements_and_fail_closed(tmp_path):
    def unavailable(*args, **kwargs):
        raise RuntimeError("sensitive error text")

    def forbidden(*args, **kwargs):
        pytest.fail("Custom contracts must not fall back to native execution")

    engine = Engine(tmp_path, evaluator=forbidden, binding_evaluator=unavailable)
    try:
        engine.import_binding(EXAMPLE)
        for backend, binding_id in [("glsim", "delivery-assessment"), ("fixture", "delivery-assessment"), ("container-glsim", None)]:
            with pytest.raises(ValueError):
                engine.create_run("escrow-normal", backend=backend, binding_id=binding_id)
        with pytest.raises(KeyError):
            engine.create_run("escrow-normal", backend="container-glsim", binding_id="missing")
        result = engine.create_run("escrow-normal", "safe", "container-glsim", "delivery-assessment")
        report = wait(engine, result["run_id"], "inconclusive")
        assert "worker doctor" in str(report["findings"])
        assert "sensitive error text" not in str(report)
        assert not report["world"]["effects"]
    finally:
        engine.close()


def test_cancel_signals_preparing_container(tmp_path):
    started, stopped = threading.Event(), threading.Event()

    def evaluator(snapshot, context, timeout, cancel_event):
        started.set()
        assert cancel_event.wait(5)
        stopped.set()
        raise RuntimeError("cancelled")

    engine = Engine(tmp_path, binding_evaluator=evaluator)
    try:
        engine.import_binding(EXAMPLE)
        created = engine.create_run("escrow-normal", "safe", "container-glsim", "delivery-assessment")
        assert started.wait(5)
        engine.cancel_run(created["run_id"])
        assert stopped.wait(5)
        assert engine.report(created["run_id"])["status"] == "cancelled"
    finally:
        engine.close()


def test_schema1_migration_backups_history_and_reopens_bindings(tmp_path):
    path = tmp_path / "lab.sqlite3"
    db = sqlite3.connect(path)
    db.execute("CREATE TABLE runs (id TEXT PRIMARY KEY, body TEXT NOT NULL)")
    db.execute("CREATE TABLE scenarios (id TEXT PRIMARY KEY, body TEXT NOT NULL)")
    db.execute("INSERT INTO runs VALUES ('history', '{\"frozen\": true}')")
    db.execute("PRAGMA user_version = 1")
    db.commit()
    db.close()
    store = Store(tmp_path)
    try:
        assert store.records() == [{"frozen": True}]
        store.save_binding(load_binding(EXAMPLE))
        assert len(store.bindings()) == 1
        with store.connection() as connection:
            assert connection.execute("PRAGMA user_version").fetchone()[0] == 2
    finally:
        store.close()
    backups = list(tmp_path.glob("lab.schema1.*.backup.sqlite3"))
    assert len(backups) == 1
    backup = sqlite3.connect(backups[0])
    try:
        assert backup.execute("PRAGMA user_version").fetchone()[0] == 1
        assert backup.execute("SELECT body FROM runs").fetchone()[0] == '{"frozen": true}'
    finally:
        backup.close()
    reopened = Store(tmp_path)
    try:
        assert len(reopened.bindings()) == 1
        assert len(list(tmp_path.glob("*.backup.sqlite3"))) == 1
    finally:
        reopened.close()


def test_invalid_stored_binding_releases_startup_lock(tmp_path):
    store = Store(tmp_path)
    snapshot = load_binding(EXAMPLE)
    snapshot["source"] += "# tampered"
    store.save_binding(snapshot)
    store.close()
    with pytest.raises(ValueError, match="mismatch") as caught:
        Engine(tmp_path)
    # Keep the traceback alive while opening the repaired installation.
    assert caught.value.__traceback__ is not None
    repaired = Store(tmp_path)
    repaired.save_binding(load_binding(EXAMPLE))
    repaired.close()
    engine = Engine(tmp_path)
    assert engine.list_bindings()[0]["id"] == "delivery-assessment"
    engine.close()
