# ADR-021: Compact profile SQLite telemetry layout

| Field | Value |
|---|---|
| Status | Accepted on benchmark evidence; owner sign-off pending. Amended at merge: rollup p95 is nearest-rank (see the end) |
| Date | 2026-10-20 |
| Decides | Physical layout of Compact-profile telemetry in SQLite: tables, keys, partitions, PRAGMAs, batching, idempotency, rollups, retention |
| Requirements | Blueprint 5.1, 5.3, 10.1-10.6, 27.2-27.5, Appendix E.8 and E.10; STORE-009, STORE-010, STORE-011, RET-001..RET-009, DASH-003, DASH-004, STATE-001..STATE-004, MVP-ACC-05 |
| Evidence | [Benchmark report](../performance/adr021-compact-sqlite-benchmark.md), scripts in [`tests/performance/sqlite_compact/`](../../tests/performance/sqlite_compact/__init__.py) |

## Context

The Compact profile keeps 30 days of native one-minute telemetry and 15-minute rollups through day 400 in SQLite (blueprint 5.1). With two positions, each node stores its own and its peer's observations (27.5), so one node holds 3,200 interface series, 200 ICMP series and 200 SNMP device series at 60-second intervals: 3,600 rows a minute, 5.18 million a day. The planning estimate in 27.3 was 22-42 GB per node at 80-150 bytes per row, to be replaced by measured values. Blueprint 5.1 already expected one table set per UTC day for native data and per month for rollups, pending this benchmark.

The benchmark compared:

* **A**: one rowid table per observation type, with a unique secondary index on `(series_key, collector_key, ts_us)`.
* **B**: one `WITHOUT ROWID` table per type, clustered on that key, with integer surrogate keys from dictionary tables.
* **C**: B split into one table set per UTC day (native) and per UTC month (rollups), with retention by `DROP TABLE`.
* **C+H**: C plus a small, time-ordered staging ("hot") table per type that absorbs the 10-second commits and is drained into the day tables every 15 minutes. This candidate was added because C showed a problem: see Rationale.
* **C′** (one file per day, attached on demand) was not built: C+H removed the problem C showed, and the bundled SQLite allows only 10 attached databases (see Alternatives).

## Decision

Use **layout C+H** in one telemetry database file, separate from the metadata database.

### Tables and keys

* **Dictionaries.** `dict_collector`, `dict_device` and `dict_interface` map 16-byte UUIDs to `INTEGER PRIMARY KEY` surrogates. The keys are node-local and never reused; replication and the API carry UUIDs, never keys. `dict_interface.device_key` records ownership, so device-scoped queries need no metadata join.
* **Vocabularies.** Shared enums from `vakr.domain.enums` are stored as 1-based integer codes. `vocab_code (vocab, value, code)` records the mapping, so the file explains itself. Codes are append-only, like `Quality` bit positions.
* **Native, one set per UTC day:** `obs_iface_dYYYYMMDD`, `obs_icmp_dYYYYMMDD`, `obs_snmp_dYYYYMMDD`. These are `WITHOUT ROWID, STRICT` tables with `PRIMARY KEY (series_key, collector_key, ts_us)`, where `series_key` is `interface_key` or `device_key`. Columns are exactly blueprint 10.1-10.3; the observation UUID is a 16-byte `BLOB`, stored but not indexed.
* **Staging, one per type:** `obs_iface_hot`, `obs_icmp_hot`, `obs_snmp_hot`. These have the same columns but `PRIMARY KEY (ts_us, series_key, collector_key)`, so inserts append at the right edge of the B-tree. `hot_state.boundary_us` holds the boundary.
* **Rollups, one set per UTC month:** `roll_iface_15m_mYYYYMM` and `roll_icmp_15m_mYYYYMM`. These are `WITHOUT ROWID, STRICT` with `PRIMARY KEY (series_key, collector_key, bucket_start_us)`. The bucket end is `bucket_start_us + 900 s`, because each table has one width.
* **Catalog:** `partition_catalog (table_name, kind, start_us, end_us, state)` lists every day and month table. Queries resolve tables from it and never parse `sqlite_schema`.

