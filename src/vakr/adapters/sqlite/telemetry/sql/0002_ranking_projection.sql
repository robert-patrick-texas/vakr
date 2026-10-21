-- description: Ranking projection (E.28 decision 1, ADR-021 amendment 7): exact ranking partials
--   per 15-minute bucket and per hour, so 6-hour and 24-hour top-utilization and top-error
--   rankings stay within the 2 s dashboard budget of 12.9. Adds two empty tables; no existing
--   table changes.
-- destructive: false
-- transactional: true
-- recovery: Creates two empty tables in one transaction; if it fails, nothing changes and
--   `vakr db migrate` can run again. The tables hold only data derived from native samples,
--   which the collector rebuilds at every start, newest first, within a minute at MVP scale.
--   Stepping back to a release without this schema means dropping both tables and deleting
--   the journal row of version 2; the CHANGELOG's notes for operators give the commands.
-- data-volume: DDL only; finishes in milliseconds whatever the size of telemetry.db. The
--   collector's rebuild afterwards reads at most the last 25 hours of native data, in passes
--   of at most 100 buckets and 60 seconds.
-- test-impact: MIG-001 and the tripwire in test_telemetry_schema.py; the
--   RankingProjectionContract suite (memory and SQLite), the E.17 #13 ranking contract
--   suite, and tests/unit/adapters/sqlite/telemetry/test_ranking_projection.py.

-- One row per projected unit: a 15-minute bucket (width_s 900, E.10) or an hour (3600). A
-- unit listed here holds, in rank_iface, exactly the partials of its native interface samples
-- for every collector. The writer deletes the rows of the bucket and the hour holding any
-- interface sample it stores, in the same transaction; the projector inserts a bucket's row in
-- the transaction that read its samples, and an hour's row once its four buckets are listed.
-- built_us is when the unit was built (diagnostics only).
CREATE TABLE rank_unit (
    width_s  INTEGER NOT NULL CHECK (width_s IN (900, 3600)),
    start_us INTEGER NOT NULL,
    built_us INTEGER NOT NULL,
    PRIMARY KEY (width_s, start_us),
    CHECK (start_us % (width_s * 1000000) = 0)
) STRICT, WITHOUT ROWID;

-- One ranking partial per collector, unit, and interface with samples in the unit. Keyed
-- collector first, so a ranking (always one collector, MPOS-008) reads one key range per run
-- of projected units of one width. It is a rowid table with the key as its index, not
-- WITHOUT ROWID: an hour's payload (about 1 KB at 60 s polling) overflows an index b-tree
-- cell on 4 KiB pages, and this layout measured 2.1 times smaller and about 30% faster to
-- read, because a unit's rows are inserted together. payload packs, little-endian: a version
-- byte; the sample, inbound, outbound, counted-error, and packet-sample counts; the number of
-- packet parts; the inbound and outbound error sums; then every available in_bps and out_bps
-- value in ascending order and the exact packet parts, all as float64 (ranking_parts.py).
-- Rows of a unit not listed in rank_unit are stale and never read.
CREATE TABLE rank_iface (
    collector_key INTEGER NOT NULL,
    width_s       INTEGER NOT NULL,
    start_us      INTEGER NOT NULL,
    series_key    INTEGER NOT NULL,
    payload       BLOB    NOT NULL,
    PRIMARY KEY (collector_key, width_s, start_us, series_key)
) STRICT;
