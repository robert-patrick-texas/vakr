# Writing migrations

A schema change is a numbered SQL file in the package: `src/vakr/adapters/sqlite/metadata/sql/` or `src/vakr/adapters/sqlite/telemetry/sql/`. Versions run 1, 2, 3, … without gaps. Released files never change, because each database's journal stores their checksums. The operator side is in [database operations](../operations/database.md#migrations); the rules are in [blueprint 5.6](../../tool-blueprint.md#56-migration-rules) and Appendix E.14.

Contents:

- [The header](#the-header)
- [Telemetry: additive or breaking](#telemetry-additive-or-breaking)
- [When a release needs manual-update-required](#when-a-release-needs-manual-update-required)
- [Tests that guard this](#tests-that-guard-this)

## The header

Each file starts with `-- key: value` lines and ends its header at the first empty line:

```sql
-- description: Add the replication cursor (MPOS-004).
-- destructive: false
-- transactional: true
-- recovery: Adds one table in one transaction; on failure nothing changes.
-- data-volume: Creates an empty table. Finishes in milliseconds.
-- test-impact: MIG-002; the replication tests.
-- compat: additive

CREATE TABLE replication_cursor (...) STRICT;
```

`transactional` defaults to `true`. Don't write `BEGIN` or `COMMIT`: the runner owns transactions. `compat` is required for telemetry migrations from 0003 on, and not used for metadata.

## Telemetry: additive or breaking

Self-update's rollback never restores `telemetry.db`, because it is too large to copy. A release that has been rolled back may therefore meet a telemetry schema a newer release wrote. `compat` tells it whether it may carry on (E.14):

| `compat` | Meaning | An older release with the floor logic |
|---|---|---|
| `additive` | Only additions an older release can ignore | Runs on it: starts, ingests, `vakr db status` exits 0 |
| `breaking` | Anything else | Refuses it (MIG-004), naming this migration |

Additive allows exactly:

- `CREATE TABLE`, `CREATE VIEW`, and `CREATE INDEX`; a `UNIQUE` index only on a table this migration creates;
- `ALTER TABLE ... ADD COLUMN`, with `NOT NULL` only if there is a `DEFAULT`, because older code inserts rows without the column;
- triggers and `INSERT`s only on tables this migration creates.

Everything else is breaking: `DROP`, `RENAME`, `UPDATE`, `DELETE`, `PRAGMA`, and rows, triggers, or uniqueness on existing tables. A new partition template version is breaking too: older code reads a family's template without choosing a version, so it would build partitions from DDL it does not know.

When code in a newer release relies on an additive table or column, it must cope with an older release having written to the database in between. Rows the older release inserted have no value in the new columns, and the new tables were not kept up to date.

## When a release needs manual-update-required

Releases before the floor logic refuse every newer telemetry schema. Until every release that self-update may still upgrade from has the floor logic, the release that ships any telemetry migration must carry `manual-update-required` in its changelog section. That section is the release notes, so the update timer skips it (UPD-005). A breaking migration always needs the marker, and the notes should say how to step back, as [the rollups guide](../operations/rollups.md#going-back-to-a-release-without-the-projection) does for 0002.

The oldest release self-update must upgrade from is `OLDEST_UPGRADE_SOURCE` in `tests/unit/adapters/sqlite/telemetry/test_telemetry_schema.py`. Raising it is an owner decision. On 2026-10-27 the owner confirmed that no earlier release is in operation, and it was raised to the release published that day. Every release from 0.4.0 on has the floor logic, so today only a breaking migration needs the marker.

## Tests that guard this

- `test_telemetry_schema.py::test_a_second_telemetry_migration_needs_a_rollback_story`: the changelog section naming each telemetry migration carries the marker, unless it is additive and every upgrade source reads it.
- `test_sqlite_migration_compat.py`: the static additive check (`additive_problems`), run on every shipped `additive` migration.
- `test_telemetry_compat.py`: an older reader on a newer additive schema (it starts, writes, and maintains), and refusal of a breaking one.
