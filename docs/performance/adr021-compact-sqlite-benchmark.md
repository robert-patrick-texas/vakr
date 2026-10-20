# ADR-021 benchmark: SQLite telemetry for the Compact profile

This report records the benchmark behind [ADR-021](../adr/ADR-021-compact-sqlite-telemetry.md). It measures four SQLite layouts for Compact-profile telemetry at twice the MVP workload: ingest, size, query latency, rollups, retention, and reader/writer concurrency. It follows blueprint 23.3: every number below comes with the context needed to reproduce it.

## Verdict

**Adopt layout C+H.** It has one `WITHOUT ROWID` table set per UTC day, clustered on `(series, collector, ts)`, monthly rollup tables, and a small time-ordered staging table drained every 15 minutes. It uses 10-second flushes with `synchronous=FULL`, the natural key with `INSERT OR IGNORE` for idempotency, `percentile_cont` for p95, retention by `DROP TABLE`, and a `TRUNCATE` checkpoint after each move. SQLite meets every MVP criterion with a wide margin, so ClickHouse is not needed at MVP scale.

| Criterion | Requirement | Measured (2× MVP workload) | Result |
|---|---|---|---|
| MVP-ACC-05, 400-day graph | Renders in under 2 s | Query 36 ms warm p50 (45 ms p95), 84 ms cold p50 (89 ms p95), over 400 days of rollups for all 3,200 series | Pass, more than 20× margin |
| Blueprint 12.9, page queries | Under 1 s p95 | Every page query at 16 days: at most 60 ms warm p95 and 378 ms cold p95. The 30-day graph projects to 60 ms warm p95 and about 180 ms cold | Pass |
| Ingest headroom | At least 10× of 60 rows/s | 26,514 rows/s on the slowest of 16 days (442×); 31,200 rows/s with two readers running | Pass |
| Disk | Fits a 100 GB volume | 33.9 GB per node for 30 days native plus 400 days of rollups, at the worst point before a drop | Pass |
| Blueprint 27.3 estimate | 22-42 GB, 80-150 B/row | 33.9 GB, 110 B/row averaged over 307 M rows | Inside the estimate |

## Context (blueprint 23.3)

