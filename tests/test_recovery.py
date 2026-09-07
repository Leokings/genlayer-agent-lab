import copy
import hashlib
import json
import os
import sqlite3
import stat
import time
import zipfile
from pathlib import Path

import pytest

from genlayer_agent_lab.api import read_admin_token
from genlayer_agent_lab.engine import Engine
from genlayer_agent_lab.recovery import backup, restore
from genlayer_agent_lab.store import Store

EXAMPLE = Path(__file__).parents[1] / "examples/contracts/delivery-binding.yaml"


def _database(root, version=2, records=()):
    root.mkdir()
    with sqlite3.connect(root / "lab.sqlite3") as db:
        for table in ("runs", "scenarios", *(["bindings"] if version == 2 else [])):
            db.execute(f"CREATE TABLE {table} (id TEXT PRIMARY KEY, body TEXT NOT NULL)")
        for record in records:
            db.execute("INSERT INTO runs VALUES (?, ?)", (record["run_id"], json.dumps(record)))
        db.execute(f"PRAGMA user_version = {version}")
    (root / "admin.token").write_text("old-administrator-secret", encoding="utf-8")


def _complete(engine, run_id):
    deadline = time.monotonic() + 3
    while engine.get_run(run_id)["status"] != "completed":
        assert time.monotonic() < deadline
        time.sleep(.01)
    return engine.report(run_id)


def _rewrite(source, destination, mutate_manifest=None, mutate_database=None, extra=None):
    with zipfile.ZipFile(source) as archive:
        manifest = json.loads(archive.read("manifest.json"))
        database = archive.read("lab.sqlite3")
    if mutate_database:
        database = mutate_database(database)
    if mutate_manifest:
        mutate_manifest(manifest)
    with zipfile.ZipFile(destination, "w") as archive:
        archive.writestr("manifest.json", json.dumps(manifest))
        archive.writestr("lab.sqlite3", database)
        if extra:
            archive.writestr(*extra)


def test_prior_schema_upgrade_recovery_preserves_reports_and_rotates_access(tmp_path):
    seed = Engine(tmp_path / "seed")
    try:
        created = seed.create_run("escrow-normal", "safe", "fixture")
        original_report = _complete(seed, created["run_id"])
        prior = copy.deepcopy(seed.store.records()[0])
    finally:
        seed.close()
    # A real finalized run encoded in the prior two-table schema. Its report
    # keeps the originating release identity across the migration and restore.
    prior.pop("binding")
    prior["toolkit_version"] = "0.1.0a1"
    prior["final_report"]["manifest"]["toolkit_version"] = "0.1.0a1"
    original_report = copy.deepcopy(prior["final_report"])
    source = tmp_path / "prior-release"
    _database(source, version=1, records=[prior])
    archive1 = tmp_path / "before-upgrade.zip"
    result = backup(source, archive1)
    assert result["manifest"]["database"]["schema_version"] == 1
    with sqlite3.connect(source / "lab.sqlite3") as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 1
    assert not list(source.glob("lab.schema1.*.backup.sqlite3"))

    upgraded = Engine(source)
    try:
        assert upgraded.report(created["run_id"]) == original_report
        binding = upgraded.import_binding(EXAMPLE)
        assert upgraded.authenticate_agent(created["run_id"], created["agent_token"])
    finally:
        upgraded.close()
    automatic = list(source.glob("lab.schema1.*.backup.sqlite3"))
    assert len(automatic) == 1
    with sqlite3.connect(automatic[0]) as old:
        assert old.execute("PRAGMA user_version").fetchone()[0] == 1
        assert json.loads(old.execute("SELECT body FROM runs").fetchone()[0])["final_report"] == original_report

    # A pre-upgrade recovery archive remains schema 1 until a release opens it.
    rolled_back = tmp_path / "restored-prior"
    restored = restore(archive1, rolled_back)
    assert restored["schema_version"] == 1 and restored["migration_on_next_start"] is True
    assert restored["agent_tokens_revoked"] == 1 and restored["admin_token_rotated"] is True
    assert "token" not in json.dumps(restored).replace("admin_token_rotated", "").replace("agent_tokens_revoked", "")
    with sqlite3.connect(rolled_back / "lab.sqlite3") as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 1
    reopened = Engine(rolled_back)
    try:
        assert reopened.report(created["run_id"]) == original_report
        assert not reopened.authenticate_agent(created["run_id"], created["agent_token"])
        assert reopened.store.records()[0]["agent_hash"] != prior["agent_hash"]
    finally:
        reopened.close()
    assert read_admin_token(rolled_back) != read_admin_token(source)

    # Post-upgrade backup/restore also retains imported custom-contract snapshots.
    archive2 = tmp_path / "after-upgrade.zip"
    backup(source, archive2)
    after = tmp_path / "restored-current"
    assert restore(archive2, after)["schema_version"] == 2
    recovered = Engine(after)
    try:
        assert recovered.report(created["run_id"]) == original_report
        assert recovered.list_bindings() == [binding]
        assert not recovered.authenticate_agent(created["run_id"], created["agent_token"])
    finally:
        recovered.close()


