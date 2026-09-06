# Backup, restore, and upgrade recovery

Lab recovery saves the local SQLite database and restores it into a **new data
directory**. Stop the Lab service first. Both operations use the same exclusive
directory lock as the Engine; they do not provide an online server endpoint.

## What the archive preserves

The archive contains exactly `lab.sqlite3` and `manifest.json`. It preserves run
history, saved reports, scenario definitions, and imported binding snapshots
(including their contract source). The manifest records the toolkit release
that created the archive, SQLite schema version, table counts, database length,
and SHA-256 checksum. Each historical report keeps its originating toolkit
version; taking a newer backup does not rewrite report provenance.

Backup uses SQLite's backup API. Committed transactions in an existing WAL are
included in a consistent, standalone database; copying `lab.sqlite3` by itself
would not provide that guarantee. Backup does not initialize the source or
upgrade its schema. This release supports recovery of schema 1 and schema 2.

Only the SQLite database is included. Recovery does **not** copy `admin.token`,
environment files, build logs, service registrations, daemon configuration,
exported files, container images, or the rest of the data directory.

**Studio's PostgreSQL data, persistent volumes, chain state, deployed contracts,
accounts, and pending transactions are outside this backup.** Saved Studio
observations and transaction IDs remain in Lab's historical reports, but restoring
those reports does not restore the Studio network or make its transactions
available again. Keep the original Studio installation if you need to reconcile
pending transactions. Plan and verify a separate Studio/PostgreSQL recovery
procedure before relying on it; this command has not implemented one.

## Create and check a backup

Use the executable from the release environment you intend to keep for recovery.
Create the archive's parent directory first. The command refuses to overwrite an
existing file.

```text
gl-agent-lab backup --data-dir /path/to/lab-data --output /path/to/backups/before-upgrade.zip
```

Windows paths can be supplied in the same arguments, for example
`--data-dir "C:\AgentLab\data" --output "C:\AgentLab\backups\before-upgrade.zip"`.
Neither `--url` nor `LAB_URL` is supported for this offline operation. Stop the
foreground server, or stop its installed service, before running it. The lock
refusal is intentional; do not remove `engine.lock` to bypass it.

The JSON result reports the archive path, counts, schema, and checksum. It prints
no administrator or agent tokens. A successful archive is flushed to disk, but
an interrupted filesystem write can still leave an incomplete archive. Keep a
second copy on a different device and verify it with a rehearsal restore.

```text
gl-agent-lab restore /path/to/backups/before-upgrade.zip --data-dir /path/to/rehearsal-data
```

`rehearsal-data` must not exist, including as an empty directory. Its parent must
already exist. Restore does not merge, overwrite, or delete an existing Lab.

Restore checks the two permitted archive entries, declared lengths, checksum,
SQLite integrity, schema layout, JSON records, and record counts before creating
the destination. Archive-controlled paths are never extracted. Parent traversal,
symlinks/reparse points, unexpected database triggers/views, compressed entries,
and archives exceeding the recovery limits are rejected. The database limit is
256 MiB; individual record JSON is limited to 16 MiB. These are bounded recovery
archives produced by this toolkit, not a general ZIP import format.

## Credentials and resumed work

Every restore creates a new `admin.token`. Reconfigure the dashboard and clients
to use that new installation's token through the normal local setup process.
Do not copy the old administrator token over it.

Saved per-run credential hashes are replaced with fresh unreachable values. Old
agent run tokens therefore cannot authenticate against restored runs, including
completed runs. The final reports themselves remain unchanged. No replacement
run tokens are issued for historical runs; create a new test run when resuming
testing. Run IDs are retained for report lookup.

The Engine marks previously unfinished runs as interrupted when it first opens
the restored database. It does not replay agent actions or resubmit Studio
transactions. Restoring a Lab archive also does not cancel transactions that an
existing Studio network may still be processing.

Archives contain scenario evidence, contract source, and saved observations, so
treat them as confidential project data. They are **not encrypted or signed**;
checksums detect corruption, not a deliberate replacement of both database and
manifest. Restore only archives from a trusted source. On Unix, new archives,
database files, and tokens receive owner-only permissions, and the restore
directory is owner-only. On Windows, filesystem permissions inherit the parent
directory ACL; choose a private parent directory. The command does not promise
to harden an existing Windows ACL.

## Upgrade and rollback procedure

1. Keep the current release environment, its pinned wheel/source revision, and
   dependency lock. Install the candidate release in a separate versioned virtual
   environment. Do not upgrade the only working environment in place.
2. Stop Lab, create a pre-upgrade archive with the recovery-capable release, and
   retain an untouched copy. The current backup implementation can read schema 1
   without upgrading it. Record the archive checksum outside the archive if you
   need to detect later replacement.
3. Restore to a new rehearsal data directory. Open that copy with the candidate
   release, verify historical reports and imported definitions, and run a small
   fixture test before switching the production Lab data path.
4. To roll back, stop the candidate service. Restore the **pre-upgrade** archive
   to another fresh directory, then start the retained compatible release against
   that directory. If the older release has no recovery command, use a retained
   recovery-capable environment solely to restore the archive; restoration keeps
   the archived schema until an Engine opens it.
5. Reconfigure clients for the newly generated administrator token and register
   the service for the intended environment/data path if needed. Confirm the
   historical report before creating new test runs. Preserve the failed upgrade's
   data separately for diagnosis.

Store currently migrates schema 1 to schema 2 when opened, and first creates a
standalone `lab.schema1.<id>.backup.sqlite3`. That automatic file is useful evidence
of the migration, but it is not this portable recovery archive and is not an
authenticated credential reset. Keep an explicit pre-upgrade archive. An older
release must never be pointed at a newer incompatible schema as a substitute for
rollback. Recovery does not implement arbitrary schema downgrades, environment
rollback, automatic upgrade scheduling, or Studio rollback.

## Verified release gate

`tests/test_recovery.py` exercises a finalized historical run in a schema-1
database: backup without migration, actual Store migration to schema 2, preservation
of its automatic pre-migration database, restoration of the schema-1 archive,
reopening/migration, unchanged historical report, and rejection of its old agent
token. A schema-2 archive additionally preserves an imported custom binding.

Other tests cover committed WAL data, the active Engine lock, existing-destination
refusal, checksum/schema/count/integrity failures, archive traversal and symlink
entries, resource bounds, and credential exclusion. These are local temporary
fixtures. They do not claim a Studio volume recovery drill, a power-loss durability
test, or verification of an older executable's entire dependency environment.