Recommended DDL (one day and one month shown; the adapter substitutes the date):

```sql
CREATE TABLE dict_collector (collector_key INTEGER PRIMARY KEY, collector_uuid BLOB NOT NULL UNIQUE) STRICT;
CREATE TABLE dict_device (device_key INTEGER PRIMARY KEY, device_uuid BLOB NOT NULL UNIQUE) STRICT;
CREATE TABLE dict_interface (interface_key INTEGER PRIMARY KEY, interface_uuid BLOB NOT NULL UNIQUE,
  device_key INTEGER NOT NULL REFERENCES dict_device (device_key)) STRICT;
CREATE INDEX dict_interface_device ON dict_interface (device_key);
CREATE TABLE vocab_code (vocab TEXT NOT NULL, value TEXT NOT NULL, code INTEGER NOT NULL,
  PRIMARY KEY (vocab, value), UNIQUE (vocab, code)) WITHOUT ROWID, STRICT;
CREATE TABLE partition_catalog (table_name TEXT PRIMARY KEY, kind TEXT NOT NULL,
  start_us INTEGER NOT NULL, end_us INTEGER NOT NULL, state TEXT NOT NULL DEFAULT 'open') WITHOUT ROWID, STRICT;
CREATE TABLE hot_state (name TEXT PRIMARY KEY, value INTEGER NOT NULL) WITHOUT ROWID, STRICT;

CREATE TABLE obs_iface_d20260901 (
  interface_key INTEGER NOT NULL, collector_key INTEGER NOT NULL, ts_us INTEGER NOT NULL,
  observation_uuid BLOB NOT NULL, poll_uuid BLOB NOT NULL, device_key INTEGER NOT NULL,
  ifindex_observed INTEGER, admin_status INTEGER, oper_status INTEGER,
  in_octets INTEGER, out_octets INTEGER, in_packets INTEGER, out_packets INTEGER,
  in_errors INTEGER, out_errors INTEGER,
  in_bps REAL, out_bps REAL, in_pps REAL, out_pps REAL,
  in_error_delta INTEGER, out_error_delta INTEGER, in_error_rate REAL, out_error_rate REAL,
  sample_elapsed_us INTEGER, counter_family INTEGER, counter_discontinuity_ticks INTEGER,
  baseline_state INTEGER NOT NULL, quality_flags INTEGER NOT NULL,
  PRIMARY KEY (interface_key, collector_key, ts_us)
) WITHOUT ROWID, STRICT;

CREATE TABLE obs_icmp_d20260901 (
  device_key INTEGER NOT NULL, collector_key INTEGER NOT NULL, ts_us INTEGER NOT NULL,
  observation_uuid BLOB NOT NULL, poll_uuid BLOB NOT NULL,
  address_family INTEGER NOT NULL, source_address BLOB, target_address BLOB NOT NULL,
  rtt_1_us INTEGER NOT NULL, rtt_2_us INTEGER NOT NULL, rtt_3_us INTEGER NOT NULL,
  rtt_4_us INTEGER NOT NULL, rtt_5_us INTEGER NOT NULL,          -- -1 = no reply
  sent_count INTEGER NOT NULL, received_count INTEGER NOT NULL, lost_count INTEGER NOT NULL,
  loss_percent REAL NOT NULL,
  min_rtt_us INTEGER, avg_rtt_us INTEGER, median_rtt_us INTEGER, max_rtt_us INTEGER, jitter_us INTEGER,
  state INTEGER NOT NULL, quality_flags INTEGER NOT NULL,
  PRIMARY KEY (device_key, collector_key, ts_us)
) WITHOUT ROWID, STRICT;

CREATE TABLE obs_snmp_d20260901 (
  device_key INTEGER NOT NULL, collector_key INTEGER NOT NULL, ts_us INTEGER NOT NULL,
  observation_uuid BLOB NOT NULL, poll_uuid BLOB NOT NULL,
  snmp_version INTEGER NOT NULL, request_count INTEGER NOT NULL, response_count INTEGER NOT NULL,
  timeout_count INTEGER NOT NULL, retry_count INTEGER NOT NULL, elapsed_ms INTEGER,
  sys_uptime_ticks INTEGER, uptime_continuity INTEGER NOT NULL, availability_state INTEGER NOT NULL,
  error_code TEXT, quality_flags INTEGER NOT NULL,
  PRIMARY KEY (device_key, collector_key, ts_us)
) WITHOUT ROWID, STRICT;

-- Staging: same columns as the day table, time-first key. Likewise obs_icmp_hot, obs_snmp_hot.
CREATE TABLE obs_iface_hot (<columns of obs_iface_d…>, PRIMARY KEY (ts_us, interface_key, collector_key))
  WITHOUT ROWID, STRICT;

CREATE TABLE roll_iface_15m_m202609 (
  interface_key INTEGER NOT NULL, collector_key INTEGER NOT NULL, bucket_start_us INTEGER NOT NULL,
  device_key INTEGER NOT NULL,
  sample_count INTEGER NOT NULL, expected_count INTEGER NOT NULL,
  valid_count INTEGER NOT NULL, available_count INTEGER NOT NULL,
  in_bps_max REAL, in_bps_p95 REAL, in_bps_avg REAL, out_bps_max REAL, out_bps_p95 REAL, out_bps_avg REAL,
  in_error_sum INTEGER, out_error_sum INTEGER,
  flagged_count INTEGER NOT NULL, quality_any INTEGER NOT NULL,
  PRIMARY KEY (interface_key, collector_key, bucket_start_us)
) WITHOUT ROWID, STRICT;

CREATE TABLE roll_icmp_15m_m202609 (
  device_key INTEGER NOT NULL, collector_key INTEGER NOT NULL, bucket_start_us INTEGER NOT NULL,
  sample_count INTEGER NOT NULL, expected_count INTEGER NOT NULL,
  sent_sum INTEGER NOT NULL, received_sum INTEGER NOT NULL, lost_sum INTEGER NOT NULL,
  rtt_min_us INTEGER, rtt_median_us INTEGER, rtt_p95_us INTEGER, rtt_max_us INTEGER,
  jitter_max_us INTEGER, jitter_p95_us INTEGER,
  snmp_ok_count INTEGER NOT NULL, snmp_fail_count INTEGER NOT NULL, restart_count INTEGER NOT NULL,
  quality_any INTEGER NOT NULL,
  PRIMARY KEY (device_key, collector_key, bucket_start_us)
) WITHOUT ROWID, STRICT;
```