def test_backup_includes_committed_wal_and_only_the_database(tmp_path):
    source = tmp_path / "source"
    _database(source)
    (source / "private-provider.env").write_text("provider-secret", encoding="utf-8")
    (source / "studio").mkdir()
    (source / "studio/account.key").write_text("studio-secret", encoding="utf-8")
    connection = sqlite3.connect(source / "lab.sqlite3")
    try:
        connection.execute("PRAGMA journal_mode = WAL")
        connection.execute("PRAGMA wal_autocheckpoint = 0")
        connection.execute("INSERT INTO scenarios VALUES ('wal-record', '{\"id\":\"wal-record\"}')")
        connection.commit()
        assert (source / "lab.sqlite3-wal").stat().st_size > 0
        with sqlite3.connect((source / "lab.sqlite3").as_uri() + "?immutable=1", uri=True) as raw:
            assert raw.execute("SELECT count(*) FROM scenarios").fetchone()[0] == 0
        archive_path = tmp_path / "wal.zip"
        backup(source, archive_path)
    finally:
        connection.close()
    with zipfile.ZipFile(archive_path) as archive:
        assert set(archive.namelist()) == {"manifest.json", "lab.sqlite3"}
        assert b"provider-secret" not in archive.read("lab.sqlite3")
        assert b"old-administrator-secret" not in archive.read("lab.sqlite3")
    destination = tmp_path / "restored"
    result = restore(archive_path, destination)
    assert result["counts"]["scenarios"] == 1 and result["studio_restored"] is False
    with sqlite3.connect(destination / "lab.sqlite3") as db:
        assert db.execute("SELECT id FROM scenarios").fetchall() == [("wal-record",)]
        assert db.execute("PRAGMA journal_mode").fetchone()[0] == "delete"
    assert not (destination / "studio").exists()


def test_backup_requires_exclusive_owner_and_never_overwrites(tmp_path):
    store = Store(tmp_path / "live")
    output = tmp_path / "snapshot.zip"
    try:
        with pytest.raises(RuntimeError, match="Stop the Lab"):
            backup(store.root, output)
        assert not output.exists()
    finally:
        store.close()
    backup(store.root, output)
    original = output.read_bytes()
    with pytest.raises(ValueError, match="new file"):
        backup(store.root, output)
    assert output.read_bytes() == original


@pytest.mark.parametrize("occupied", [False, True])
def test_restore_rejects_even_empty_existing_destinations(tmp_path, occupied):
    source = tmp_path / "source"
    _database(source)
    archive = tmp_path / "backup.zip"
    backup(source, archive)
    destination = tmp_path / "existing"
    destination.mkdir()
    if occupied:
        (destination / "admin.token").write_text("keep-this-secret", encoding="utf-8")
    with pytest.raises(ValueError, match="nonexistent"):
        restore(archive, destination)
    assert destination.is_dir()
    if occupied:
        assert (destination / "admin.token").read_text() == "keep-this-secret"


@pytest.mark.parametrize("fault", ["checksum", "version", "schema", "counts", "corrupt_database", "traversal", "duplicate"])
def test_invalid_archive_rejected_before_creating_destination(tmp_path, fault):
    source = tmp_path / "source"
    _database(source)
    original, broken = tmp_path / "good.zip", tmp_path / "bad.zip"
    backup(source, original)
    manifest_change = None
    database_change = None
    extra = None
    if fault == "checksum":
        def manifest_change(m):
            m["database"].update(sha256="0" * 64)
    elif fault == "version":
        def manifest_change(m):
            m.update(format_version=99)
    elif fault == "schema":
        def manifest_change(m):
            m["database"].update(schema_version=99)
    elif fault == "counts":
        def manifest_change(m):
            m["database"]["counts"].update(runs=12)
    elif fault == "corrupt_database":
        def database_change(data):
            return b"not a sqlite file" + data[17:]
    elif fault == "traversal":
        extra = ("../outside", "do not extract")
    else:
        extra = ("lab.sqlite3", "duplicate")
    if fault == "duplicate":
        with pytest.warns(UserWarning, match="Duplicate name"):
            _rewrite(original, broken, manifest_change, database_change, extra)
    else:
        _rewrite(original, broken, manifest_change, database_change, extra)
    destination = tmp_path / "recovered"
    with pytest.raises(ValueError):
        restore(broken, destination)
    assert not destination.exists()
    assert not (tmp_path / "outside").exists()


