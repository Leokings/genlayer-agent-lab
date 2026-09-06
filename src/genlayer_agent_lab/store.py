"""Single-owner SQLite storage with atomic state/trace commits."""

import json
import os
import sqlite3
import uuid
from contextlib import contextmanager
from pathlib import Path


class Store:
    def __init__(self, data_dir: Path):
        self.root = Path(data_dir).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        if os.name != "nt":
            self.root.chmod(0o700)
        self._lock = (self.root / "engine.lock").open("a+b")
        self._lock.seek(0, 2)
        if self._lock.tell() == 0:
            self._lock.write(b"0")
            self._lock.flush()
        self._lock.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(self._lock.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self._lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            self._lock.close()
            raise RuntimeError("This data directory is in use. Connect to its server with --url.") from exc
        self.path = self.root / "lab.sqlite3"
        try:
            with self.connection() as db:
                version = db.execute("PRAGMA user_version").fetchone()[0]
                if version not in {0, 1, 2}:
                    raise RuntimeError(f"Unsupported database schema version {version}; this release supports 2")
                if version == 1:
                    backup_path = self.root / f"lab.schema1.{uuid.uuid4().hex}.backup.sqlite3"
                    backup = sqlite3.connect(backup_path)
                    try:
                        db.backup(backup)
                    finally:
                        backup.close()
                db.execute("BEGIN IMMEDIATE")
                db.execute("CREATE TABLE IF NOT EXISTS runs (id TEXT PRIMARY KEY, body TEXT NOT NULL)")
                db.execute("CREATE TABLE IF NOT EXISTS scenarios (id TEXT PRIMARY KEY, body TEXT NOT NULL)")
                db.execute("CREATE TABLE IF NOT EXISTS bindings (id TEXT PRIMARY KEY, body TEXT NOT NULL)")
                if version != 2:
                    db.execute("PRAGMA user_version = 2")
        except Exception:
            self._lock.close()
            raise

    @contextmanager
    def connection(self):
        db = sqlite3.connect(self.path, timeout=10)
        try:
            with db:
                yield db
        finally:
            db.close()

    def records(self) -> list[dict]:
        with self.connection() as db:
            return [json.loads(row[0]) for row in db.execute("SELECT body FROM runs ORDER BY rowid")]

    def save(self, run: dict):
        with self.connection() as db:
            db.execute("INSERT INTO runs VALUES (?,?) ON CONFLICT(id) DO UPDATE SET body=excluded.body",
                       (run["run_id"], json.dumps(run, ensure_ascii=True)))

    def scenarios(self) -> list[dict]:
        with self.connection() as db:
            return [json.loads(row[0]) for row in db.execute("SELECT body FROM scenarios")]

    def save_scenario(self, scenario: dict):
        with self.connection() as db:
            db.execute("INSERT INTO scenarios VALUES (?,?) ON CONFLICT(id) DO UPDATE SET body=excluded.body",
                       (scenario["id"], json.dumps(scenario)))

    def close(self):
        if not self._lock.closed:
            self._lock.close()

    def bindings(self) -> list[dict]:
        with self.connection() as db:
            return [json.loads(row[0]) for row in db.execute("SELECT body FROM bindings")]

    def save_binding(self, snapshot: dict):
        with self.connection() as db:
            db.execute("INSERT INTO bindings VALUES (?,?) ON CONFLICT(id) DO UPDATE SET body=excluded.body",
                       (snapshot["definition"]["id"], json.dumps(snapshot, ensure_ascii=True)))