Column notes for the adapter:

* `ts_us` is Unix microseconds UTC.
* SQLite `INTEGER` is signed 64-bit, so HC counters at or above 2^63 must be stored as `value - 2**64` and converted back on read. Python's `sqlite3` raises `OverflowError` otherwise.
* Missing values are `NULL`, never 0. `NULL` costs one header byte.
* Rollup bit rates are rounded to whole bits per second. SQLite then stores an integral `REAL` as a 3-6 byte integer, which cut rollup rows from 102 to 73 bytes. Rounding native rates is an open question (see Risks).
* ICMP loss in rollups is derived from `sent_sum` and `lost_sum`, never stored as a percentage (RET-008).

### PRAGMAs

| Setting | Value | Why |
|---|---|---|
| `page_size` | 4096 (the default) | 8 KiB saved 5-6% of space but cut C+H ingest throughput by 13% and wrote more bytes per hour. |
| `journal_mode` | `WAL` | Readers never block the writer (5.3). |
| `synchronous` | `FULL` | A commit is durable before the spool is acknowledged (STORE-010). It costs about 5 ms per commit, which is 0.05% of writer time at six commits a minute. With `NORMAL`, a power cut could undo commits the spool had already released. |
| `wal_autocheckpoint` | 1000 (the default) | With staging, a 10-second commit writes tens of pages, so checkpoints are small. 10,000 pages measured 21% faster with 8% fewer bytes written in a sweep: an optional tuning, not needed for any criterion. |
| `journal_size_limit` | 67108864 (64 MiB) | Caps the WAL left after any checkpoint reset. The post-move `TRUNCATE` empties it anyway. |
| `cache_size` | -65536 (64 MiB) per connection | Holds the staging table and the day table's leaf pages; every run used it. |
| `auto_vacuum` | `NONE` | Dropped day tables go to the freelist and the next day reuses them, so the file stops growing at steady state. |
| `foreign_keys` | `ON` | Blueprint 5.3. |
| `busy_timeout` | 5000 | Blueprint 5.3. The single writer rarely waits. |
| `temp_store` | `MEMORY` | Sorting for rollups and rankings stays off disk. |