def test_rechecks_sqlite_integrity_even_when_checksum_is_updated(tmp_path):
    source = tmp_path / "source"
    _database(source)
    original, broken = tmp_path / "good.zip", tmp_path / "bad.zip"
    backup(source, original)
    with zipfile.ZipFile(original) as archive:
        corrupted = b"not a sqlite file" + archive.read("lab.sqlite3")[17:]
    _rewrite(original, broken,
             lambda m: m["database"].update(sha256=hashlib.sha256(corrupted).hexdigest()),
             lambda _: corrupted)
    with pytest.raises(ValueError, match="integrity"):
        restore(broken, tmp_path / "destination")


def test_archived_symlinks_and_compressed_entries_are_rejected(tmp_path):
    for kind in ("symlink", "compressed"):
        archive_path = tmp_path / f"{kind}.zip"
        with zipfile.ZipFile(archive_path, "w") as archive:
            archive.writestr("manifest.json", "{}")
            entry = zipfile.ZipInfo("lab.sqlite3")
            if kind == "symlink":
                entry.external_attr = (stat.S_IFLNK | 0o777) << 16
            else:
                entry.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(entry, "outside.sqlite3")
        with pytest.raises(ValueError, match="regular, uncompressed"):
            restore(archive_path, tmp_path / kind)


def test_unsafe_paths_and_database_triggers_are_rejected(tmp_path):
    source = tmp_path / "source"
    _database(source)
    with pytest.raises(ValueError, match="traversal"):
        backup(source, tmp_path / "child/../output.zip")
    with sqlite3.connect(source / "lab.sqlite3") as db:
        db.execute("CREATE TRIGGER extra AFTER UPDATE ON runs BEGIN DELETE FROM scenarios; END")
    with pytest.raises(ValueError, match="unexpected"):
        backup(source, tmp_path / "unsafe.zip")
    assert not (tmp_path / "unsafe.zip").exists()


@pytest.mark.parametrize("version,ddl", [
    (1, "CREATE TABLE workflow_runs (id TEXT PRIMARY KEY, body TEXT NOT NULL)"),
    (2, "CREATE TABLE unrelated (id TEXT PRIMARY KEY, body TEXT NOT NULL)"),
    (2, "CREATE TABLE workflow_runs (id TEXT PRIMARY KEY, body TEXT)"),
    (2, "CREATE TABLE workflow_runs (id TEXT PRIMARY KEY, body TEXT NOT NULL, extra TEXT)"),
    (2, "CREATE TABLE workflow_runs (id TEXT PRIMARY KEY, body TEXT NOT NULL CHECK(body <> ''))"),
    (2, "CREATE TABLE workflow_runs (id TEXT PRIMARY KEY, body TEXT NOT NULL UNIQUE)"),
])
def test_optional_workflow_table_does_not_allow_other_schema_extensions(tmp_path, version, ddl):
    source = tmp_path / "source"
    _database(source, version=version)
    with sqlite3.connect(source / "lab.sqlite3") as db:
        db.execute(ddl)
    with pytest.raises(ValueError, match="unexpected|layout"):
        backup(source, tmp_path / "unsafe.zip")
    assert not (tmp_path / "unsafe.zip").exists()


def test_schema_two_accepts_exact_optional_empty_workflow_table(tmp_path):
    source = tmp_path / "source"
    _database(source)
    with sqlite3.connect(source / "lab.sqlite3") as db:
        db.execute("CREATE TABLE IF NOT EXISTS workflow_runs (id TEXT PRIMARY KEY, body TEXT NOT NULL)")
    archive = tmp_path / "workflow.zip"
    result = backup(source, archive)
    assert result["manifest"]["database"]["counts"]["workflow_runs"] == 0
    restored = restore(archive, tmp_path / "restored")
    assert restored["schema_version"] == 2 and restored["counts"]["workflow_runs"] == 0


