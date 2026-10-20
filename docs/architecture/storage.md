# Storage ports and adapters

All persistence goes through typed `Protocol`s in `vakr.ports` (blueprint [5.2](../../tool-blueprint.md#52-mandatory-persistence-ports)). Scheduler, collection, API, web, and TUI code depend only on those protocols and on the [domain model](domain.md). Adapters under `vakr.adapters` implement them, and they are the only place a database driver, SQL, or a driver row may appear (rules 1 and 2). Every adapter proves it behaves the same as the others by passing the shared contract suites (rule 6, blueprint [19.3](../../tool-blueprint.md#193-metadata-and-telemetry-adapter-contract-tests)).

| Port module | Protocols |
|---|---|
| `vakr.ports.metadata` | `MetadataUnitOfWork`; `DeviceRepository`, `InterfaceRepository`, `SiteRepository`, `TagRepository`, `GroupRepository`, `PolicyRepository`, `CollectorRepository`, `AuditRepository`; generic `EntityRepository` and `NamedRepository`; filters |
| `vakr.ports.telemetry` | `TelemetryBackend`; `ObservationWriter`, `ObservationQuery`, `CurrentStateQuery`, `RollupRepository`, `CollectorHealthRepository`, `RetentionManager`; `TelemetryCapabilities`, `WriteResult`, `RetentionPolicy`, `RetentionReport` |
| `vakr.ports.clock` | `Clock`, `SystemClock`, `ManualClock` |

The 5.2 `TagGroupRepository` is split into `TagRepository` and `GroupRepository`, which share the generic protocol. `DashboardRepository` and `UserSessionRepository` arrive with their features in M4. `MigrationManager` is in `vakr.ports.migrations`, and the SQLite implementation is in `vakr.adapters.sqlite.migrations` (see the [database operations guide](../operations/database.md)).

## Metadata: units of work

A unit of work owns the transaction (rule 4). An operation that adds a device, tags it, and records an audit event commits all three or none (DB-002):

```python
with uow_factory() as uow:
    uow.devices.add(device)
    uow.audit.append(event)
    uow.commit()
```

Leaving the block without `commit()` rolls back, and so does an exception, which then propagates. After `commit()` the unit of work can make and commit further changes. A conflict caused by another unit of work may surface from the repository call or from `commit()`; either way nothing from the failed unit of work is committed.

Repository behaviour every backend reproduces:

- `get` and `get_by_name` return `None` when nothing matches.
- `add` requires `version == 1`. It raises `DuplicateError` for a reused ID or natural key and `NotFoundError` for a missing referenced site, tag, group, or device.
- `update(record, writer=...)` checks, in this order: the record exists (`NotFoundError`); `stored.managed_by.permits(writer)` (`OwnershipError`, META-008); `record.version == stored.version` (`ConflictError`, DB-003); and that no fixed field changed (`ValidationError`). It then stores the record with `version + 1` and returns what it stored.
- Interfaces have three write paths: `update` for owned fields, `update_observed` for discovered fields with no owner check, and `set_lifecycle`. Each path may change only its own field group; see [interface field groups](domain.md#interface-field-groups).
- Nothing is hard-deleted. `set_admin_state(RETIRED)` and `set_lifecycle(RETIRED)` are the soft deletes, and records, identity history, and telemetry all stay (META-009). Retired records leave default lists unless a filter asks for them.
- Lists are pages (API-005) in a stable total order. Named records sort by `(name, id)`, policies by `(order, name, id)`, interfaces by `(device_id, if_name, id)`, identity history oldest first, and audit newest first. Strings compare by code point and UUIDs by their canonical text, so PostgreSQL must use `COLLATE "C"`. Cursors are opaque keyset cursors (`vakr.domain.paging.encode_cursor`) and never contain row IDs (DB-007).

## Telemetry

- **Writes.** `write_batch(batch_id, observations)` accepts any mix of observation types, up to `capabilities.max_batch_rows`. It is atomic and idempotent on `observation_id` (STORE-009, STORE-010). The first write of an ID wins, a replay counts as `duplicates`, and an oversized or invalid batch writes nothing.
- **Series.** `interface_series` and `icmp_series` take one subject, one collector (STATE-004), one metric, a bounded `TimeWindow`, and `max_points` (1 to 1,200). Backends build the result with `vakr.domain.series_assembly`. `resolution_spans` splits the window at the native horizon, using rollup buckets before it when the backend has rollups and native samples from it on, and `assemble_series` applies the shared rules: gap points, larger UUID wins on equal timestamps, nothing duplicated at the boundary, and truncation disclosed. Adapters must not reimplement those rules.
- **Raw reads.** `read_raw` pages stored observations of one type for one subject, ordered by `(timestamp, observation_id)`. Its `total` is `None`, because counting raw telemetry is not bounded-cost.
- **Current state.** `CurrentStateQuery` returns the latest observation not flagged `NOT_ATTEMPTED`, per collector, built with `vakr.domain.projections` so that age, freshness (E.3), and quality are computed the same way everywhere (DB-005).
- **Rollups and retention.** `RollupRepository` stores `RollupBucket`s by identity, so re-running a job replaces buckets (RET-009). `RetentionManager.apply(now)` removes native data older than the policy allows. With rollups, it never removes data past `coverage_end()`, and it aligns the cutoff to a bucket boundary (E.10). The cutoff becomes the native horizon that series use.

### Capabilities

Backends declare their differences in `TelemetryCapabilities` instead of `if backend == ...` checks (rule 5):

| Field | Meaning |
|---|---|
| `rollups` | Rollups are stored and served. With `False`, every `RollupRepository` method raises `NotImplementedError`, series are all native, retention ignores coverage, and DB-006 is reported as not applicable. |
| `max_batch_rows` | Largest batch `write_batch` accepts (STORE-011). |
| `time_partitioned` | Native data lives in time partitions (Compact); informational. |

## Contract suites

`tests/contract/contract_*.py` define backend-neutral test classes. Their names don't start with `Test`, so pytest doesn't collect them where they are defined.

| Suite | Covers |
|---|---|
| `MetadataCrudContract` | DB-001 round trips, order, paging, filters, duplicates, references; DB-007 |
| `MetadataRulesContract` | DB-002 rollback, DB-003 stale versions, META-008 ownership and interface write paths, META-009 soft delete, IFID-005 history, SEC-018 audit |
| `TelemetryWriteContract` | DB-004 round trips of every type, `None` values, quality, ordering; STORE-009 idempotency; STORE-010 rejected batches |
| `TelemetrySeriesContract` | STORE-012 gaps and quality, API-006 point cap, STATE-004 per-collector series, DB-006 native/rollup boundary, rollup repository, retention |
| `TelemetryStateContract` | DB-005 latest valid evidence, freshness, ties, listings; OBS-001 collector health; DB-007 |

The suites take their implementation from two fixtures:

- `metadata_uow_factory` is a `MetadataUnitOfWorkFactory` that opens units of work on one fresh, empty store.
- `telemetry_factory` is a `TelemetryBackendFactory` that returns views of one fresh, empty store.

At most one unit of work holds uncommitted writes at a time, so single-writer SQLite can run every test. One test opens a reader beside an uncommitted writer, which WAL mode allows (DB-008). Tests that need rollups read the capability and skip with "not applicable".

`vakr.adapters.memory` is the reference implementation. It passes every suite, twice for telemetry (rollups on and off), and serves as the storage double for application tests. It isn't durable; never use it in a service.

## Adding a backend

1. Implement the protocols in a new adapter package, such as `vakr.adapters.metadata.sqlite`. Map every driver error to a `vakr.domain.errors` exception: a unique-constraint violation becomes `DuplicateError`, a zero-row versioned `UPDATE` becomes `ConflictError`, and a stale WAL snapshot (`SQLITE_BUSY_SNAPSHOT`) becomes `ConflictError`. No driver type may cross the port.
2. Store timestamps as integer microseconds (`vakr.domain.timestamps.to_epoch_us`), store quality as a signed 64-bit integer, and store a lost ICMP probe as `-1`, reading it back as `None` (ICMP-004).
3. Build series with `assemble_series` and current state with `vakr.domain.projections`, from whatever your queries fetch.
4. Create `tests/contract/<backend>/` containing an `__init__.py`, a `conftest.py` that defines `metadata_uow_factory` and/or `telemetry_factory` for a fresh store in `tmp_path`, and a `test_<backend>_contracts.py` that subclasses each suite with an empty body, as `tests/contract/memory/test_memory_contracts.py` does.
5. Run `uv run pytest tests/contract/<backend>` and fix the adapter until it passes **unchanged** suites. If a suite assumption is wrong for every backend, change the suite and the reference adapter together, never per backend.
