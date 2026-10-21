# Databases

On the Compact profile (the MVP), vakr keeps everything in two SQLite files in `/var/lib/vakr`:

- **`metadata.db`** holds the inventory: sites, tags, groups, devices, interfaces and their annotations, collection policies, collectors, interface identity history, and the audit log. Since schema 3 it also holds local accounts, login sessions, kiosk tokens, and failed-login counts ([users](users.md)).
- **`telemetry.db`** holds what the collectors measured: 30 days of one-minute observations and 400 days of 15-minute rollups, laid out as [ADR-021](../adr/ADR-021-compact-sqlite-telemetry.md) describes.

This page covers where the files are, how to check, migrate, back up, and restore them, how telemetry is stored and expired, and what to do when vakr refuses to use a database.

Neither database holds device credentials. Devices name their SNMP credential, and the credential itself stays in `/etc/vakr/secrets/credentials.toml` ([credentials](credentials.md)). As a result, a backup without the credentials file cannot poll devices, and the credentials file needs stronger handling than the database backups ([blueprint 16.5](../../tool-blueprint.md#165-backup-scope)). `metadata.db` does hold the Argon2id hashes of local account passwords and SHA-256 digests of session tokens. Neither can be turned back into a password or a token, but treat `metadata.db` and its backups as confidential ([users](users.md#where-the-secrets-live)).

## Files

| Path | What it is |
|---|---|
| `/var/lib/vakr/metadata.db` | The metadata database. |
| `/var/lib/vakr/telemetry.db` | The telemetry database. At the MVP workload it grows to about 40 GB per node and then stays there, because space freed by retention is reused. |
| `*.db-wal`, `*.db-shm` beside each | The write-ahead log and its index. While vakr runs they are part of the database: never delete them, and never copy a `.db` file on its own. |
| `/var/lib/vakr/backups/metadata-<UTC time>.db`, `telemetry-<UTC time>.db` | Verified online backups written by `vakr db migrate` and `vakr db backup`. Each is a single self-contained file at mode 0600 in a 0700 directory. |
| `/var/lib/vakr/backups/pre-update-<UTC time>.tar.gz` | Self-update's archive of the configuration and every database, taken before each update ([updates](updates.md)). |

The package creates `/var/lib/vakr` (owner `vakr:vakr`, mode 0750). `VAKR_ROOT` relocates every path for tests and containers. Provision at least 100 GB of SSD-class storage for it ([blueprint 27.3](../../tool-blueprint.md#273-storage-sizing-measured-by-adr-021)): the telemetry file, one backup copy of it, the spool, and the logs must all fit.

## Commands

Every `vakr db` subcommand acts on both databases, metadata first, unless you name one: `vakr db status telemetry`.

| Command | What it does |
|---|---|
| `vakr db status [metadata\|telemetry]` | Shows each database's path, schema version, the range this release supports, pending migrations, the applied history with durations, and `PRAGMA quick_check`. For telemetry it also shows the WAL size and the age of the oldest staged row. Changes nothing. |
| `vakr db status telemetry --full` | Checks every telemetry partition too. On a full 40 GB file this takes several minutes; without `--full`, only the dictionaries, catalog, and staging tables are checked. |
| `vakr db migrate [metadata\|telemetry]` | Writes a verified online backup of each database that has pending migrations, then applies them. It creates missing databases on a fresh install. |
| `vakr db migrate --allow-destructive --backup-confirmed` | Also applies migrations that remove or rewrite data (MIG-005). |
| `vakr db backup [metadata\|telemetry] [--output DIR]` | Writes verified online backups while services keep running. The default directory is `backups/` beside the databases. |

The package runs `vakr db migrate` on every install and upgrade, so both databases are always migrated. When it runs as root, `vakr db` switches to the `vakr` service user before it opens anything, so the files it creates stay vakr-owned.

All subcommands accept `--database FILE` to work on another file, such as a backup you want to check before restoring it. vakr reads the file's kind from its header, so `vakr db status --database /var/lib/vakr/backups/telemetry-20261020T031500Z.db` checks it as a telemetry database.

| Exit status | Meaning |
|---|---|
| 0 | Every database is current and passed its integrity check (or every migration or backup succeeded). |
| 1 | A problem with any database: one is missing, has pending or failed migrations, fails its integrity check, or is unreadable or not a vakr database. |
| 2 | No problem as above, but a schema is newer than this release supports (MIG-004); see [newer schema](#newer-schema-refused). Usage errors also exit 2. |

```console
$ vakr db status telemetry
database:  /var/lib/vakr/telemetry.db
schema:    1 (this release runs 1; upgrades from 0)
state:     current: schema 1 is current
pending:   none
integrity: ok (PRAGMA quick_check of the 12 fixed tables; --full checks all)
history:
  0001 initial applied 2026-10-20T03:15:00Z in 0.004 s by vakr 1.4.0
wal:       3.9 MiB
staging:   oldest staged row 412 s old
```

## How vakr uses SQLite

These settings follow [blueprint 5.3](../../tool-blueprint.md#53-sqlite-requirements):

- **WAL mode.** Readers work from a snapshot and never wait for the writer, and the writer never waits for readers.
- **Durability.** `metadata.db` uses `synchronous=NORMAL`: a power cut can lose the last few commits but cannot corrupt the file. `telemetry.db` uses `synchronous=FULL`: a batch of observations counts as stored only once it is on disk, and only then does the spool release it (STORE-010).
- **Foreign keys on.** A device cannot point at a site that does not exist.
- **One writer at a time, with bounds.** Inside one vakr process, writers queue for the single write lock. A writer that would be the 33rd in the queue, or that waits more than 10 s, gets a "retry shortly" error instead of hanging. A write lock held by *another* process (for example `vakr db migrate`) is waited for at most 5 s (`busy_timeout`). Each process keeps at most 8 open connections to each file.
- **No deletes of records.** Retiring a device or interface is a state change, so its history and telemetry stay (META-009, LIFE-002). Identity history and the audit log are append-only. Triggers in the schema refuse any delete or rewrite, even from a hand-run `sqlite3` session. Telemetry expires only by retention.
- **No row numbers in results.** Every record is identified by its UUID (DB-007). Telemetry rows use small node-local keys internally, but they never leave vakr.

| Bound | Default |
|---|---|
| `busy_timeout` (another process's write lock) | 5,000 ms |
| Wait for the writer path or a connection, in-process | 10,000 ms |
| Writers queued for the writer path | 32 |
| Open connections per process and database | 8 |
| Telemetry page cache per connection | 64 MiB |

These defaults are Appendix E.20 and E.21.

The adapters are synchronous: every call blocks on disk I/O and locks. vakr's asynchronous services call them from a bounded worker thread, never from the event loop, and send telemetry writes through one dedicated writer thread (5.3).

## How telemetry is stored

The telemetry file follows ADR-021's layout C+H; [Appendix E.21](../../tool-blueprint.md#e21-compact-telemetry-storage-adr-021-store-010-store-011) lists every default.

- **Day partitions.** Each UTC day has one table per observation type: `obs_iface_d20261001` for interface samples, and likewise `obs_icmp`, `obs_snmp`, `obs_poll`, and `obs_health`. A table is created when the first row of its day arrives. Rows are clustered by interface or device, collector, and time, so a graph of one interface reads one contiguous range per day.
- **Staging.** New rows first go to a small, time-ordered staging table per type (`obs_iface_hot`, ...). Every 15 minutes the service moves rows older than 2 minutes into their day tables in one transaction and then resets the WAL with a truncating checkpoint. Late rows, such as a peer's backfill after a partition, go straight to their day table.
- **Rollups.** 15-minute buckets for every interface metric (traffic, packets, errors) and every ICMP metric (latency, loss, jitter) live in one table per UTC month, `roll_iface_m202610` and `roll_icmp_m202610`. Each row holds all metrics of one interface or device, collector, and bucket in a packed form. Interface values are kept to 7 significant digits (float32 precision); ICMP values exactly (ADR-021 amendment 3). The collector builds them every hour; see [Rollups and native-data retention](rollups.md).
- **Catalog.** `partition_catalog` lists every day and month table, and `partition_template` holds the table definitions that migrations maintain.
- **Ranking projection.** `rank_unit` lists the projected quarter-hours and hours of the last 25 hours, and `rank_iface` holds each interface's exact ranking partial for them, so long rankings answer within 2 seconds (telemetry schema 2, about 220 MiB at the MVP workload). The collector rebuilds it at every start; see [the ranking projection](rollups.md#ranking-projection).
- **Retention.** An hourly pass expires native data older than `telemetry.native_retention_days` (30), but never data the rollups do not yet cover ([why and how to watch it](rollups.md)), and rollups older than `telemetry.rollup_retention_days` (400). Expired data disappears from every query at once. The tables that hold only expired data are dropped whole, never deleted row by row, so the disk keeps up to 31 days of native data and 431 days of rollups at its high-water mark. Their space is reused by the next tables, so the file stops growing; `VACUUM INTO` gives space back if you ever need to shrink it.

You should never need to look inside the file, but every table is plain SQLite. The pinned integer codes for states and families are listed in its `vocab_code` table.

### Telemetry health

The `telemetry_db` check in `vakr check health` reads local files only and runs in well under a second:

| Result | When |
|---|---|
| OK | The schema is current, the WAL is at most 256 MiB, the oldest staged row is at most 30 minutes old, and the fixed tables pass `PRAGMA quick_check`; or the host has no `/var/lib/vakr`. |
| WARNING | No database yet, pending migrations, an interrupted transactional migration, a WAL above 256 MiB, or staged rows older than 30 minutes. |
| CRITICAL | A schema newer than this release, an unreadable or foreign file, an interrupted migration that needs a restore, or a failed `quick_check`. |

Two warnings are telemetry-specific:

- **WAL above 256 MiB.** The truncating checkpoint after each move could not reset the WAL, usually because a long-running reader held an old snapshot. It resolves itself at the next move once readers finish. If it persists, look for a stuck API or TUI process.
- **Staged rows older than 30 minutes.** The service is not running its 15-minute maintenance, so staging keeps growing. Queries stay correct, only slower. Check that the vakr services are running and look in their journal for maintenance errors.

Neither is ever reported as a device or interface problem: storage trouble is vakr's own health.

A separate `rollups` check reports whether the hourly rollup job keeps up, because native data expires only behind it. Its thresholds and what to do are in [Rollups and native-data retention](rollups.md#the-rollups-health-check).

## Migrations

Every schema change is a numbered SQL file shipped inside the vakr package, such as `0001_initial.sql`. Each database has its own sequence. Each migration states what it changes, whether it is destructive, whether it runs in a single transaction, how to recover if it fails part-way, how its run time grows with the data, and which tests cover it ([blueprint 5.6](../../tool-blueprint.md#56-migration-rules)). `vakr db status` lists pending migrations with their descriptions and marks destructive ones.

Each database records its migrations in its own `schema_migrations` table. A migration runs in three steps:

1. vakr commits an `in_progress` row for the migration on its own.
2. It runs the migration's statements and marks the row `applied`, in one transaction where SQLite allows it.
3. If anything fails, a transactional migration rolls back completely and the row is removed: the database is exactly as before.

**Telemetry partitions.** A telemetry migration never rewrites day or month tables. A column change updates the partition template; tables created afterwards get the new column, and older tables report it as missing until retention drops them. This keeps upgrades fast on a 40 GB file.

**Startup check.** Services start only on the schema versions the installed release runs on ([16.3](../../tool-blueprint.md#163-startup-preflight)). After installing a release by hand, for example with `apt install`, run `vakr db migrate` before starting services.

**Destructive migrations.** A migration that removes or rewrites data needs two flags: `--allow-destructive` (you mean it) and `--backup-confirmed` (you have kept the backup that `vakr db migrate` just wrote, preferably off the host). vakr re-verifies that backup before it continues. A fresh install has nothing to destroy, so it needs neither flag.

**Interrupted migrations (MIG-003).** If the host loses power or the process is killed mid-migration, the `in_progress` row survives and `vakr db status` reports `interrupted`:

- If the migration was transactional, SQLite has already undone its work. Run `vakr db migrate` again.
- If it was not transactional (rare: only statements SQLite refuses inside a transaction, such as `VACUUM`), it may be partly applied, and vakr refuses to continue. Restore the backup that `vakr db migrate` wrote just before it started (see [restore](#restore)), then run `vakr db migrate` again. The status output repeats the migration's own recovery notes.

Never edit `schema_migrations` by hand. vakr compares it with the migrations it ships and refuses a history it did not write.

## Backup

Run `vakr db backup` whenever you want a copy. It uses SQLite's online backup API, so services keep running and the copy is consistent. Before the copy is given its final name, vakr checks that it opens, passes `PRAGMA quick_check`, and is at the expected schema version. `vakr db migrate` takes the same backup of each database it is about to migrate, and self-update archives all databases before every update.

A telemetry backup is as large as the telemetry file, about 40 GB at the MVP workload, and takes several minutes. The 100 GB volume has room for one copy beside the live file; move older copies off the host. If space is short, back up the metadata only (`vakr db backup metadata`): it is small, and it is what you cannot rebuild, whereas telemetry is history.

Copy backups off the host on your normal schedule. Back up `/etc/vakr/secrets/credentials.toml` separately, with stronger protection.

## Restore

To restore a database from a backup (the steps are the same for `metadata.db` and `telemetry.db`):

1. Stop vakr: `sudo systemctl stop vakr.target`.
2. Check the backup: `vakr db status --database /var/lib/vakr/backups/<name>-<time>.db`. It should report `current`, or `pending` if the backup came from an older release.
3. Move the current files aside, including the WAL. A WAL left beside the restored file would be applied to the wrong database.

   ```sh
   cd /var/lib/vakr
   sudo mkdir -p broken && sudo mv metadata.db metadata.db-wal metadata.db-shm broken/ 2>/dev/null
   ```

4. Put the backup in place: `sudo install -o vakr -g vakr -m 0640 backups/metadata-<time>.db metadata.db`.
5. Run `vakr db status`, and `sudo -u vakr vakr db migrate` if it reports pending migrations.
6. Start vakr: `sudo systemctl start vakr.target`.

The restore keeps every UUID, so dashboards, history, and telemetry still match their devices and interfaces. Restoring an older telemetry backup leaves a gap from the backup time to the restore; the collectors and the peer's replication fill only what they still hold.

## Integrity checks

`vakr db status` and the health checks run `PRAGMA quick_check`. On `metadata.db` it takes well under a second at MVP size and covers the whole file. On `telemetry.db` the default covers the dictionaries, catalog, and staging tables; `vakr db status telemetry --full` checks every partition.

If `quick_check` fails, stop vakr, copy all three files of that database (`.db`, `-wal`, `-shm`) somewhere safe for the developers, and [restore](#restore) the newest good backup. Damage usually means a disk or filesystem fault, so check the disk before you trust the host again.

## Newer schema refused

`vakr db status` exits 2, and the service refuses to start, with a message such as:

```text
schema 2 was written by a newer vakr release; this release supports up to 1. Do not run this release on it: reinstall the newer release, or restore the backup taken before the upgrade
```

This happens when an older vakr runs against a database that a newer release has already migrated. Common causes are a manual downgrade, a rollback that skipped the database, or a database copied from a newer node. The older release cannot know what the newer migration changed, so it does not touch the file (MIG-004). Do one of the following:

- **Go forward.** Reinstall the newer release, for example with `vakr self-update --version <newer>`.
- **Go back.** Restore the backup from before the upgrade. `vakr self-update --rollback` restores self-update's pre-update archive (UPD-005). Otherwise use the `<name>-<time>.db` that `vakr db migrate` wrote before it migrated (see [restore](#restore)). Changes made after the upgrade are lost.

Other refusals name their cause: "not a vakr metadata database" or "not a vakr telemetry database" means the path points at another file, and "differs from this release's" means a different build migrated the database. In both cases, check the path, then restore a backup.
