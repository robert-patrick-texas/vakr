# Metadata database

On the Compact profile (the MVP), vakr keeps its inventory in one SQLite file, **`/var/lib/vakr/metadata.db`**. That covers sites, tags, groups, devices, interfaces and their annotations, collection policies, collectors, interface identity history, and the audit log. Telemetry lives in a separate database. This page covers where the files are, how to check, migrate, back up, and restore the metadata database, and what to do when vakr refuses to use it.

The database never holds secrets. Devices name their SNMP credential, and the credential itself stays in `/etc/vakr/secrets/credentials.toml` ([credentials](credentials.md)). As a result, a metadata backup without the credentials file cannot poll devices, and the credentials file needs stronger handling than the metadata backup ([blueprint 16.5](../../tool-blueprint.md#165-backup-scope)).

## Files

| Path | What it is |
|---|---|
| `/var/lib/vakr/metadata.db` | The database. |
| `/var/lib/vakr/metadata.db-wal`, `metadata.db-shm` | The write-ahead log and its index. While vakr runs they are part of the database: never delete them, and never copy `metadata.db` on its own. |
| `/var/lib/vakr/backups/metadata-<UTC time>.db` | Verified online backups written by `vakr db migrate` and `vakr db backup`. Each is a single self-contained file at mode 0600 in a 0700 directory. |
| `/var/lib/vakr/backups/pre-update-<UTC time>.tar.gz` | Self-update's archive of the configuration and every database, taken before each update ([updates](updates.md)). |

The package creates `/var/lib/vakr` (owner `vakr:vakr`, mode 0750). `VAKR_ROOT` relocates every path for tests and containers.

## Commands

| Command | What it does |
|---|---|
| `vakr db status` | Shows the database path, schema version, the range this release supports, pending migrations, the applied history with durations, and `PRAGMA quick_check`. Changes nothing. |
| `vakr db migrate` | Writes a verified online backup, then applies pending migrations. It creates the database on a fresh install. |
| `vakr db migrate --allow-destructive --backup-confirmed` | Also applies migrations that remove or rewrite data (MIG-005). |
| `vakr db backup [--output DIR]` | Writes a verified online backup while services keep running. The default directory is `backups/` beside the database. |

Run `migrate` and `backup` as the service user, for example `sudo -u vakr vakr db migrate`. Files they create belong to whoever runs them, and the services, which run as `vakr`, cannot write a `metadata.db` owned by root.

All three accept `--database FILE` to work on another file. For example, `vakr db status --database /var/lib/vakr/backups/metadata-20261020T031500Z.db` checks a backup before you restore it.

| Exit status | Meaning |
|---|---|
| 0 | The schema is current and the integrity check passed (or the migration or backup succeeded). |
| 1 | A problem: no database yet, pending or failed migrations, a failed integrity check, or a file that is unreadable or not a vakr metadata database. |
| 2 | The schema is newer than this release supports (MIG-004); see [newer schema](#newer-schema-refused). Usage errors also exit 2. |

```console
$ vakr db status
database:  /var/lib/vakr/metadata.db
schema:    1 (this release runs 1; upgrades from 0)
state:     current: schema 1 is current
pending:   none
integrity: ok (PRAGMA quick_check)
history:
  0001 initial applied 2026-10-20T03:15:00Z in 0.002 s by vakr 0.2.0
```

## How vakr uses SQLite

These settings follow [blueprint 5.3](../../tool-blueprint.md#53-sqlite-requirements):

- **WAL mode.** Readers work from a snapshot and never wait for the writer, and the writer never waits for readers.
- **`synchronous=NORMAL`.** In WAL mode a power cut can lose the last few commits but cannot corrupt the file.
- **Foreign keys on.** A device cannot point at a site that does not exist.
- **One writer at a time, with bounds.** Inside one vakr process, writers queue for the single write lock. A writer that would be the 33rd in the queue, or that waits more than 10 s, gets a "retry shortly" error instead of hanging. A write lock held by *another* process (for example `vakr db migrate`) is waited for at most 5 s (`busy_timeout`). Each process keeps at most 8 open connections to the file.
- **No deletes.** Retiring a device or interface is a state change, so its history and telemetry stay (META-009, LIFE-002). Identity history and the audit log are append-only. Triggers in the schema refuse any delete or rewrite, even from a hand-run `sqlite3` session.
- **No row numbers.** Every record is identified by its UUID, stored as 16 bytes, and the tables have no row IDs at all (DB-007).

| Bound | Default |
|---|---|
| `busy_timeout` (another process's write lock) | 5,000 ms |
| Wait for the writer path or a connection, in-process | 10,000 ms |
| Writers queued for the writer path | 32 |
| Open connections per process | 8 |

These defaults are proposed for Appendix E and are not yet listed there.

The adapter is synchronous: every call blocks on disk I/O and locks. vakr's asynchronous services (M3 onwards) must call it from a bounded worker thread, never from the event loop, and send telemetry writes through one dedicated writer thread (5.3).

## Migrations

Every schema change is a numbered SQL file shipped inside the vakr package, such as `0001_initial.sql`. Each one states what it changes, whether it is destructive, whether it runs in a single transaction, how to recover if it fails part-way, how its run time grows with the data, and which tests cover it ([blueprint 5.6](../../tool-blueprint.md#56-migration-rules)). `vakr db status` lists pending migrations with their descriptions and marks destructive ones.

The database records each migration in its `schema_migrations` table. A migration runs in three steps:

1. vakr commits an `in_progress` row for the migration on its own.
2. It runs the migration's statements and marks the row `applied`, in one transaction where SQLite allows it.
3. If anything fails, a transactional migration rolls back completely and the row is removed: the database is exactly as before.

**Startup check.** Services start only on the schema version the installed release runs on ([16.3](../../tool-blueprint.md#163-startup-preflight)). After installing a release by hand, for example with `apt install`, run `vakr db migrate` before starting services.

**Destructive migrations.** A migration that removes or rewrites data needs two flags: `--allow-destructive` (you mean it) and `--backup-confirmed` (you have kept the backup that `vakr db migrate` just wrote, preferably off the host). vakr re-verifies that backup before it continues. A fresh install has nothing to destroy, so it needs neither flag.

**Interrupted migrations (MIG-003).** If the host loses power or the process is killed mid-migration, the `in_progress` row survives and `vakr db status` reports `interrupted`:

- If the migration was transactional, SQLite has already undone its work. Run `vakr db migrate` again.
- If it was not transactional (rare: only statements SQLite refuses inside a transaction, such as `VACUUM`), it may be partly applied, and vakr refuses to continue. Restore the backup that `vakr db migrate` wrote just before it started (see [restore](#restore)), then run `vakr db migrate` again. The status output repeats the migration's own recovery notes.

Never edit `schema_migrations` by hand. vakr compares it with the migrations it ships and refuses a history it did not write.

## Backup

Run `vakr db backup` whenever you want a copy. It uses SQLite's online backup API, so services keep running and the copy is consistent. Before the copy is given its final name, vakr checks that it opens, passes `PRAGMA quick_check`, and is at the expected schema version. `vakr db migrate` takes the same backup automatically, and self-update archives all databases before every update.

Copy backups off the host on your normal schedule. Back up `/etc/vakr/secrets/credentials.toml` separately, with stronger protection.

## Restore

To restore `metadata.db` from a backup:

1. Stop vakr: `sudo systemctl stop vakr.target`.
2. Check the backup: `vakr db status --database /var/lib/vakr/backups/metadata-<time>.db`. It should report `current`, or `pending` if the backup came from an older release.
3. Move the current files aside, including the WAL. A WAL left beside the restored file would be applied to the wrong database.

   ```sh
   cd /var/lib/vakr
   sudo mkdir -p broken && sudo mv metadata.db metadata.db-wal metadata.db-shm broken/ 2>/dev/null
   ```

4. Put the backup in place: `sudo install -o vakr -g vakr -m 0640 backups/metadata-<time>.db metadata.db`.
5. Run `vakr db status`, and `sudo -u vakr vakr db migrate` if it reports pending migrations.
6. Start vakr: `sudo systemctl start vakr.target`.

The restore keeps every UUID, so dashboards, history, and telemetry still match their devices and interfaces.

## Integrity checks

`vakr db status` and the `metadata_db` health check both run `PRAGMA quick_check`, which takes well under a second at MVP size. The health check reads local files only and changes nothing:

| Result | When |
|---|---|
| OK | The schema is current and sound, or the host has no `/var/lib/vakr` (not an installed vakr node). |
| WARNING | No database yet (fresh install: run `vakr db migrate`), pending migrations, or an interrupted transactional migration. |
| CRITICAL | A schema newer than this release, a failed `quick_check`, an unreadable file, a file that is not a vakr metadata database, or an interrupted migration that needs a restore. |

If `quick_check` fails, stop vakr, copy all three `metadata.db*` files somewhere safe for the developers, and [restore](#restore) the newest good backup. Damage usually means a disk or filesystem fault, so check the disk before you trust the host again.

## Newer schema refused

`vakr db status` exits 2, and the service refuses to start, with a message such as:

```text
schema 2 was written by a newer vakr release; this release supports up to 1. Do not run this release on it: reinstall the newer release, or restore the backup taken before the upgrade
```

This happens when an older vakr runs against a database that a newer release has already migrated. Common causes are a manual downgrade, a rollback that skipped the database, or a database copied from a newer node. The older release cannot know what the newer migration changed, so it does not touch the file (MIG-004). Do one of the following:

- **Go forward.** Reinstall the newer release, for example with `vakr self-update --version <newer>`.
- **Go back.** Restore the backup from before the upgrade. `vakr self-update --rollback` restores self-update's pre-update archive (UPD-005). Otherwise use the `metadata-<time>.db` that `vakr db migrate` wrote before it migrated (see [restore](#restore)). Changes made after the upgrade are lost.

Other refusals name their cause: "not a vakr metadata database" means the path points at another file, and "differs from this release's" means a different build migrated the database. In both cases, check the path, then restore a backup.
