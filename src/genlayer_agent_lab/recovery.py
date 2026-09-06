"""Offline, bounded SQLite recovery archives; no credentials or Studio volumes copied."""

from __future__ import annotations

import hashlib
import json
import os
import secrets
import sqlite3
import stat
import tempfile
import time
import zipfile
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

from . import __version__

MAX_DATABASE_BYTES = 256 * 1024 * 1024
MAX_MANIFEST_BYTES = 64 * 1024
MAX_ROW_BYTES = 16 * 1024 * 1024
SCHEMAS = {1, 2}
ARCHIVE_FORMAT = "genlayer-agent-lab-sqlite-backup"


def _path(value: Path) -> Path:
    path = Path(value).expanduser()
    if ".." in path.parts:
        raise ValueError("Recovery paths must not contain parent traversal")
    path = path.absolute()
    # Checking before resolving also catches junctions/reparse points on Windows.
    for part in reversed((path, *path.parents)):
        try:
            info = part.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
            raise ValueError("Recovery paths must not contain symlinks or reparse points")
    return path


def _private(path: Path, mode: int):
    if os.name != "nt":
        path.chmod(mode)


@contextmanager
def _exclusive_lock(root: Path):
    """Match Store's lifetime advisory lock without initializing or migrating it."""
    lock_path = _path(root / "engine.lock")
    lock = lock_path.open("a+b")
    try:
        lock.seek(0, 2)
        if lock.tell() == 0:
            lock.write(b"0")
            lock.flush()
        lock.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            raise RuntimeError("Stop the Lab service before backing up or restoring this data directory") from None
        yield
    finally:
        lock.close()


def _read_database(path: Path):
    db = None
    try:
        db = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=5)
        db.execute("PRAGMA trusted_schema = OFF")
        return db
    except sqlite3.Error:
        if db is not None:
            db.close()
        raise ValueError("Recovery database could not be opened safely") from None


def _inspect_database(path: Path) -> dict:
    if not path.is_file() or not 0 < path.stat().st_size <= MAX_DATABASE_BYTES:
        raise ValueError("Database is missing or exceeds the recovery size limit")
    db = _read_database(path)
    deadline = time.monotonic() + 30
    db.set_progress_handler(lambda: int(time.monotonic() > deadline), 10000)
    try:
        if db.execute("PRAGMA integrity_check").fetchall() != [("ok",)]:
            raise ValueError("Database integrity verification failed")
        version = db.execute("PRAGMA user_version").fetchone()[0]
        if version not in SCHEMAS:
            raise ValueError("Database schema is unsupported by this recovery release")
        tables = {"runs", "scenarios"} | ({"bindings"} if version == 2 else set())
        objects = db.execute("SELECT name, type FROM sqlite_master WHERE name NOT LIKE 'sqlite_%'").fetchall()
        if set(objects) != {(name, "table") for name in tables}:
            raise ValueError("Database contains unexpected tables, views, indexes or triggers")
        counts = {}
        for table in sorted(tables):
            columns = db.execute(f"PRAGMA table_info({table})").fetchall()
            if [(row[1], row[2].upper(), row[5]) for row in columns] != [("id", "TEXT", 1), ("body", "TEXT", 0)]:
                raise ValueError("Database table layout is unsupported")
            counts[table] = 0
            for identifier, body in db.execute(f"SELECT id, body FROM {table}"):
                if not isinstance(identifier, str) or not isinstance(body, str) or len(body.encode()) > MAX_ROW_BYTES:
                    raise ValueError("Database record is invalid or exceeds the recovery size limit")
                try:
                    value = json.loads(body)
                except (ValueError, RecursionError):
                    raise ValueError("Database contains invalid JSON records") from None
                if type(value) is not dict:
                    raise ValueError("Database records must be JSON objects")
                counts[table] += 1
        return {"schema_version": version, "counts": counts}
    except sqlite3.Error:
        raise ValueError("Database integrity or schema verification failed") from None
    finally:
        db.close()