@pytest.mark.parametrize("active_status", ["preparing", "running", "closing"])
def test_workflow_restore_keeps_reports_revokes_access_and_interrupts_without_resuming(
        tmp_path, active_status):
    from genlayer_agent_lab.workflows import WorkflowManager, WorkflowSpec

    def no_runtime(*args, **kwargs):
        pytest.fail("Recovery must not recreate a signer, cohort or Studio session")

    source = tmp_path / "source"
    _database(source)
    token = "old-workflow-agent-token"
    spec = WorkflowSpec.model_validate({
        "context": {"resource_id": "service-001", "policy_version": "v1",
                    "amount": 100, "evidence": "Delivery receipt"},
        "initial_fixture": {"decision": "approve", "authorized_amount": 100},
        "expectations": {"final_state": {"decision": "approve", "authorized_amount": 100,
                                         "released_amount": 100}, "required_actions": ["release"]},
    }).model_dump()
    completed = {
        "run_id": "workflow-" + "a" * 32, "status": "completed", "spec": spec,
        "token_sha256": hashlib.sha256(token.encode()).hexdigest(), "created_at": 1,
        "contract_address": "0x" + "b" * 40,
        "state": {"decision": "approve", "authorized_amount": 100, "released_amount": 100},
        "decision": {"status": "FINALIZED", "execution_success": True,
                     "result": {"decision": "approve", "authorized_amount": 100}},
        "transactions": {}, "intents": {"release-key": {
            "operation": "release", "idempotency_key": "release-key", "status": "completed",
            "tx_id": "0x" + "c" * 64, "result": {"released_amount": 100}, "error_code": None}},
        "events": [{"index": 0, "kind": "ended", "status": "completed", "cleanup": "restored"}],
        "event_count": 1, "behavior_failures": [], "backend_failures": [], "cleanup": "restored",
        "error_code": None, "finish_requested": True, "cancel_requested": False,
        "ambiguous_submission": False,
    }
    store = Store(source)
    manager = WorkflowManager(store, source, client_factory=no_runtime, cohort_factory=no_runtime)
    try:
        manager._runs[completed["run_id"]] = copy.deepcopy(completed)
        manager._save(completed)
        original_report = manager.report(completed["run_id"])
        assert original_report["verification"] == "pass"
        assert manager.authenticate(completed["run_id"], token)
    finally:
        manager.close()
        store.close()
    active = copy.deepcopy(completed)
    active.update(run_id="workflow-" + "d" * 32, status=active_status,
                  cleanup="required", finish_requested=False)
    active["transactions"] = {"evaluate": {"tx_id": "0x" + "e" * 64, "status": "ACCEPTED"}}
    with sqlite3.connect(source / "lab.sqlite3") as db:
        db.execute("INSERT INTO workflow_runs VALUES (?,?)", (active["run_id"], json.dumps(active)))
    archive = tmp_path / "workflows.zip"
    backup(source, archive)
    destination = tmp_path / "restored"
    result = restore(archive, destination)
    assert result["agent_tokens_revoked"] == 2
    assert result["counts"]["workflow_runs"] == 2 and result["studio_restored"] is False
    assert not (destination / "studio").exists()
    with sqlite3.connect(destination / "lab.sqlite3") as db:
        rows = {identifier: json.loads(body)
                for identifier, body in db.execute("SELECT id, body FROM workflow_runs")}
    interrupted = rows[active["run_id"]]
    assert interrupted["status"] == "interrupted" and interrupted["cleanup"] == "unresolved"
    assert interrupted["error_code"] == "interrupted_by_restore"
    for key in ("spec", "transactions", "intents", "events"):
        assert interrupted[key] == active[key]
    store = Store(destination)
    manager = WorkflowManager(store, destination, client_factory=no_runtime, cohort_factory=no_runtime)
    try:
        assert manager.report(completed["run_id"]) == original_report
        assert not manager.authenticate(completed["run_id"], token)
        assert not manager.authenticate(active["run_id"], token)
        assert manager._thread is None and manager._active_id is None
        with pytest.raises(ValueError, match="explicit external recovery"):
            manager.create(spec)
    finally:
        manager.close()
        store.close()


