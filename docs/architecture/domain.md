# Domain model and observation contract

`vakr.domain` names what an operator reasons about: devices, interfaces, reachability, counters, freshness, and quality. Everything else (collectors, storage adapters, the API, the web UI, the TUI) exchanges these types through the [ports](storage.md). The package imports only the standard library (ARCH-001); `tests/unit/domain/test_domain_boundaries.py` enforces that.

This page covers the entities, the observation contract, the envelope encoding, and the append-only rules. The requirements are in the blueprint, sections [4.3](../../tool-blueprint.md#43-non-negotiable-correctness-invariants), [6.3](../../tool-blueprint.md#63-language-neutral-observation-boundary), [7](../../tool-blueprint.md#7-canonical-domain-model), and [10](../../tool-blueprint.md#10-telemetry-schemas-and-retention-behavior).

## Module map

| Module | Contents |
|---|---|
| `enums`, `quality` | Shared state vocabularies and combinable quality flags |
| `ids` | Typed UUID identifiers, `new_id`, `parse_id` |
| `errors` | `DomainError`, `ValidationError`, `NotFoundError`, `ConflictError`, `DuplicateError`, `OwnershipError` |
| `timestamps` | The UTC rule and the integer-microsecond encoding |
| `ownership` | `ManagedBy` (file or API) |
| `inventory` | `Site`, `Device`; re-exports `ManagedBy`, `Tag`, `Group`, `Collector` |
| `catalog` | `Tag`, `Group`, `Collector` |
| `interfaces` | `Interface`, `InterfaceAnnotation`, and the owned/observed field groups |
| `control` | `CollectionPolicy`, `IdentityHistoryEntry`, `AuditEvent` |
| `reviews` | `IdentityReview`, `ReviewState`: discovered interfaces whose identity waits for an administrator (IFID-007, E.26) |
| `users`, `sessions` | `Role`, `User`, `LoginFailure`, `Session`, `KioskToken`, `canonical_username`: local accounts and their sessions; hashes and token digests stay out of `repr` (SEC-005, SEC-010, E.12) |
| `observations` | `IcmpObservation`, the `Observation` union; re-exports every observation type |
| `snmp_observations`, `interface_observations`, `polls` | `SnmpDeviceObservation`, `InterfaceObservation`, `DevicePoll`, `CollectorHealthObservation` |
| `codec`, `codec_fields`, `codec_values` | Plain-dict encoding of every observation type |
| `json_values` | JSON values and canonical bytes |
| `envelope` | `ObservationEnvelope`, `seal`, `open_envelope`, wire dict and bytes |
| `state` | Current-state read models and freshness; re-exports the read models below |
| `series`, `paging`, `rollups` | `TimeWindow`, `Metric`, `Series`; `PageRequest`, `Page`; `RollupBucket` |
| `series_assembly`, `projections` | Backend-neutral rules every telemetry adapter applies |

## Identity

Every public identity is an application-assigned UUID (META-001, META-002, API-002). A device's UUID doesn't depend on its name, address, `sysName`, or `sysObjectID`. An interface's UUID doesn't depend on its `ifIndex` (invariant 6): `if_index` is an observed attribute that discovery updates. Each kind of ID is its own `NewType` (`DeviceId`, `InterfaceId`, and so on), so a type checker catches an interface ID passed where a device ID belongs.

New IDs are UUIDv7 (`uuid.uuid7()`). Their first 48 bits are a millisecond timestamp, so IDs created together sort together and B-tree inserts stay local. `parse_id` accepts only the 36-character hyphenated form, in either case, and rejects the nil and max UUIDs. Database row IDs never leave an adapter.

## Inventory and control entities

All records are frozen, slotted dataclasses that check their invariants in `__post_init__`, so an invalid record can't exist anywhere. A name has no surrounding whitespace and no control characters. A time is aware UTC. A counter is an `int`, never a `bool`. A rate is a finite, non-negative `float`.

| Entity | Purpose | Natural key |
|---|---|---|
| `Device` | Polled device: poll address (canonical IP literal or RFC 1123 host name), preferred family, site, `AdminState`, per-device intervals and `ReachabilityStrategy`, credential **reference name**, comment, tags, groups | `name` |
| `Interface` | Logical interface with persistent identity (see below) | none; list by device |
| `InterfaceAnnotation` | Interest class (`PRIMARY` is the high-priority flag), sticky, `is_wan_circuit`, provider, circuit ID, contracted and capacity-override bps, site override, public-dashboard eligibility, display rank | n/a |
| `Site`, `Tag`, `Group` | Navigation and policy scoping | `name` |
| `Collector` | A polling position; every observation names one | `name` |
| `CollectionPolicy` | Ordered rule document stored verbatim as JSON-compatible data; `vakr.policy` interprets it | `name` |
| `IdentityHistoryEntry` | One identity decision: outcome, source, prior and observed `ifIndex`, scores, evidence, actor (IFID-005) | append-only |
| `AuditEvent` | Actor, action, object, time, outcome, request ID, redacted summary (SEC-018) | append-only |

The blueprint names the WAN-circuit field `is_wan_circuit` in the domain and `wan_circuit` in configuration files (META-012). The domain follows that.

### Versions and ownership

Every editable record carries an integer `version`, starting at 1, for optimistic concurrency (DB-003). A write names the version it read. A repository refuses a stale write with `ConflictError` and never silently overwrites.

Every editable record also carries `managed_by` (META-008, blueprint [11.2](../../tool-blueprint.md#112-file-versus-api-ownership)). `ManagedBy.permits(writer)` is true only when the writer is the same file, or when the record and the writer are both API-managed. A UI or API edit of a file-managed record raises `OwnershipError`, and so does a file reload that would overwrite an API-managed record.

### Interface field groups

Interfaces have three writers, so their fields fall into groups (`vakr.domain.interfaces`):

| Group | Fields | Written by |
|---|---|---|
| Observed | `if_index`, `if_name`, `if_descr`, `if_alias`, `if_type`, `mac`, `if_speed`, `if_high_speed`, `addresses`, `description_tags` | Discovery, through `update_observed`; no ownership check |
| Owned | `comment`, `tags`, `annotation` | The owning source, through `update` |
| Lifecycle | `lifecycle` | `set_lifecycle` only |
| Fixed | `id`, `device_id`, `managed_by`, `created_at` | Never changed |

Operator tags (`tags`) and description-derived tags (`description_tags`) are separate fields, so a description change never removes a tag someone added in vakr (META-013).

## Observations

An observation is an immutable fact (blueprint [7.2](../../tool-blueprint.md#72-observation-entities)). Every observation carries:

- `observation_id`, the deduplication key (STORE-009);
- `collector_id`, because evidence from different positions is kept apart (STATE-004);
- an aware **UTC** timestamp, with naive and non-UTC times refused (invariant 11);
- `quality`, the combinable `Quality` flags.

Observations that come from a poll also carry `poll_id`.

| Type | Subject | Notes |
|---|---|---|
| `DevicePoll` | device | Scheduled, started, and completed times must agree with `execution`. Its timestamp is the **scheduled** time, the schedule anchor (invariant 10). |
| `IcmpObservation` | device | `rtts_us` is a tuple of integer microseconds, with `None` for a probe that got no reply. `sent`, `received`, and `loss_pct` are derived from it. Summary statistics are `None` when nothing replied. |
| `SnmpDeviceObservation` | device | Request, response, timeout, and retry counts; `sys_uptime_ticks` is `None` when not read. |
| `InterfaceObservation` | interface | Raw counters are `int` or `None`; derived rates are `float` or `None`. |
| `CollectorHealthObservation` | collector | A lean subset of 10.4. |

Three rules from 4.3 shape these types:

- **Missing is `None`, never `0`** (invariant 1). An agent that didn't return a counter produced no value; it didn't report an idle link.
- **No rate without a valid baseline** (invariant 5, RATE-004). An `InterfaceObservation` may carry rates or deltas only when `baseline_state` is `VALID` and no flag in `BASELINE_INVALIDATING` is set. Construction refuses anything else, so a reset can't become a spike.
- **No descriptive metadata in telemetry** (STORE-008). Names, aliases, tags, and circuit labels stay on `Interface` and `Device`.

The `-1` "no reply" sentinel of ICMP-004 is a storage detail. Adapters write `-1` and read it back as `None`, so no calculation in the domain can mistake it for a latency (ICMP-005).

## Envelope encoding

Observations cross process boundaries inside an `ObservationEnvelope` (blueprint 6.3, ARCH-002). The JSON form is the definition; Python classes only implement it. The durable spool (M3) will store the same envelope inside length-delimited MessagePack records. Here is the `icmp_impaired` golden fixture, pretty-printed for reading:

```json
{
  "batch_id": "01929b4a-0000-7000-8000-00000000b001",
  "checksum": "sha256:25e14002b487e9c803fdca1c90525688e444645e32473177ae7e95c4d528c890",
  "collector_id": "01929b4a-0000-7000-8000-00000000c001",
  "observation_id": "01929b4a-0000-7000-8000-000000000001",
  "observation_type": "icmp",
  "payload": {
    "address_family": "ipv4", "avg_rtt_us": 699,
    "device_id": "01929b4a-0000-7000-8000-00000000d001", "jitter_us": 377,
    "max_rtt_us": 1033, "median_rtt_us": 883, "min_rtt_us": 0,
    "rtts_us": [812, null, 1033, 954, 0], "source_address": "198.51.100.2",
    "state": "impaired", "target_address": "192.0.2.10"
  },
  "poll_id": "01929b4a-0000-7000-8000-00000000f001",
  "quality": 4194305,
  "schema_version": 1,
  "timestamp_us": 1790856000123456
}
```

| Key | Meaning |
|---|---|
| `schema_version` | `1`. A reader refuses any other value with a message to upgrade. |
| `observation_type` | `device_poll`, `icmp`, `snmp_device`, `interface`, or `collector_health` |
| `observation_id`, `batch_id`, `collector_id` | UUIDs |
| `poll_id` | UUID; `null` for `collector_health`, and required for every other type |
| `timestamp_us` | Observation time; a poll's scheduled time |
| `payload` | Type-specific fields, listed field by field in `vakr.domain.codec_fields.SPECS` |
| `quality` | Quality flags as one integer, 0 to 2^63-1. Readers keep bits they don't know. |
| `checksum` | `sha256:` plus the lower-case hex SHA-256 of the canonical bytes of the envelope **without** its `checksum` key. Covering the header as well as the payload catches a flipped bit in `observation_id` too. |

Value encoding:

- **UUIDs** are canonical lower-case hyphenated strings.
- **Timestamps** are **integer microseconds since 1970-01-01T00:00:00Z**, and their keys end in `_us` (`timestamp_us`, `started_at_us`). Integers sort and round-trip exactly in every language, and `datetime` has microsecond resolution, so nothing is lost.
- **Enums** use their stored values: strings for state enums, integers for the IF-MIB status enums.
- **Missing values** are `null`. Every key is always present.
- **Integers** are exact decimal at any size. Counters reach 2^64 - 1, so a consumer must parse them as integers, not doubles.
- **Floats** use the shortest round-trip form, as Python `repr` writes it: `1.0`, `0.0001`, `1e-05`, `1e+16`. Exponent form is used below 1e-4 and from 1e16, with a sign and at least two exponent digits. NaN, infinity, and negative zero are refused.

**Canonical bytes** are UTF-8 JSON with keys sorted by code point, `,` and `:` separators and no other whitespace, non-ASCII characters written raw, and `"`, `\`, and control characters escaped as Python's `json` module escapes them. A Go implementation must disable HTML escaping (`SetEscapeHTML(false)`) and format floats as above.

### Golden fixtures

`tests/fixtures/observations/*.json` hold the canonical envelope bytes, with no trailing newline, of every observation in `tests/unit/domain/golden_observations.py`. Between them they cover all five types, `null` RTTs and counters, combined quality flags, counters above 2^53, exponent-form floats, a negative integer, sub-second times, and non-ASCII text. `test_domain_envelope.py` proves the Python codec reproduces each file byte for byte and decodes it to an equal observation. A future Go codec must pass the same files (blueprint [24](../../tool-blueprint.md#24-rules-for-a-future-go-component)).

After a deliberate schema change, regenerate them with:

```sh
uv run python -m tests.unit.domain.golden_observations --write
```

## Read models

- `TimeWindow` is a bounded, half-open UTC range `[start, end)` (API-006).
- `Series` holds one subject, one collector, and one metric. Its points are strictly increasing in time. An observation whose value is missing stays in the series as a `None` point, and every rollup bucket in a rollup span appears, as `None` when no bucket was stored (STORE-012). `resolutions` discloses which tier (`NATIVE` or `ROLLUP`, with bucket width) produced each part of the window (STATE-005, DB-006). `truncated` says whether `max_points` (at most 1,200, per 10.6) cut the answer short.
- `CurrentDeviceState` and `CurrentInterfaceState` carry the evidence observations themselves, plus observed time, age, collector, the effective interval, quality, and freshness (STATE-003, TIME-005). Data is stale when its age exceeds `max(2.5 × interval, interval + 30 s)` (Appendix E.3).
- `PageRequest` has a limit of 1 to 500 (default 100) and an opaque keyset cursor. `Page` carries the items, the next cursor, and the total (API-005).

## Append-only rules

These values are part of stored data and of the cross-language contract. Changing one is a schema decision, not a refactor:

- **Quality bit positions** never move and are never reused; new flags are appended (`tests/unit/test_domain_vocabulary.py` pins them).
- **Enum values** are stored strings and integers. Members may be appended; existing values never change.
- **Observations** are never edited. A correction is a new observation.
- **Identity history and audit events** are only appended, never updated or deleted.
- **Payload schemas** change only with a `SCHEMA_VERSION` bump, a backward-read plan for spooled records, and regenerated golden fixtures.
- **Inventory records** are never hard-deleted by the ports. Retiring a device or interface is a state change, and history stays (META-009, LIFE-002).