def _sha256(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def _snapshot(source: Path, destination: Path):
    db, target = _read_database(source), None
    deadline = time.monotonic() + 60
    try:
        target = sqlite3.connect(destination)
        page_size = db.execute("PRAGMA page_size").fetchone()[0]

        def bounded(status, remaining, total):
            if total * page_size > MAX_DATABASE_BYTES or time.monotonic() > deadline:
                raise ValueError("Database backup exceeded the recovery size or time limit")

        # SQLite copies a consistent snapshot, including committed WAL pages.
        # Copying only the source file could silently omit those transactions.
        db.backup(target, pages=256, progress=bounded, sleep=.01)
        target.execute("PRAGMA journal_mode = DELETE")
    except sqlite3.Error:
        raise ValueError("SQLite could not produce a consistent backup") from None
    finally:
        if target is not None:
            target.close()
        db.close()


def backup(data_dir: Path, output: Path) -> dict:
    """Back up an existing, stopped Lab installation without changing its schema."""
    root, output = _path(data_dir), _path(output)
    source = _path(root / "lab.sqlite3")
    if not root.is_dir() or not source.is_file():
        raise ValueError("The source must be an existing Lab data directory")
    if not output.parent.is_dir() or output.exists():
        raise ValueError("Backup output must be a new file in an existing directory")
    for suffix in ("-wal", "-shm", "-journal"):
        _path(Path(str(source) + suffix))
    with _exclusive_lock(root), tempfile.TemporaryDirectory(prefix=".lab-backup-", dir=output.parent) as temporary:
        snapshot = Path(temporary) / "lab.sqlite3"
        _snapshot(source, snapshot)
        inspected = _inspect_database(snapshot)
        manifest = {
            "format": ARCHIVE_FORMAT, "format_version": 1, "toolkit_version": __version__,
            "created_at": datetime.now(UTC).isoformat(),
            "database": {"file": "lab.sqlite3", "bytes": snapshot.stat().st_size,
                         "sha256": _sha256(snapshot), **inspected},
            "scope": "Lab SQLite only; Studio volumes, images, accounts, credentials and logs are excluded",
            "restore_auth": "Fresh administrator token; saved per-run credential hashes revoked",
        }
        encoded = json.dumps(manifest, indent=2).encode("utf-8")
        created = False
        try:
            with output.open("xb") as stream:
                created = True
                _private(output, 0o600)
                with zipfile.ZipFile(stream, "w", compression=zipfile.ZIP_STORED) as archive:
                    archive.writestr("manifest.json", encoded)
                    archive.write(snapshot, "lab.sqlite3")
                stream.flush()
                os.fsync(stream.fileno())
        except Exception:
            if created:
                output.unlink(missing_ok=True)
            raise
    return {"backed_up": True, "archive": str(output), "manifest": manifest}


def _unpack_verified(archive_path: Path, stage: Path) -> dict:
    if not archive_path.is_file() or not 0 < archive_path.stat().st_size <= MAX_DATABASE_BYTES + MAX_MANIFEST_BYTES:
        raise ValueError("Recovery archive is missing or exceeds the size limit")
    try:
        with zipfile.ZipFile(archive_path) as archive:
            entries = archive.infolist()
            if len(entries) != 2 or {item.filename for item in entries} != {"manifest.json", "lab.sqlite3"}:
                raise ValueError("Recovery archive must contain exactly the manifest and database")
            for item in entries:
                mode = item.external_attr >> 16
                if ((stat.S_IFMT(mode) not in (0, stat.S_IFREG)) or item.flag_bits & 1
                        or item.compress_type != zipfile.ZIP_STORED):
                    raise ValueError("Recovery archive entries must be regular, uncompressed files")
                limit = MAX_MANIFEST_BYTES if item.filename == "manifest.json" else MAX_DATABASE_BYTES
                if not 0 < item.file_size <= limit or item.compress_size != item.file_size:
                    raise ValueError("Recovery archive entry exceeds its size limit")
            manifest = json.loads(archive.read("manifest.json"))
            if (type(manifest) is not dict or manifest.get("format") != ARCHIVE_FORMAT
                    or type(manifest.get("format_version")) is not int or manifest["format_version"] != 1
                    or not isinstance(manifest.get("toolkit_version"), str)
                    or not 1 <= len(manifest["toolkit_version"]) <= 64):
                raise ValueError("Recovery archive manifest format is unsupported")
            metadata = manifest.get("database")
            if (type(metadata) is not dict or metadata.get("file") != "lab.sqlite3"
                    or type(metadata.get("bytes")) is not int
                    or metadata["bytes"] != archive.getinfo("lab.sqlite3").file_size
                    or type(metadata.get("schema_version")) is not int
                    or metadata["schema_version"] not in SCHEMAS):
                raise ValueError("Recovery archive database manifest is invalid")
            destination = stage / "lab.sqlite3"
            # Never extract archive-controlled paths or attributes.
            with archive.open("lab.sqlite3") as source, destination.open("xb") as target:
                remaining = metadata["bytes"]
                while chunk := source.read(min(1024 * 1024, remaining + 1)):
                    remaining -= len(chunk)
                    if remaining < 0:
                        raise ValueError("Recovery database exceeds its declared size")
                    target.write(chunk)
                if remaining:
                    raise ValueError("Recovery database is truncated")
            if _sha256(destination) != metadata.get("sha256"):
                raise ValueError("Recovery database checksum does not match the manifest")
            inspected = _inspect_database(destination)
            if inspected != {key: metadata.get(key) for key in ("schema_version", "counts")}:
                raise ValueError("Recovery database schema or record counts do not match the manifest")
            return manifest
    except (zipfile.BadZipFile, json.JSONDecodeError, UnicodeError, RecursionError, EOFError):
        raise ValueError("Recovery archive is corrupt or malformed") from None


def _revoke_run_tokens(path: Path) -> int:
    db = None
    revoked = 0
    try:
        db = sqlite3.connect(path)
        db.execute("PRAGMA trusted_schema = OFF")
        db.execute("PRAGMA secure_delete = ON")
        with db:
            for identifier, body in db.execute("SELECT id, body FROM runs").fetchall():
                run = json.loads(body)
                if "agent_hash" in run:
                    # No corresponding token is retained, so historical agents
                    # cannot authenticate after recovery, even for completed runs.
                    run["agent_hash"] = hashlib.sha256(secrets.token_bytes(32)).hexdigest()
                    db.execute("UPDATE runs SET body = ? WHERE id = ?",
                               (json.dumps(run, ensure_ascii=True), identifier))
                    revoked += 1
        db.execute("PRAGMA journal_mode = DELETE")
    except sqlite3.Error:
        raise ValueError("Recovery database could not revoke old run credentials") from None
    finally:
        if db is not None:
            db.close()
    return revoked


def restore(archive: Path, destination: Path) -> dict:
    """Verify an archive, then restore only into a nonexistent data directory."""
    archive, destination = _path(archive), _path(destination)
    if destination.exists() or not destination.parent.is_dir():
        raise ValueError("Restore requires a nonexistent destination in an existing directory")
    with tempfile.TemporaryDirectory(prefix=".lab-restore-", dir=destination.parent) as temporary:
        stage = Path(temporary)
        manifest = _unpack_verified(archive, stage)
        revoked = _revoke_run_tokens(stage / "lab.sqlite3")
        inspected = _inspect_database(stage / "lab.sqlite3")
        # mkdir is an atomic no-overwrite claim, including on Windows. No existing
        # installation is merged, deleted, or replaced by this operation.
        destination.mkdir(mode=0o700)
        owned = False
        created = []
        try:
            with _exclusive_lock(destination):
                if set(destination.iterdir()) != {destination / "engine.lock"}:
                    raise ValueError("Restore destination was populated by another process")
                owned = True
                database = destination / "lab.sqlite3"
                os.replace(stage / "lab.sqlite3", database)
                created.append(database)
                _private(database, 0o600)
                token_file = destination / "admin.token"
                with token_file.open("x", encoding="utf-8") as token:
                    created.append(token_file)
                    _private(token_file, 0o600)
                    token.write(secrets.token_urlsafe(32) + "\n")
                    token.flush()
                    os.fsync(token.fileno())
        except Exception:
            if owned:
                for path in created:
                    path.unlink(missing_ok=True)
                (destination / "engine.lock").unlink(missing_ok=True)
            try:
                destination.rmdir()
            except OSError:
                pass
            raise
    return {"restored": True, "data_dir": str(destination), **inspected,
            "source_toolkit_version": manifest["toolkit_version"],
            "admin_token_rotated": True, "agent_tokens_revoked": revoked,
            "migration_on_next_start": inspected["schema_version"] < max(SCHEMAS),
            "studio_restored": False}