@pytest.mark.parametrize("status", ["completed", "cancelled", "inconclusive", "interrupted"])
def test_terminal_workflow_records_keep_outcomes_but_lose_old_credentials(tmp_path, status):
    source = tmp_path / "source"
    _database(source)
    original = {"run_id": "workflow-history", "status": status, "cleanup": "restored",
                "token_sha256": "a" * 64, "agent_hash": "b" * 64,
                "events": [{"kind": "historical"}], "error_code": None}
    with sqlite3.connect(source / "lab.sqlite3") as db:
        db.execute("CREATE TABLE workflow_runs (id TEXT PRIMARY KEY, body TEXT NOT NULL)")
        db.execute("INSERT INTO workflow_runs VALUES (?, ?)", (original["run_id"], json.dumps(original)))
    archive = tmp_path / "workflow.zip"
    backup(source, archive)
    destination = tmp_path / "restored"
    assert restore(archive, destination)["agent_tokens_revoked"] == 1
    with sqlite3.connect(destination / "lab.sqlite3") as db:
        recovered = json.loads(db.execute("SELECT body FROM workflow_runs").fetchone()[0])
    assert recovered.pop("token_sha256") != original.pop("token_sha256")
    assert recovered.pop("agent_hash") != original.pop("agent_hash")
    assert recovered == original


def test_source_symlink_rejected_where_supported(tmp_path):
    source = tmp_path / "source"
    _database(source)
    linked = tmp_path / "linked"
    try:
        linked.symlink_to(source, target_is_directory=True)
    except OSError:
        pytest.skip("Creating symlinks requires an OS capability unavailable to this test")
    with pytest.raises(ValueError, match="symlinks"):
        backup(linked, tmp_path / "unsafe.zip")


def test_backup_and_restore_enforce_size_bounds(tmp_path, monkeypatch):
    import genlayer_agent_lab.recovery as recovery

    source = tmp_path / "source"
    _database(source)
    archive = tmp_path / "backup.zip"
    backup(source, archive)
    monkeypatch.setattr(recovery, "MAX_DATABASE_BYTES", 512)
    with pytest.raises(ValueError, match="size"):
        backup(source, tmp_path / "too-large.zip")
    with pytest.raises(ValueError, match="size"):
        restore(archive, tmp_path / "too-large")
    assert not (tmp_path / "too-large").exists()


def test_newer_source_schema_is_not_downgraded_or_archived(tmp_path):
    source = tmp_path / "source"
    _database(source)
    with sqlite3.connect(source / "lab.sqlite3") as db:
        db.execute("PRAGMA user_version = 99")
    with pytest.raises(ValueError, match="schema"):
        backup(source, tmp_path / "future.zip")
    with sqlite3.connect(source / "lab.sqlite3") as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 99
    assert not (tmp_path / "future.zip").exists()


def test_malformed_source_sqlite_returns_a_sanitized_recovery_error(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    (source / "lab.sqlite3").write_bytes(b"private-corrupt-database-data" * 100)
    with pytest.raises(ValueError) as failure:
        backup(source, tmp_path / "corrupt.zip")
    assert "private-corrupt" not in str(failure.value)
    assert not (tmp_path / "corrupt.zip").exists()


def test_sqlite_open_errors_are_sanitized(monkeypatch, tmp_path):
    import genlayer_agent_lab.recovery as recovery

    def unavailable(*args, **kwargs):
        raise sqlite3.DatabaseError("untrusted-database-details")

    monkeypatch.setattr(recovery.sqlite3, "connect", unavailable)
    with pytest.raises(ValueError, match="opened safely"):
        recovery._read_database(tmp_path / "broken.sqlite3")
    with pytest.raises(ValueError, match="revoke old run credentials"):
        recovery._revoke_run_tokens(tmp_path / "broken.sqlite3")


def test_failed_restore_does_not_leave_partial_credential_state(monkeypatch, tmp_path):
    import genlayer_agent_lab.recovery as recovery

    source = tmp_path / "source"
    _database(source)
    archive = tmp_path / "backup.zip"
    backup(source, archive)

    def failed_token(*args):
        raise OSError("Simulated local storage failure")

    monkeypatch.setattr(recovery.secrets, "token_urlsafe", failed_token)
    destination = tmp_path / "restored"
    with pytest.raises(OSError, match="storage failure"):
        restore(archive, destination)
    assert not destination.exists()
    assert not list(tmp_path.glob(".lab-restore-*"))


@pytest.mark.skipif(os.name == "nt", reason="Windows permissions inherit the parent directory ACL")
def test_backup_and_restored_credentials_are_owner_only(tmp_path):
    source = tmp_path / "source"
    _database(source)
    archive = tmp_path / "private.zip"
    backup(source, archive)
    destination = tmp_path / "restored"
    restore(archive, destination)
    assert archive.stat().st_mode & 0o777 == 0o600
    assert destination.stat().st_mode & 0o777 == 0o700
    assert (destination / "admin.token").stat().st_mode & 0o777 == 0o600