### Writing, batching and durability

* One writer thread owns the connection (5.3). It flushes one transaction (`BEGIN IMMEDIATE` … `COMMIT`) every **10 seconds** (STORE-011's 5-10 s; TEST-005's under-10-second observation-to-query latency). At this workload a flush is about 600 rows.
* A row goes to staging when `ts_us >= boundary`; otherwise it goes straight to its day table, which is the path for late rows such as a peer's backfill.
* Every **15 minutes** the writer moves rows with `ts_us < now - 2 min` from staging into their day tables. It inserts them in key order, deletes them from staging, and advances `boundary_us`, all in one transaction. Only the mover moves the boundary, so a row is in staging exactly when `ts_us >= boundary_us`. Readers never see a row twice or not at all, and a replayed row always meets its earlier copy in the same table.
* Straight after each move, the writer runs `PRAGMA wal_checkpoint(TRUNCATE)`. Without it, two readers querying back to back kept every automatic (passive) checkpoint from resetting the WAL, which grew to 1.19 GB in 60 seconds of full-speed ingest. `journal_size_limit` applies only when the WAL resets, so it could not help. With the truncating checkpoint the WAL peaked at 14 MB. The checkpoint took 309 ms (p50) and 402 ms (max) once every 15 minutes and never reported busy, so readers were never starved.
* The spool is acknowledged only after `COMMIT` returns (STORE-010).

### Idempotency (STORE-009)

Use the natural primary key `(series_key, collector_key, ts_us)` with `INSERT OR IGNORE`, and keep the observation UUID unindexed:

* The key is needed anyway for clustering, so deduplication costs nothing.
* A unique UUID index added 39 bytes per row (+26% size) and cost 3-21% of ingest throughput.
* Replaying committed batches inserted 0 rows in every layout, at 130,000-260,000 rows/s.
* The key cannot collide for distinct observations: one collector produces at most one observation per series per poll, at microsecond resolution.

### Queries