| Item | Value |
|---|---|
| Source revision | `3764e23` plus the uncommitted benchmark files of this change (tracked files clean) |
| Schema version | First telemetry schema; the DDL is in ADR-021 |
| Host | KVM guest, Intel Xeon (Cascade Lake), 4 vCPU, 9.7 GiB RAM, no swap |
| Storage | `/dev/sda1` ext4 (`rw,relatime,discard,commit=30`) on an 80 GB QEMU virtual disk. Measured from the guest: 4 KiB append+`fsync` p50 1.5 ms, p95 2.7 ms, p99 4.6 ms; sequential write 657 MiB/s; cold sequential read 1.6 GB/s; cold random 4 KiB read p50 46 µs, which suggests the hypervisor caches the image |
| Kernel | Linux 7.0.0-38-generic (Ubuntu) |
| Python | CPython 3.15.0, python-build-standalone via uv 0.13.3, the interpreter family the `.deb` bundles |
| SQLite | 3.53.1 (the `sqlite3` module's library). Relevant options: `ENABLE_PERCENTILE`, `ENABLE_DBSTAT_VTAB`, `ENABLE_MATH_FUNCTIONS`, `MAX_ATTACHED=10`, `MAX_COMPOUND_SELECT=500`, `DEFAULT_PAGE_SIZE=4096`, `DEFAULT_WAL_SYNCHRONOUS=2`, `THREADSAFE=1` |
| Driver | Standard-library `sqlite3` only |
| Workload | 100 devices (80 routers × 10, 20 switches × 40 interfaces), 2 collectors: 3,200 interface, 200 ICMP and 200 SNMP device series at 60 s, which is 3,600 rows a minute and 5,184,000 a day |
| Generator | `sqlc_workload`, seed 21, start 2026-09-01T00:00Z. Diurnal (UTC−6) and weekly traffic with noise and 1% bursts; 2% of devices a day have an outage, 3% reload, 3% lose one position's path; failed polls store `NULL` counters and rates with `NOT_ATTEMPTED`; long gaps and reloads invalidate the next baseline; 0.4% of probes store `-1`; observation IDs are 16-byte UUIDv7 |
| Process model | One writer process, one connection, one transaction per flush; readers are separate processes with read-only connections (concurrency test: 2 readers) |
| Data location | `.claude/bench-data/` in the agent worktree on the disk above (see "Deviations"), capped at 14 GiB; deleted afterwards |
| Concurrent load | The host was shared with other development agents (load average about 2 on 4 vCPUs), so short runs show some noise. The one clear outlier is noted in the appendix |

### Deviations from the task setup

* The task named the session scratchpad for data. On this host that path is a 4.9 GB RAM-backed `tmpfs` with 3.8 GB free, shared with other agents' test temp files. On `tmpfs`, `fsync` does nothing and nothing can be cold, so `synchronous` and cold-cache results would be meaningless, and filling it would take memory from the benchmark and other agents. Data therefore went to a gitignored directory on the real disk, under the same 15 GB budget (peak 13.8 GB: the 12.6 GB 16-day database plus a 1.26 GB WAL during the unmitigated concurrency test). Only small result files lived in the scratchpad.
* Cold-cache runs evict the database and WAL from the guest page cache with `posix_fadvise(POSIX_FADV_DONTNEED)`; `fincore` confirmed eviction. No sudo was used. The hypervisor may still cache the disk image, so cold numbers are a lower bound for slower physical storage.
* The full 30 days would need about 25 GB of native data and did not fit the budget. The recommended layout was loaded with 16 full days at full series count (12.5 GiB), B with 8 and A with 4. Sizes per day are constant under layout C, and query latency is shown against data volume below, so 30-day figures are extrapolated from those measurements.

## Method

* **Ingest sweeps** (`sweep-*`) load two hours (432,000 rows) into a fresh database for each layout × flush interval (60 s or 10 s) × `synchronous` (`NORMAL` or `FULL`), plus single-option variants (UUID index, rounded rates, projection, 8 KiB pages, 10,000-page checkpoint). They record each commit's latency, the bytes the process wrote (`/proc/self/io` `wchar`), and the cost of replaying the last 10 minutes of byte-identical batches.
* **Scale runs** (`scale-*`) load full UTC days and run the query suite at 1, 2, 4, 8 (and 16) days.
  * Warm: one untimed run, then 50 timed runs of the same query and series.
  * Cold: 5 runs, each on a fresh connection after eviction and on a different series.
  * Queries:
    * one interface for 1 h and 24 h raw;
    * 7 d and 30 d downsampled to at most 1,200 points (min, max and average per bucket in SQL);
    * one device's raw ICMP for 24 h;
    * DASH-003 top utilization over 15 minutes (p95 per series, three ways);
    * DASH-004 top errors;
    * current state for one device (80 series) and for all 3,200 series.
* **Rollups:** one 15-minute bucket for all series (10 runs) and one whole day, three p95 methods, cross-checked for identical output.
* **Retention:** expire the oldest day; C drops tables, A and B delete per series and then `incremental_vacuum`.
* **Concurrency:** 60 s each of idle readers, readers with a writer at full speed, and readers with a writer paced at ten times real time.
* **Rollup store:** 400 days of 15-minute rollups at full series count in monthly tables, then 30/90/180/400-day graphs, warm and cold.

All percentiles use linear interpolation (type 7). Latency is wall-clock time in the calling Python process and includes building result rows.

## 1. Ingest: layouts, flush interval, and `synchronous`

These are two-hour sweeps of 432,000 rows each. The required rate is 3,600 rows a minute, which is 60 rows/s. "Written" is the bytes the writer process passed to `write()` per hour of data, covering WAL frames and checkpoint copies.

| Layout, 10 s flush, `FULL` | rows/s | commit p50 / p95 / max (ms) | 15-min move p50 / max (ms) | written MiB per hour of data |
|---|---|---|---|---|
| A: rowid + key index | 21,571 | 18.6 / 110.4 / 209 | - | 1,075 |
| B: one clustered table | 9,054 | 47.1 / 187.2 / 374 | - | 2,537 |
| C: clustered, per day | 11,964 | 40.8 / 126.6 / 187 | - | 2,537 |
| C+H, move every 5 min | 27,933 | 7.8 / 21.4 / 161 | 309 / 741 | 781 |
| **C+H, move every 15 min** | **35,854** | **7.4 / 28.5 / 136** | **531 / 634** | **386** |

* **Why plain C and B are slow.** Each commit lands one row in each series present, and in a series-clustered B-tree that is a different leaf page per series. A minute of data therefore dirties about 3,400 pages (13 MiB), and every commit pays for them in WAL frames and checkpoints, whatever the flush interval.
* **Why A looks good here, briefly.** After two hours, A's 15-byte index entries still pack several series per index page. Once each series has index pages of its own, every commit dirties one index page per series. The scale-A run wrote 35-43 GB a day, against the 15 GB a day this sweep extrapolates to.
* **What staging buys.** C+H appends 10-second flushes at the right edge of a time-ordered table and pays the per-series page write once per move, carrying 15 rows per series.
* **`synchronous=FULL` vs `NORMAL`.** `FULL` costs about 5 ms per commit with staging: C+H15 at 10 s ran at 54,051 rows/s, 2.8 / 6.7 / 213 ms, under `NORMAL`. The full table is in the appendix.
* **Variants** (C+H15, 10 s, `NORMAL` unless noted; bytes/row includes the small staging table at this run length):

| Variant | rows/s | commit p95 (ms) | written MiB/h | bytes/row | Effect |
|---|---|---|---|---|---|
| Baseline | 54,051 | 6.7 | 386 | 165.2 | |
| Unique index on `observation_uuid` | 52,653 | 9.3 | 402 | 204.5 | +39.3 B/row (plain C: +39.2, and 21% slower ingest) |
| Upserted `cur_iface` projection | 28,383 | 82.7 | 487 | 166.3 | Halves throughput |
| Bit rates rounded to whole bit/s | 55,384 | 7.8 | 362 | 158.0 | −7 B/row (plain C: −10.0) |
| `page_size = 8192` | 47,129 | 5.2 | 401 | 157.6 | −8 B/row, 13% slower |
| `wal_autocheckpoint = 10000` | 65,493 | 5.1 | 354 | 165.2 | 21% faster, 8% fewer bytes |

* **One-minute flushes** behave as expected: C+H15 at 60 s under `FULL` ran 30,916 rows/s with 54.6 / 213.2 / 278 ms. Ten-second flushes are recommended for TEST-005 freshness, and they are also cheaper per commit with staging.

## 2. Full-day ingest of the recommended layout

This is 16 consecutive UTC days, 82,944,000 rows, under the recommended settings: C+H, 10 s flush, `synchronous=FULL`, a move every 15 minutes, and default checkpointing.

| Measure | Day 0 | Days 0-15 range |
|---|---|---|
| rows/s (insert time only) | 33,600 | 26,514-37,698 (days 13-14 slowest, once the file outgrew RAM) |
| Commit p50 / p95 / p99 (ms) | 6.2 / 25.6 / 100.4 | 5.1-6.2 / 17.3-48.5 / 76.4-124.7 |
| Commit max (ms) | 166 | 596 |
| Move every 15 min, p50 / max (ms) | 697 / 970 | 683-936 / 3,408 |
| Bytes written by the process per day | 15.6 GB | 15.6-15.8 GB |
| File growth per day | 716 MiB | 731 MiB average, constant |

* **Headroom.** The slowest day sustained 26,514 rows/s, which is **442× the required 60 rows/s**, against the 10× criterion.
* **Writer busy time.** Per minute of data the writer spends about 55 ms on commits (6 × 9 ms mean) and 47 ms on its share of a move, about 0.2% of the minute.
* **Catch-up.** After a partition heals, a day of peer backlog (2.6 million rows) drains in about 100 s at these rates, without starving local ingest (MPOS-005).
* **A and B over full days.** Their scale runs used settings that favour them: 60 s flushes, `NORMAL`, a 10,000-page checkpoint. Even so:
  * A sustained 22,427-27,054 rows/s (commit p50 88-124 ms) and wrote 35-43 GB a day.
  * B sustained 14,780-15,793 rows/s (commit p50 218-266 ms) and wrote 66-72 GB a day.
  * C+H wrote 15.7 GB a day under the stricter recommended settings.

## 3. Size

**Native**, measured at 16 days with `dbstat`; bytes per row includes keys, page headers and free space:

| Table family | Rows per day | MiB per day | Bytes/row | Page fill |
|---|---|---|---|---|
| `obs_iface_d*` | 4,608,000 | 678.8 | 154.5 | 0.90 |
| `obs_icmp_d*` | 288,000 | 30.7 | 111.9 | 0.90 |
| `obs_snmp_d*` | 288,000 | 21.3 | 77.5 | 0.89 |
| All native | 5,184,000 | 730.9 | 147.8 | |

The staging table held 248 pages (1 MiB) at the end of the run; it never holds more than 17 minutes of rows.

**Rollups**, measured on the full 400-day store: 130,498,660 rows in monthly tables, with rates rounded to whole bit/s.

| Table family | Rows | GB | Bytes/row | Page fill |
|---|---|---|---|---|
| `roll_iface_15m_m*` | 122,818,660 | 8.22 | 66.9 | 0.90 |
| `roll_icmp_15m_m*` | 7,680,000 | 0.43 | 56.2 | 0.90 |

A 3-day probe of the same generator measured 102.2 B/row for interface rollups with full-precision rates and 73.4 B/row rounded, a 28% saving. At 15-minute resolution, fractions of a bit per second carry no information.

**Other storage options**, from the sweeps:

| Option | Native bytes/row |
|---|---|
| Unique `observation_uuid` index | +39 (+26%) |
| Rounding native bit rates | −10 (−7%) |
| 8 KiB pages | −8.6 (−6%), with slower ingest |

### Projected disk use per node

| Component | Rows | Size |
|---|---|---|
| Native: 32 day tables (30 retained, today, and the day awaiting its drop) | 165.9 M | 32 × 766 MB = 24.5 GB |
| Interface rollups: up to 431 days, because a month table is dropped only once all of it is past day 400 | 132.4 M | 431 × 20.5 MB = 8.86 GB |
| ICMP rollups: 431 days | 8.3 M | 431 × 1.08 MB = 0.47 GB |
| Staging, WAL (at most 64 MiB after each truncating checkpoint), catalog, dictionaries | | under 0.1 GB |
| **Total** | **306.6 M** | **33.9 GB (31.6 GiB)** |

Comparison with the blueprint 27.3 estimate:

* With exactly 30 days of native data and 400 days of rollups, the figure is 31.6 GB.
* Full-precision rollup rates would add about 3.6 GB, giving 37.5 GB; a UUID index would add 6.5 GB.
* The estimate was 276 M rows and 22-42 GB per node. The measured figure is inside it, at 110 B/row.
* The row count is higher than the estimate because rollups cover days 1-400, not 31-400. Rollups are computed while native rows still exist (E.10), and whole-month drops keep up to 31 extra days.

On a 100 GB volume this leaves about 66 GB for the metadata database, the spool, logs, and a `VACUUM INTO` or backup copy of the telemetry file (about 34 GB).

## 4. Query latency

**Recommended layout (C+H), 1 day vs 16 days of data.** Warm figures are p50 / p95 over 50 runs; cold figures are p50 / p95 over 5 runs on a fresh connection with an evicted page cache, each on a different series. "Samples" is the native rows the query read at 16 days.

| Query | Samples | 1 d warm | 16 d warm | 16 d cold | 30 d projected (warm p95 / cold p50) |
|---|---|---|---|---|---|
| One interface, 1 h raw | 60 | 0.39 / 0.48 | 0.40 / 0.59 | 10.2 / 11.1 | same (window does not grow) |
| One interface, 24 h raw | 1,440 | 2.23 / 2.94 | 3.14 / 5.18 | 14.6 / 15.2 | same |
| One interface, 7 d downsampled (1,008 points) | 10,080 | 1.89 / 2.21 | 10.9 / 17.7 | 54.6 / 88.8 | same (7 d of data present from day 7) |
| One interface, 30 d downsampled (720 points) | 23,040 | 1.44 / 2.12 | 19.3 / 32.1 | 94.1 / 101.8 | 60 / 176 (× 30/16) |
| One device ICMP, 24 h raw | 1,440 | 2.28 / 3.01 | 2.70 / 4.26 | 6.3 / 7.2 | same |
| Top utilization, 15 min, `percentile_cont` (DASH-003) | 48,000 | 42.6 / 47.2 | 44.0 / 59.7 | 294 / 361 | same (window does not grow) |
| Top utilization, window functions | 48,000 | 198 / 218 | 196 / 215 | 454 / 469 | same |
| Top utilization, p95 in Python | 48,000 | 75.5 / 97.1 | 75.4 / 91.2 | 355 / 364 | same |
| Top errors, 15 min (DASH-004) | 48,000 | 49.1 / 77.7 | 44.5 / 58.8 | 300 / 378 | same |
| Current state, one switch (80 series) | 80 | 1.68 / 2.21 | 0.90 / 1.16 | 9.1 / 23.7 | same |
| Current state, all 3,200 series | 3,200 | 24.0 / 26.5 | 23.8 / 26.5 | 31.5 / 43.5 | same |

The full table at 1, 2, 4, 8 and 16 days is in the appendix.

### Does series latency scale with points returned or with table size?

The question: on a clustered key, does a per-interface query's cost follow the points it returns, or the size of the table? The table shows p50 in ms, warm / cold. "ds 30 d" covers all the data present, so its sample count grows with the data.

| Data | B file | B 24 h raw (1,440 pts) | B ds 30 d | C+H file | C+H 24 h raw | C+H ds 30 d | A file | A 24 h raw | A ds 30 d |
|---|---|---|---|---|---|---|---|---|---|
| 1 d (1,440 samples) | 0.75 GB | 1.65 / 5.1 | 0.85 / 5.6 | 0.75 GB | 2.23 / 12.0 | 1.44 / 10.6 | 0.83 GB | 3.57 / 110.9 | 2.53 / 124.3 |
| 2 d (2,880) | 1.50 GB | 1.56 / 6.0 | 1.73 / 10.1 | 1.50 GB | 2.30 / 13.1 | 2.72 / 16.3 | 1.66 GB | 3.62 / 113.4 | 5.77 / 208.2 |
| 4 d (5,760) | 3.00 GB | 1.43 / 5.6 | 3.60 / 17.0 | 3.02 GB | 2.30 / 14.1 | 5.39 / 34.9 | 3.36 GB | 3.88 / 116.9 | 12.85 / 429.0 |
| 8 d (11,520) | 6.03 GB | 1.48 / 5.7 | 7.69 / 33.6 | 6.08 GB | 2.48 / 13.4 | 9.91 / 50.1 | | | |
| 16 d (23,040) | | | | 12.27 GB | 3.14 / 14.6 | 19.26 / 94.1 | | | |

**Proved for the clustered layouts.**

* A fixed 1,440-point query costs the same at 0.75 GB as at 6 GB (B) and 12.3 GB (C+H), warm and cold. That holds after the file outgrew RAM at about 14 days.
* Cost grows with samples read: about 0.67 µs per sample warm and 2.9 µs cold for B, and about 0.84 µs warm and 4 µs cold for C+H. C+H's figure includes one extra table arm per day.
* The only size-dependent term is B-tree depth, which is logarithmic and invisible at this scale.
* Staging adds a fixed cost to every series query, because the staging arm is a short time-range scan: about 0.3 ms warm and 7 ms cold (B's 1 h raw is 0.06 / 0.7 ms; C+H's is 0.4 / 8-10 ms).

**Disproved for A.**

* A's index finds the rows, but they sit on time-ordered table pages, so every sample is a separate page read.
* Warm, that costs 3.6-3.9 ms for 24 h, which is fine. Cold, it costs 111-117 ms for 24 h, and the downsampled cost grows by about 75 µs per sample (429 ms at 5,760 samples).
* A cold 30-day graph (43,200 samples) would take about 3.2 s, past the 1 s page budget.

## 5. Rollup computation (RET-004, RET-009)

These run on day 15 of the recommended layout. The bucket covers 12:00-12:15, all 3,200 series and 48,000 native rows. The three methods produced identical rows, compared with 1e-9 relative tolerance.

| Method | One bucket, compute + store (p50 / p95, 10 runs) | Whole day, 307,200 rollup rows from 4.6 M native |
|---|---|---|
| `percentile_cont` (SQLite percentile extension) | 110 / 120 ms | 10.4 s |
| Window functions (`ROW_NUMBER … NULLS LAST`), portable | 336 / 345 ms | 31.5 s |
| Python (fetch, sort, interpolate, insert) | 130 / 140 ms | 15.3 s |
| ICMP rollup, 200 series, Python | 16 / 53 ms | 1.8 s |

* The day's 307,200 interface buckets averaged 14.98 valid samples of 15 expected.
* 890 buckets were incomplete and 180 had no valid sample (outages). These are stored with their counts, not dropped or averaged away (ROLL-T05). 5,536 buckets carry a quality flag in `quality_any`.
* The first full-day run found a real bug: in an all-outage bucket `SUM(oper_status = 1)` is `NULL`, which violated `NOT NULL`. The rollup now uses `COUNT(CASE …)`, and a regression test covers it.

## 6. Retention

Each layout expires its oldest day.

| Layout | Operation | Time | Bytes written | WAL after | Space |
|---|---|---|---|---|---|
| C+H (16 days, 12.6 GB) | `DROP TABLE` × 3 + catalog rows, one transaction | 0.99 s, then a 17 ms checkpoint | 0.77 MB | 0.77 MB | 181,548 pages (709 MiB) to the freelist; the next day's tables reuse them |
| B (8 days, 6.0 GB, `auto_vacuum=INCREMENTAL`) | `DELETE` of 5,184,000 rows, one key range per series | 7.2 s | 1.15 GB | 572 MB | 179,244 pages to the freelist |
| | `PRAGMA incremental_vacuum` | 13.9 s | 1.56 GB | 792 MB | file −735 MB |
| A (4 days, 3.4 GB, `auto_vacuum=INCREMENTAL`) | `DELETE`, one index range per series | 20.8 s | 2.61 GB | 746 MB | 200,236 pages to the freelist |
| | `PRAGMA incremental_vacuum` | 8.7 s | 1.67 GB | 832 MB | file −821 MB |

* Dropping a day is 7-30× faster than deleting it.
* It writes 3,500-5,500× fewer bytes: 0.77 MB against 2.7-4.3 GB.
* It never swells the WAL. A delete pushes the WAL to 0.6-0.8 GB, which a busy reader would then keep from resetting (section 7).
* A naive `DELETE … WHERE ts_us < ?` on A or B has no usable index and would scan the whole table, so it was not attempted. The per-series range form above is the best case for A and B.
* **C′ (one file per day).** It would turn retention into an `unlink`, but retention is already a one-second operation. The bundled SQLite allows 10 attached databases (`MAX_ATTACHED=10`; `setlimit` cannot raise it, and an eleventh `ATTACH` fails with "too many attached databases - max 10"). A 30-day graph would need three or four attach/query/detach rounds, merged in Python. C′ was therefore not built.

## 7. Readers while the writer runs (WAL)

Two reader processes ran raw 24 h, 7 d downsampled, top utilization and one device's current state in a loop for 60 s per phase. The data was the 16-day database.

| Phase | Writer | raw 24 h p50 / p95 / p99 | 7 d ds p50 / p95 | top util p50 / p95 | device current p50 / p95 | WAL max |
|---|---|---|---|---|---|---|
| Readers only | - | 2.8 / 4.4 / 6.2 | 16.5 / 22.4 | 48.2 / 66.1 | 1.5 / 2.2 | - |
| Writer at full speed | 34,800 rows/s, commit p95 35 ms | 2.8 / 4.8 / 9.2 | 15.9 / 24.2 | 57.7 / 87.7 | 9.2 / 14.9 | **1.19 GB** |
| Writer at 10× real time | 600 rows/s, commit p95 88 ms | 2.6 / 3.9 / 5.0 | 16.9 / 22.8 | 49.5 / 70.9 | 4.1 / 10.6 | 1.26 GB |
| Full speed + `wal_checkpoint(TRUNCATE)` after each move | 31,200 rows/s, commit p95 18 ms | 2.1 / 3.9 / 6.6 | 11.8 / 18.6 | 58.8 / 80.6 | 9.1 / 14.5 | **14 MB** |
| 10× real time + `TRUNCATE` | 600 rows/s, commit p95 20 ms | 2.0 / 3.6 / 4.5 | 12.0 / 18.4 | 49.2 / 67.2 | 4.5 / 7.3 | 9 MB |

* Reader latency barely moves under WAL. The one-device current-state query rises because the staging table it scans grows while the writer appends minutes back to back.
* With continuously overlapping readers, passive autocheckpoints can never reset the WAL. It grew to 1.2 GB, and `journal_size_limit` cannot shrink a WAL that never resets.
* A `TRUNCATE` checkpoint after each move fixes this: 309 ms p50, 402 ms max, never busy. ADR-021 adopts it.

## 8. Idempotency (STORE-009)

* Every sweep replayed its last 10 minutes of committed batches byte for byte; the 16-day run replayed its last 30 minutes (108,000 rows: 28 minutes already moved into the day table, 2 still staged). **Every replay inserted 0 rows.**
* Replay speed was 130,000-260,000 rows/s, because replays are primary-key probes without writes.
* The natural key therefore deduplicates for free.
* A unique `observation_uuid` index costs 39 bytes per row (about 6 GB per node over 30 days) and 3-21% of ingest throughput, and adds no protection the key lacks.

## 9. The 400-day rollup store and long-range graphs (MVP-ACC-05)

The store holds 400 days for all 3,200 interface and 200 ICMP series in 14 monthly tables: 8.65 GB, loaded one UTC day per transaction at 288,188 rows/s. Latency is in ms, over 50 warm runs and 5 cold runs on different series.

| Graph | Points | Native samples represented | Warm p50 / p95 / max | Cold p50 / p95 / max |
|---|---|---|---|---|
| Interface, 30 d | 720 | 43,170 | 3.1 / 3.3 / 3.7 | 7.3 / 9.5 / 9.6 |
| ICMP, 30 d | 720 | 43,200 | 2.6 / 3.2 / 4.6 | 5.8 / 7.0 / 7.1 |
| Interface, 90 d | 1,080 | 129,480 | 9.4 / 10.7 / 11.6 | 19.8 / 22.8 / 23.0 |
| ICMP, 90 d | 1,080 | 129,600 | 6.8 / 7.4 / 7.7 | 15.8 / 16.8 / 17.0 |
| Interface, 180 d | 1,080 | 258,990 | 16.7 / 22.6 / 25.3 | 38.6 / 40.6 / 41.1 |
| ICMP, 180 d | 1,080 | 259,200 | 12.9 / 14.5 / 15.5 | 30.8 / 31.6 / 31.8 |
| **Interface, 400 d** | **1,200** | **575,715** | **36.1 / 45.0 / 52.6** | **83.9 / 88.7 / 89.1** |
| ICMP, 400 d (latency, loss from counts, restarts) | 1,200 | 576,000 | 29.4 / 40.2 / 45.6 | 78.5 / 81.8 / 82.6 |

* The 400-day interface graph reads about 38,400 rollup rows from 14 tables and re-buckets them to 8-hour points in SQL, keeping max, highest p95 and the valid-weighted average.
* It uses under 1/20 of the 2 s budget even cold, leaving the rest to the API, serialization and the browser.
* Latency grows linearly with rollup rows read: about 0.9 µs per row warm and 2.2 µs cold. Monthly partitioning adds no visible per-table cost.

## Reproducing

```sh
uv sync
DATA=/var/tmp/vakr-bench          # a directory on the disk under test, 15 GB free; not tmpfs
uv run pytest tests/performance   # generator and helper unit tests, about 1 s
uv run python -m tests.performance.sqlite_compact.bench_plan --data-dir "$DATA" --results "$DATA/results"
uv run python -m tests.performance.sqlite_compact.bench_report --results "$DATA/results" \
    --digest docs/performance/adr021-digest.json
```

`bench_plan --list` prints the experiment names. `--only <prefix>` runs a subset, for example `--only sweep-CH15` or `--only scale-CH`, and finished experiments are skipped unless `--force` is given. Each experiment's database is deleted when it ends. The whole plan took about 3 h 15 min of wall time on the host above: sweeps 35 min, scale-CH 65 min, scale-B 55 min, scale-A 20 min, rollup store 13 min. Every step is a plain module call, such as `python -m tests.performance.sqlite_compact.bench_ingest --help`, so single measurements can be repeated by hand.

[`adr021-digest.json`](adr021-digest.json) holds every result this report cites, without the bulky host compile-option lists.

## Appendix: generated tables

These tables are printed by `bench_report` from the result files. "samples" is the count at the largest data volume, the cold column is p50, and bytes/row in the sweep table includes staging-table pages and freelist space, which matter at two hours but not over full days. Short sweeps run on a shared host are noisy: C-60-NORMAL came out slower than C-60-FULL, which a rerun would not be expected to repeat.

### All ingest sweeps (two hours each; name = layout-flush seconds-synchronous[-variant])

| Run | rows/s | commit p50 / p95 / max ms | move p50 / max ms | written MiB/h | bytes/row | replay inserted, rows/s |
|---|---|---|---|---|---|---|
| A-10-FULL | 21,571 | 18.6 / 110.4 / 209 | - | 1,075 | 156.9 | 0, 181,869 |
| A-10-NORMAL | 17,143 | 11.6 / 150.1 / 284 | - | 1,075 | 156.9 | 0, 255,723 |
| A-60-FULL | 23,806 | 139.4 / 284.6 / 365 | - | 620 | 156.9 | 0, 238,796 |
| A-60-NORMAL | 35,696 | 76.7 / 246.4 / 471 | - | 620 | 156.9 | 0, 258,987 |
| B-10-FULL | 9,054 | 47.1 / 187.2 / 374 | - | 2,537 | 150.9 | 0, 229,390 |
| B-10-NORMAL | 9,305 | 42.8 / 191.6 / 377 | - | 2,537 | 150.9 | 0, 202,896 |
| B-60-FULL | 15,736 | 212.9 / 421.7 / 565 | - | 2,191 | 150.9 | 0, 221,135 |
| B-60-NORMAL | 17,555 | 195.4 / 353.3 / 476 | - | 2,191 | 150.9 | 0, 157,347 |
| C-10-FULL | 11,964 | 40.8 / 126.6 / 187 | - | 2,537 | 150.9 | 0, 143,672 |
| C-10-NORMAL-ckpt10k | 20,192 | 12.5 / 170.2 / 698 | - | 2,003 | 150.9 | 0, 140,679 |
| C-10-NORMAL-page8k | 12,112 | 21.7 / 124.7 / 229 | - | 3,572 | 142.3 | 0, 198,837 |
| C-10-NORMAL-proj | 7,339 | 59.0 / 256.0 / 430 | - | 2,688 | 152.0 | 0, 129,726 |
| C-10-NORMAL-round | 7,182 | 51.9 / 272.4 / 407 | - | 2,442 | 140.9 | 0, 155,318 |
| C-10-NORMAL-uuid | 7,990 | 51.8 / 247.2 / 398 | - | 2,580 | 190.1 | 0, 259,315 |
| C-10-NORMAL | 10,155 | 46.9 / 167.9 / 377 | - | 2,537 | 150.9 | 0, 201,576 |
| C-60-FULL | 16,534 | 237.4 / 348.1 / 403 | - | 2,191 | 150.9 | 0, 162,277 |
| C-60-NORMAL | 11,030 | 265.9 / 757.6 / 1049 | - | 2,191 | 150.9 | 0, 217,836 |
| CH15-10-FULL | 35,854 | 7.4 / 28.5 / 136 | 531 / 634 | 386 | 165.2 | 0, 194,352 |
| CH15-10-NORMAL-ckpt10k | 65,493 | 2.8 / 5.1 / 11 | 537 / 1093 | 354 | 165.2 | 0, 204,148 |
| CH15-10-NORMAL-page8k | 47,129 | 2.6 / 5.2 / 181 | 603 / 2008 | 401 | 157.6 | 0, 204,745 |
| CH15-10-NORMAL-proj | 28,383 | 6.0 / 82.7 / 217 | 560 / 894 | 487 | 166.3 | 0, 150,754 |
| CH15-10-NORMAL-round | 55,384 | 3.6 / 7.8 / 142 | 578 / 684 | 362 | 158.0 | 0, 170,902 |
| CH15-10-NORMAL-uuid | 52,653 | 3.1 / 9.3 / 218 | 565 / 669 | 402 | 204.5 | 0, 253,743 |
| CH15-10-NORMAL | 54,051 | 2.8 / 6.7 / 213 | 543 / 735 | 386 | 165.2 | 0, 149,430 |
| CH15-60-FULL | 30,916 | 54.6 / 213.2 / 278 | 571 / 1042 | 365 | 165.2 | 0, 251,951 |
| CH15-60-NORMAL | 65,103 | 13.1 / 69.1 / 100 | 505 / 649 | 365 | 165.2 | 0, 188,313 |
| CH5-10-FULL | 27,933 | 7.8 / 21.4 / 161 | 309 / 741 | 781 | 156.3 | 0, 147,493 |
| CH5-10-NORMAL | 32,560 | 2.9 / 9.1 / 178 | 343 / 1343 | 781 | 156.3 | 0, 202,056 |
| CH5-60-FULL | 45,365 | 21.9 / 43.0 / 60 | 267 / 417 | 743 | 156.3 | 0, 230,496 |
| CH5-60-NORMAL | 45,917 | 12.7 / 20.9 / 83 | 280 / 853 | 743 | 156.3 | 0, 237,983 |

### scale-CH (C+H, recommended settings)

 16 days, 82,944,000 rows, file 11.43 GiB, 147.9 bytes/row; elapsed 52 min

| Table family | pages | MiB | fill |
|---|---|---|---|
| obs_iface | 2,780,490 | 10,861 | 0.90 |
| obs_icmp | 125,907 | 492 | 0.90 |
| obs_snmp | 87,204 | 341 | 0.89 |


| Query | samples | 1 d warm p50/p95 | cold p50 | 2 d warm p50/p95 | cold p50 | 4 d warm p50/p95 | cold p50 | 8 d warm p50/p95 | cold p50 | 16 d warm p50/p95 | cold p50 |
|---|---|---|---|---|---|---|---|---|---|---|---|
| raw_1h | 60 | 0.4 / 0.5 | 7.3 | 0.4 / 0.6 | 8.6 | 0.8 / 1.5 | 8.5 | 0.5 / 0.7 | 8.6 | 0.4 / 0.6 | 10.2 |
| raw_24h | 1,440 | 2.2 / 2.9 | 12.0 | 2.3 / 3.2 | 13.1 | 2.3 / 2.9 | 14.1 | 2.5 / 2.9 | 13.4 | 3.1 / 5.2 | 14.6 |
| ds_7d | 10,080 | 1.9 / 2.2 | 11.9 | 3.5 / 4.0 | 18.4 | 7.1 / 8.6 | 28.0 | 11.4 / 17.4 | 40.5 | 10.9 / 17.6 | 54.5 |
| ds_30d | 23,040 | 1.4 / 2.1 | 10.6 | 2.7 / 3.5 | 16.3 | 5.4 / 6.8 | 34.9 | 9.9 / 15.7 | 50.0 | 19.3 / 32.1 | 94.1 |
| icmp_raw_24h | 1,440 | 2.3 / 3.0 | 5.8 | 2.4 / 3.3 | 6.5 | 3.2 / 4.6 | 7.2 | 2.6 / 3.4 | 6.5 | 2.7 / 4.3 | 6.3 |
| top_util_builtin | 25 | 42.6 / 47.2 | 306.7 | 69.7 / 73.0 | 309.2 | 44.1 / 51.3 | 333.9 | 42.6 / 47.4 | 323.9 | 44.0 / 59.7 | 294.0 |
| top_util_window | 25 | 197.9 / 217.5 | 502.5 | 206.3 / 265.9 | 535.2 | 199.8 / 220.3 | 532.0 | 200.2 / 245.0 | 454.4 | 196.0 / 215.2 | 454.4 |
| top_util_python | 25 | 75.5 / 97.1 | 343.6 | 80.4 / 98.3 | 395.2 | 76.5 / 97.3 | 374.5 | 78.1 / 102.0 | 383.0 | 75.3 / 91.2 | 355.0 |
| top_errors | 25 | 49.0 / 77.7 | 326.2 | 45.8 / 63.9 | 306.2 | 45.6 / 56.6 | 328.3 | 46.8 / 68.4 | 325.8 | 44.5 / 58.8 | 300.0 |
| current_device_seek | 80 | 1.7 / 2.2 | 10.6 | 1.0 / 1.8 | 8.3 | 2.2 / 2.5 | 11.2 | 0.9 / 1.1 | 10.5 | 0.9 / 1.2 | 9.1 |
| current_all_seek | 3,200 | 24.0 / 26.5 | 33.0 | 23.5 / 26.5 | 29.3 | 24.5 / 40.8 | 31.1 | 23.6 / 28.4 | 33.6 | 23.8 / 26.5 | 31.5 |

### scale-B (one clustered table)

 8 days, 41,472,000 rows, file 5.62 GiB, 145.4 bytes/row; elapsed 52 min

| Table family | pages | MiB | fill |
|---|---|---|---|
| obs_iface | 1,365,141 | 5,333 | 0.91 |
| obs_icmp | 62,176 | 243 | 0.91 |
| obs_snmp | 42,856 | 167 | 0.91 |


| Query | samples | 1 d warm p50/p95 | cold p50 | 2 d warm p50/p95 | cold p50 | 4 d warm p50/p95 | cold p50 | 8 d warm p50/p95 | cold p50 |
|---|---|---|---|---|---|---|---|---|---|
| raw_1h | 60 | 0.1 / 0.1 | 0.6 | 0.1 / 0.1 | 0.8 | 0.1 / 0.1 | 0.8 | 0.1 / 0.1 | 0.7 |
| raw_24h | 1,440 | 1.6 / 2.8 | 5.1 | 1.6 / 2.0 | 6.0 | 1.4 / 1.6 | 5.6 | 1.5 / 1.9 | 5.7 |
| ds_7d | 10,080 | 1.0 / 1.4 | 4.6 | 2.2 / 2.6 | 9.6 | 5.1 / 6.1 | 19.5 | 8.2 / 11.3 | 29.9 |
| ds_30d | 11,520 | 0.8 / 1.4 | 5.5 | 1.7 / 1.9 | 10.1 | 3.6 / 4.4 | 17.0 | 7.7 / 8.4 | 33.6 |
| icmp_raw_24h | 1,440 | 1.6 / 2.1 | 6.5 | 1.8 / 2.3 | 5.1 | 1.8 / 2.7 | 5.0 | 1.8 / 2.0 | 5.2 |
| top_util_builtin | 25 | 46.9 / 63.1 | 416.5 | 44.9 / 54.0 | 419.9 | 45.4 / 49.1 | 483.7 | 45.0 / 50.1 | 494.5 |
| top_util_window | 25 | 211.0 / 255.5 | 552.5 | 211.4 / 245.8 | 896.4 | 206.7 / 236.8 | 584.8 | 202.2 / 235.9 | 672.7 |
| top_util_python | 25 | 69.5 / 82.0 | 407.2 | 103.8 / 121.5 | 502.4 | 72.7 / 85.1 | 467.3 | 73.2 / 82.8 | 489.0 |
| top_errors | 25 | 46.0 / 51.4 | 363.8 | 47.5 / 58.4 | 473.3 | 47.0 / 67.4 | 419.8 | 46.4 / 49.7 | 461.0 |
| current_device_seek | 80 | 0.4 / 0.5 | 11.5 | 0.9 / 1.0 | 13.7 | 0.5 / 0.6 | 13.5 | 0.4 / 0.8 | 14.6 |
| current_all_seek | 3,200 | 25.1 / 26.5 | 288.9 | 28.2 / 38.0 | 356.0 | 26.4 / 30.9 | 311.5 | 27.6 / 30.6 | 383.6 |

### scale-A (rowid table plus key index)

 4 days, 20,736,000 rows, file 3.13 GiB, 161.9 bytes/row; elapsed 18 min

| Table family | pages | MiB | fill |
|---|---|---|---|
| obs_iface | 644,525 | 2,518 | 0.98 |
| obs_iface_key | 110,991 | 434 | 0.89 |
| obs_icmp | 29,504 | 115 | 0.98 |
| obs_snmp | 20,609 | 81 | 0.99 |
| obs_icmp_key | 6,466 | 25 | 0.89 |
| obs_snmp_key | 6,466 | 25 | 0.89 |


| Query | samples | 1 d warm p50/p95 | cold p50 | 2 d warm p50/p95 | cold p50 | 4 d warm p50/p95 | cold p50 |
|---|---|---|---|---|---|---|---|
| raw_1h | 60 | 0.1 / 0.1 | 5.4 | 0.1 / 0.1 | 5.5 | 0.2 / 0.2 | 5.8 |
| raw_24h | 1,440 | 3.6 / 4.9 | 110.9 | 3.6 / 4.3 | 113.4 | 3.9 / 4.6 | 116.9 |
| ds_7d | 5,760 | 3.3 / 4.8 | 114.5 | 6.5 / 9.1 | 196.2 | 13.1 / 20.1 | 465.0 |
| ds_30d | 5,760 | 2.5 / 2.9 | 124.3 | 5.8 / 6.1 | 208.2 | 12.8 / 14.2 | 429.0 |
| icmp_raw_24h | 1,440 | 3.4 / 4.8 | 89.9 | 3.3 / 4.0 | 82.0 | 3.5 / 5.6 | 100.1 |
| top_util_builtin | 25 | 60.1 / 66.3 | 336.9 | 59.1 / 66.2 | 361.5 | 66.1 / 92.4 | 476.1 |
| top_util_window | 25 | 219.8 / 314.1 | 498.7 | 216.7 / 242.8 | 546.1 | 221.5 / 283.2 | 548.6 |
| top_util_python | 25 | 86.1 / 101.3 | 377.2 | 85.5 / 98.8 | 361.1 | 88.1 / 103.2 | 410.4 |
| top_errors | 25 | 62.7 / 86.7 | 356.9 | 60.1 / 69.6 | 332.5 | 61.5 / 66.1 | 382.1 |
| current_device_seek | 80 | 0.4 / 0.5 | 7.0 | 0.4 / 0.5 | 7.2 | 0.4 / 0.5 | 6.9 |
| current_all_seek | 3,200 | 22.3 / 27.7 | 219.4 | 22.7 / 24.9 | 239.2 | 24.8 / 30.7 | 253.9 |

## Appendix B: the real adapter (M2 exit)

This appendix measures the shipped adapter, `vakr.adapters.sqlite.telemetry`, rather than the harness above. Every row went through the public API: `writer.write_batch` in 10-second flushes, `maintain` every 15 simulated minutes, and `rollups.write` once each bucket was 10 minutes old (E.10). The design changes it measures are ADR-021 amendments 2-5: the observation UUID in the key, poll and health partitions, and packed float32 rollup rows holding all six interface metrics.

| Item | Value |
|---|---|
| Script | `uv run python -m tests.performance.sqlite_telemetry.bench_adapter --days 2` ([`bench_adapter.py`](../../tests/performance/sqlite_telemetry/__init__.py)); results in [`adr021-adapter-digest.json`](adr021-adapter-digest.json) |
| Host, Python, SQLite | As in the context table above; the host was shared with four other agents (load average about 2) |
| Workload | 100 devices, 1,600 interfaces, 2 collectors: 3,200 interface, 200 ICMP, 200 SNMP, and 200 poll series, plus 2 health series, at 60 s. That is 3,802 rows a minute, 5.47 million a day, generated as validated domain objects (`tele_workload`, seed 21) |
| Load | 2 full UTC days: 10,949,760 native rows, 3,801,600 rollup metric buckets (19,800 every 15 minutes) |
| Data location | `.claude/bench-data/` on the real disk, capped at 8 GB (peak 1.7 GB), deleted afterwards |

### Ingest

| Measure | Value | ADR-021 harness |
|---|---|---|
| Rows/s inside `write_batch` (encoding, keys, routing, commit with `synchronous=FULL`) | **32,894** (548× the 60 rows/s needed) | 26,514-37,698 |
| Flush of 634 rows, p50 / p95 / max | 13.8 / 51.2 / 239 ms | 5-6 / 17-49 / 596 ms |
| `maintain` (move + truncating checkpoint), p50 / max | 0.75 / 1.08 s every 15 min | 0.7-0.9 / 3.4 s |
| `rollups.write` of one bucket for every series (19,800 metric buckets), p50 / max | 0.80 / 1.19 s every 15 min | not measured |
| Writer busy time | 633 s over 2 days: 0.37% | 0.2% |
| Bytes the process wrote per day | 19.8 GB | 15.6-15.8 GB |

The flush costs more than the harness's because the adapter builds rows from domain objects and resolves dictionary keys per row; it still leaves the writer idle 99.6% of the time. The extra bytes written come from the poll and health families and from rollup rows, which rewrite one page per series per bucket; at 7.2 TB a year this is still well within SSD endurance.

### Size

Measured with `dbstat` after two days; bytes per row include keys, page headers, and free space.

| Family | Rows | Bytes/row | Page fill | ADR-021 harness |
|---|---|---|---|---|
| `obs_iface_d*` | 9,216,000 | 156.6 | 0.90 | 154.5 |
| `obs_icmp_d*` | 576,000 | 116.6 | 0.90 | 111.9 |
| `obs_snmp_d*` | 576,000 | 80.1 | 0.88 | 77.5 |
| `obs_poll_d*` | 576,000 | 72.2 | 0.88 | not stored |
| `obs_health_d*` | 5,760 | 69.7 | 0.89 | not stored |
| `roll_iface_m*` (6 metrics a row) | 614,400 | 110.9 (18.5 per metric bucket) | 0.84 | 66.9 (6 aggregates, bit rates only) |
| `roll_icmp_m*` (3 metrics a row) | 38,400 | 61.9 (20.6 per metric bucket) | 0.88 | 56.2 |

Projected per node at the worst point, just before a scheduled drop (31.04 days of native data, 431 days of rollups, blueprint 27.3):

| Component | Per day | Worst case |
|---|---|---|
| Native, all five families | 799 MB | 24.8 GB |
| Rollups, interface and ICMP | 35.2 MB | 15.2 GB |
| WAL after a truncating checkpoint, staging, dictionaries, catalog | | under 0.1 GB |
| **Total** | | **40.1 GB** |

That is the 27.3 envelope of about 40 GB, so a `VACUUM INTO` or backup copy still fits a 100 GB volume, with about 20 GB left for the metadata database, the spool, and logs. Without the float32 rule (amendment 3) the rollups would need about 26 GB and the node about 52 GB.

### Queries

Warm is p50 of 20 runs on an open store; cold is p50 of 3 runs, each on a fresh store after `posix_fadvise(DONTNEED)` on the database and WAL. Latency includes building the domain read models.

| Query | Warm p50 / p95 (ms) | Cold p50 (ms) |
|---|---|---|
| One interface, 1 h raw (`interface_series`, 60 points) | 0.33 / 0.38 | 2.0 |
| One interface, 24 h raw, capped at 1,200 points (`interface_series`) | 6.4 / 7.1 | 11.0 |
| One interface, 24 h downsampled to 2-minute buckets (`downsampled_series`, 720 points) | 4.2 / 4.8 | 8.7 |
| One interface, 30-day window at 1-hour buckets (2 days of data present) | 3.6 / 4.0 | 12.3 |
| One device ICMP, 24 h raw, capped at 1,200 | 6.8 / 7.4 | 10.3 |
| `read_raw`, one page of 500 interface samples | 14.4 / 15.8 | 21.8 |
| `device_state`, one device | 0.17 / 0.26 | 2.6 |
| `interface_states`, one switch (40 interfaces) | 8.3 / 9.5 | 26.9 |
| `device_states`, all 100 devices | 17.0 / 23.9 | 77.0 |
| `rollups.read`, one interface, one day (96 buckets) | 1.5 / 1.6 | 3.3 |
| **400-day graph** from rollups (`downsampled_series`, 38,400 buckets to 1,200 points of 8 h) | **208 / 224** | **231** (max 324) |
| 400-day window through the port (`interface_series`, first 1,200 rollup buckets) | 22.5 / 27.6 | 22.1 |

* **30-day graphs.** Only two days were loaded, so the 30-day query read 2,880 samples. The harness showed cost grows with samples read (0.84 µs warm, 4 µs cold per sample); 30 full days of samples (43,200) project to about 40 ms warm and 180 ms cold, inside the 1 s page budget.
* **The 400-day graph** was measured on a separate store holding 400 days of all six metrics for one interface: a graph reads only one series' rows, which are contiguous in each month table, so the other series' volume does not change its cost. It decodes one metric from each packed row in Python (3-7 µs a row), which is slower than the harness's SQL aggregation (36 ms) and well inside MVP-ACC-05's 2 s.
* **Listings** pay for a seek per subject and partition, plus a full state per subject. 77 ms cold for every device is acceptable for a page; a dashboard refreshing every few seconds stays warm.