* **One series** (graphs): resolve tables from the catalog for `[t0, t1)`, then build one arm per table plus the staging arm, joined with `UNION ALL`. Each arm is a primary-key range: `WHERE series_key = :s AND collector_key = :c AND ts_us >= :t0 AND ts_us < :t1`.
* **Downsampling** to at most 1,200 points uses `GROUP BY (ts_us - :t0) / :width` with `COUNT`, `MIN`, `MAX` and `AVG` in SQL. Widths come from a fixed ladder (60 s … 1 day), so 7 d uses 10 minutes, 30 d one hour, and 400 d eight hours.
* **Whole fleet** (top utilization DASH-003, top errors DASH-004, rollups, current state): drive from the dictionaries with `FROM dict_interface d CROSS JOIN dict_collector c CROSS JOIN <day table> t ON …`. `CROSS JOIN` fixes the join order, so SQLite seeks once per series instead of scanning a day. The staging arm is a time-range scan.
* **Current state:** read the newest staged row per series (`SELECT *, MAX(ts_us) … GROUP BY series`, which uses SQLite's bare-column rule). Fall back to the day tables newest first, one seek per series, for series with no staged row. This keeps a stale interface's true age (STATE-003). A separately upserted projection table was measured and rejected: it halved ingest throughput, and it is not needed for speed.

### Rollups

* Every 15 minutes, once a bucket's end is more than 10 minutes in the past (E.10), one statement computes all 3,200 interface rows from day plus staging rows and writes them with `INSERT OR REPLACE`. Re-running a bucket is therefore safe (RET-009).
* The p95 is nearest-rank, computed in Python with `vakr.logic.stats.p95` (see the amendment below). The benchmark measured Hyndman-Fan type 7 (linear interpolation) through SQLite's `percentile_cont`, a window-function query and Python; all three took 110-336 ms per bucket, so the choice of rule does not change the decision.
* Count columns use `COUNT(…)`, never `SUM(condition)`, so a bucket whose polls all failed stores 0 available and 0 valid samples rather than `NULL`. The first full-day run hit this.
* `quality_any` is the bitwise OR of the bucket's flags, written as one `MAX(flags & bit)` per defined `Quality` bit, because SQLite has no `bit_or` aggregate.
* ICMP rollups (200 series) are computed in Python from every individual reply, so the RTT median and p95 come from up to 75 values, never from per-poll averages.

### Retention

* **Native:** once a UTC day is entirely older than 30 days and its rollups are complete (ROLL-T04), one transaction drops its three tables and deletes their catalog rows. No `DELETE` or `VACUUM` runs.
* **Rollups:** drop a month's tables once the month's last bucket is older than 400 days. Up to about 430 days are kept, which satisfies "at least day 400" (RET-002).
* The file stays at its high-water size, because freed pages are reused by the next day's tables. `VACUUM INTO` remains available for an operator who wants the space back.

### SQLite features relied on

* `STRICT` tables (3.37) and `WITHOUT ROWID`.
* Window functions and the percentile extension are not required (see the amendment below).

JSON functions are not used and play no part in the decision. The bundled 3.53.1 has every feature listed.

### ClickHouse

ClickHouse is not needed at MVP scale. SQLite alone meets every pass criterion: the 400-day graph runs with more than 20× margin, ingest with more than 400×, and the disk projection fills a third of the volume. It becomes worth revisiting only past the criteria under "Rollback and revisit criteria".

## Alternatives

* **A (rowid plus a secondary key index).** Its index needs the same one-page-per-series write per commit as B once each series fills an index page. Every point of a series graph is a separate table-page read, so cold graphs cost one random read per sample. A also needs `DELETE` and `VACUUM` for retention. Rejected.
* **B (one clustered table per type).** Queries scale with points returned, but retention must `DELETE` 5.18 million rows a day spread over 3,200 key ranges. Ingest also pays one leaf-page write per series per commit. Rejected.
* **C without staging.** Each commit dirties one leaf page per series present, about 3,400 pages (13 MiB) of WAL per minute of data. At 10-second flushes plain C sustained only 10,000-12,000 rows/s, with commit p95 of 127-168 ms, and wrote 2.5 GiB per hour of data. Kept as the fallback if staging proves troublesome.
* **C′ (one file per day).** Retention would be an `unlink`, but the bundled SQLite is compiled with `SQLITE_MAX_ATTACHED=10`, and `setlimit` cannot raise it. A 30-day graph would need three or four attach/query/detach rounds, merged in Python, which loses SQL-side aggregation across rounds. Not needed, because dropping a day table is already cheap.
* **A UUID unique index for idempotency.** Rejected; see Idempotency.
* **An upserted current-state projection.** Rejected; see Queries.
* **A larger page size or rounding native rates.** Measured; see Risks.

## Rationale

The workload's defining property is that 3,200 series each gain one row a minute. A series-clustered B-tree is ideal for reading but receives each minute's rows at 3,200 different leaf pages, so every commit rewrites thousands of pages to the WAL and then to the database. The staging table turns that into right-edge appends for the 10-second commits. It pays the one-page-per-series cost once per 15-minute move, with 15 rows per series instead of one. This kept the clustered read path while cutting commit latency about 5× and write volume 4-6.6× (6.6× in the two-hour sweep; about 4× over full days).

## Benefits

* Graph queries read only the day or month tables they need, and cost scales with points returned.
* Retention is a metadata operation, not a rewrite.
* Writes are a few milliseconds per flush, leaving the writer idle more than 99.8% of the time at 2× the MVP workload.
* Deduplication is free.

## Risks

* **Write amplification.** Over each full day the writer process wrote 15.6-15.8 GB to storage for 0.77 GB of stored data: WAL frames, checkpoint copies, and the tail page of every series rewritten at each move. That averages 0.18 MB/s, or about 5.7 TB a year, which is well inside enterprise SSD or virtual-disk endurance but worth checking on consumer flash. Plain C's sweep rate extrapolates to about 64 GB a day. Moving every 30 minutes, or a larger `wal_autocheckpoint`, lowers this further, at the cost of a larger staging table to scan.
* **Checkpoint starvation.** Under continuous reads, only the explicit `TRUNCATE` checkpoint after each move keeps the WAL bounded. The adapter should report WAL size in health and alert above about 256 MB.
* **The mover is new adapter logic.** It must move rows and advance the boundary in one transaction; a unit test pins the invariant. If it stops, staging grows by about 36 MiB an hour and queries still return correct results, only slower, so the adapter must report mover lag in health.
* **The schema has about 130 tables at steady state** (31 days × 3 types, 15 months × 2 types, staging and fixed tables). Every new connection parses the whole schema: opening a connection and running its first query measured 2.7 ms at 133 tables, so readers should keep connections open.
* **Integer codes.** Code assignments must never be renumbered, the same discipline as `Quality` bits.
* **HC counters at or above 2^63** need the signed mapping described above.
* **Rounding native rates** would save about 10 bytes per row (7%). It changes stored precision, which is a RATE decision for the logic owner; the default is not to round.
* **Host differences.** The benchmark host's virtual disk is fast (fsync p50 1.5 ms). On slower storage, `synchronous=FULL` costs more per commit, but six commits a minute leave ample margin.

## Security impact

None beyond the existing rules. Telemetry rows hold no credentials or community strings (10.2). Table names in dynamic SQL come only from fixed prefixes and dates through the catalog, never from requests. The telemetry file keeps the same ownership and permissions as the metadata database.

## Performance impact

Measured on 4 vCPU, 9.7 GiB RAM and an ext4 virtual disk, with the full 2× MVP workload. The full context is in the [report](../performance/adr021-compact-sqlite-benchmark.md).

| Measure | Measured | Criterion |
|---|---|---|
| Ingest, 10 s flushes, `synchronous=FULL`, 16 full days | 26,514-37,698 rows/s per day; commit p50 5-6 ms, p95 17-49 ms; move p50 0.7-0.9 s every 15 min | at least 10× of 60 rows/s: **442×** on the slowest day |
| Ingest with two readers running | 31,200 rows/s; WAL at most 14 MB with the post-move `TRUNCATE` | |
| Native size | 147.8 B/row (iface 154.5, ICMP 111.9, SNMP 77.5), including keys and free space; 766 MB/day | |
| Rollup size, rates rounded | 66.9 B/row interface, 56.2 B/row ICMP; 21.6 MB/day | |
| Disk per node, 32 days native + 431 days rollups | **33.9 GB** (110 B/row over 307 M rows) | fits 100 GB; 27.3 estimated 22-42 GB |
| 400-day interface graph | 36 ms warm p50 (45 p95); 84 ms cold (89 p95) | **under 2 s** (MVP-ACC-05) |
| One interface, 1 h / 24 h raw | 0.4 / 3.1 ms warm; 10 / 15 ms cold | page under 1 s p95 (12.9) |
| One interface, 7 d / 30 d downsampled (16 days loaded) | 10.9 / 19.3 ms warm p50; 30 d projects to 60 ms warm p95, about 180 ms cold | page under 1 s p95 |
| Top utilization (DASH-003) / top errors (DASH-004), 15 min | 44 / 45 ms warm p50; p95 cold 361 / 378 ms | page under 1 s p95 |
| Current state: one switch / all 3,200 series | 0.9 / 24 ms warm; 9 / 32 ms cold | |
| Rollup: one 15-min bucket for all series / one whole day | 110 ms / 10.4 s (`percentile_cont`) | every 15 min |
| Retention of one day | `DROP` 0.99 s, 0.77 MB written | B: `DELETE` + vacuum 21 s, 2.7 GB; A: 29 s, 4.3 GB |
| Replay of committed batches | 0 rows inserted, 130,000-260,000 rows/s | STORE-009 |
| Readers during full-speed ingest | 24 h raw p95 3.9 ms; top utilization p95 81 ms | |

The alternatives measured on the same host:

| Layout | Result |
|---|---|
| A | Cold 24 h graph 111-117 ms, and a cold 30-day graph projects to about 3.2 s. Retention 29 s and 4.3 GB written. Writes 35-43 GB a day. |
| B | Same graph latencies as C+H. Retention 21 s and 2.7 GB written. Writes 66-72 GB a day. |
| Plain C | Ingest 10,000-12,000 rows/s at 10 s flushes, commit p95 127-168 ms. |

## Compatibility and migration impact

This is the first telemetry schema, so nothing needs migrating. The schema version goes in the telemetry file's `user_version`. Later changes follow 5.6. Because each day and month is its own table, a column change can apply to new partitions only, with queries selecting `NULL` for the column from older ones until those age out; no 40 GB rewrite is needed. Lite uses the same adapters and layout, with 24-hour retention.

## Test changes

* The benchmark harness lives in `tests/performance/sqlite_compact/`. Its unit tests (`test_sqlc_*.py`) cover generator determinism, missing-data shape, partition routing, the staging invariant, idempotent replay for every layout, agreement of the three p95 methods (ROLL-T01), DASH-003/DASH-004 ranking semantics, current-state equivalence and retention.
* The telemetry adapter's contract tests (19.3) should reuse these cases against the real adapter.

## Documentation changes

This ADR and the benchmark report in `docs/performance/`. Blueprint 27.3's estimate should be replaced by the measured figure (owner action; this change does not edit the blueprint).

## Rollback and revisit criteria

Revisit when any of these holds:

* A node exceeds 3× this workload (about 10,000 series).
* p95 graph latency approaches 500 ms in production telemetry.
* Mover lag or WAL growth appears in health data.
* Disk projections pass 50% of the provisioned volume.
* The interpreter's SQLite build drops `ENABLE_PERCENTILE` or raises `MAX_ATTACHED` (which would reopen C′).

Rolling back to plain C means disabling the staging path: the day tables, keys and queries are unchanged.

## Amendment: the rollup p95 rule (2026-10-20, at merge)

The benchmark computed p95 with linear interpolation (`percentile_cont`), but Appendix E.17 #13 already fixes p95 as nearest-rank: `vakr.logic.stats.p95`, which top-utilization rankings (DASH-003) use, and which rollups (RET-004) must share. This amendment records the contradiction and resolves it in favour of E.17:

* Nearest-rank always returns a real sample, so a rollup's p95 equals the p95 a ranking computes from the same native rows, in every language.
* SQLite's `percentile_disc` is not nearest-rank: it disagreed with `vakr.logic.stats.nearest_rank` on 360 of 399 random sample sets of 1-399 values.
* The adapter therefore fetches each bucket's available rates in key order and computes p95 in Python. The benchmark's Python method took 130 ms for one bucket of all 3,200 series, against 110 ms for `percentile_cont`, every 15 minutes.
* The SQL aggregates (counts, max, average, the `quality_any` OR) stay in SQL.

The benchmark harness keeps its type-7 comparison as evidence; ROLL-T01 for the adapter tests nearest-rank.
