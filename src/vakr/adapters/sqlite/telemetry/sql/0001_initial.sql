-- description: Initial Compact telemetry schema (ADR-021 layout C+H): subject dictionaries,
--   pinned vocabulary codes, the partition catalog and the DDL templates of day and month
--   partitions, the staging boundary, and one time-ordered staging table per observation type.
-- destructive: false
-- transactional: true
-- recovery: Runs only on an empty database. If it fails, its transaction rolls back and the
--   file stays empty; remove telemetry.db and its -wal and -shm files and run `vakr db migrate`.
-- data-volume: Creates empty tables and a few hundred rows of codes; finishes in milliseconds.
-- test-impact: MIG-001 (fresh install); the DB-004, DB-005, DB-006 contract suites and
--   tests/unit/adapters/sqlite/telemetry run on this schema.

-- Conventions:
-- * Subjects and collectors are 16-byte UUID blobs in the dictionaries only. Telemetry rows
--   carry node-local integer keys, which never leave the adapter (DB-007) and are never
--   reused: triggers refuse deletes from the dictionaries.
-- * Timestamps are integer microseconds since 1970-01-01T00:00:00Z, UTC (invariant 11).
-- * Missing values are NULL, never 0 (invariant 1). Enums are pinned integer codes listed in
--   vocab_code (append-only). HC counters at or above 2^63 are stored as value - 2^64.
-- * series_key is the dictionary key of the subject: the interface for obs_iface, the
--   collector for obs_health, and the device for every other family.
-- * Native rows are keyed (series_key, collector_key, ts_us, observation_uuid). INSERT OR
--   IGNORE on that key makes replays idempotent (STORE-009) and keeps two observations
--   that share an instant apart, as the contract requires (ADR-021 amendment 2).
-- * Day and month partitions are created by the adapter from partition_template, and listed
--   in partition_catalog with the template version that built them. A later migration that
--   changes a partition's columns updates the template and its version; partitions built by
--   an older template keep their columns until retention drops them, and queries select NULL
--   for a column such a partition lacks. No migration rewrites partition data.

CREATE TABLE dict_collector (
    collector_key  INTEGER PRIMARY KEY,
    collector_uuid BLOB    NOT NULL UNIQUE CHECK (length(collector_uuid) = 16)
) STRICT;

CREATE TABLE dict_device (
    device_key  INTEGER PRIMARY KEY,
    device_uuid BLOB    NOT NULL UNIQUE CHECK (length(device_uuid) = 16)
) STRICT;

-- device_key is NULL for an interface known only from rollups until its first sample arrives.
CREATE TABLE dict_interface (
    interface_key  INTEGER PRIMARY KEY,
    interface_uuid BLOB    NOT NULL UNIQUE CHECK (length(interface_uuid) = 16),
    device_key     INTEGER REFERENCES dict_device (device_key)
) STRICT;
CREATE INDEX dict_interface_by_device ON dict_interface (device_key, interface_key);

CREATE TRIGGER dict_collector_keep BEFORE DELETE ON dict_collector
BEGIN SELECT RAISE(ABORT, 'telemetry dictionary keys are never removed'); END;
CREATE TRIGGER dict_device_keep BEFORE DELETE ON dict_device
BEGIN SELECT RAISE(ABORT, 'telemetry dictionary keys are never removed'); END;
CREATE TRIGGER dict_interface_keep BEFORE DELETE ON dict_interface
BEGIN SELECT RAISE(ABORT, 'telemetry dictionary keys are never removed'); END;

CREATE TABLE vocab_code (
    vocab TEXT    NOT NULL,
    value TEXT    NOT NULL,
    code  INTEGER NOT NULL CHECK (code >= 1),
    PRIMARY KEY (vocab, value),
    UNIQUE (vocab, code)
) STRICT, WITHOUT ROWID;

INSERT INTO vocab_code (vocab, value, code) VALUES
    ('AddressFamily', 'ipv4', 1), ('AddressFamily', 'ipv6', 2),
    ('BaselineState', 'valid', 1), ('BaselineState', 'no_baseline', 2),
    ('BaselineState', 'invalidated', 3),
    ('CollectorState', 'healthy', 1), ('CollectorState', 'degraded', 2),
    ('CollectorState', 'overloaded', 3), ('CollectorState', 'unavailable', 4),
    ('CounterFamily', 'hc64', 1), ('CounterFamily', 'legacy32', 2),
    ('IcmpState', 'reachable', 1), ('IcmpState', 'impaired', 2),
    ('IcmpState', 'unreachable', 3), ('IcmpState', 'unknown', 4),
    ('PollExecution', 'complete', 1), ('PollExecution', 'partial', 2),
    ('PollExecution', 'failed', 3), ('PollExecution', 'not_attempted', 4),
    ('PollExecution', 'missed_overrun', 5), ('PollExecution', 'running', 6),
    ('PollExecution', 'scheduled', 7), ('PollExecution', 'cancelled', 8),
    ('ReachabilityStrategy', 'icmp_gated', 1), ('ReachabilityStrategy', 'snmp_fallback', 2),
    ('ReachabilityStrategy', 'icmp_only', 3), ('ReachabilityStrategy', 'snmp_only', 4),
    ('RollupSlot.roll_icmp', 'rtt_median_us', 1), ('RollupSlot.roll_icmp', 'loss_pct', 2),
    ('RollupSlot.roll_icmp', 'jitter_us', 3),
    ('RollupSlot.roll_iface', 'in_bps', 1), ('RollupSlot.roll_iface', 'out_bps', 2),
    ('RollupSlot.roll_iface', 'in_pps', 3), ('RollupSlot.roll_iface', 'out_pps', 4),
    ('RollupSlot.roll_iface', 'in_errors', 5), ('RollupSlot.roll_iface', 'out_errors', 6),
    ('SnmpState', 'responding', 1), ('SnmpState', 'timeout', 2), ('SnmpState', 'error', 3),
    ('SnmpState', 'skipped', 4), ('SnmpState', 'partial', 5), ('SnmpState', 'unknown', 6),
    ('SnmpVersion', '2c', 1), ('SnmpVersion', '3', 2),
    ('UptimeContinuity', 'continuous', 1), ('UptimeContinuity', 'reset_detected', 2),
    ('UptimeContinuity', 'unknown', 3);

-- One row per family. ddl contains the placeholder {table}; the adapter substitutes a name
-- such as obs_iface_d20261001 or roll_iface_m202610, built from the family and a UTC date.
CREATE TABLE partition_template (
    family  TEXT    NOT NULL PRIMARY KEY,
    version INTEGER NOT NULL CHECK (version >= 1),
    ddl     TEXT    NOT NULL
) STRICT, WITHOUT ROWID;

-- Every day and month partition. Rollup partitions also keep the latest bucket end they hold
-- (the rollup frontier), their widest bucket, and their bucket count; native partitions leave
-- these at NULL, NULL, and 0.
CREATE TABLE partition_catalog (
    table_name       TEXT    NOT NULL PRIMARY KEY,
    family           TEXT    NOT NULL REFERENCES partition_template (family),
    start_us         INTEGER NOT NULL,
    end_us           INTEGER NOT NULL,
    template_version INTEGER NOT NULL,
    max_end_us       INTEGER,
    max_width_s      INTEGER,
    bucket_count     INTEGER NOT NULL DEFAULT 0 CHECK (bucket_count >= 0),
    CHECK (end_us > start_us)
) STRICT, WITHOUT ROWID;
CREATE INDEX partition_catalog_by_family ON partition_catalog (family, start_us);

-- staging_boundary_us: a native row is in staging exactly when ts_us >= this (ADR-021).
-- native_horizon_us, once present: native rows before it are expired and invisible.
-- rollup_horizon_us, once present: rollup buckets ending at or before it are expired.
CREATE TABLE telemetry_state (
    name  TEXT    NOT NULL PRIMARY KEY,
    value INTEGER NOT NULL
) STRICT, WITHOUT ROWID;
INSERT INTO telemetry_state (name, value) VALUES ('staging_boundary_us', 0);

INSERT INTO partition_template (family, version, ddl) VALUES
('obs_iface', 1, 'CREATE TABLE {table} (
    series_key INTEGER NOT NULL, collector_key INTEGER NOT NULL, ts_us INTEGER NOT NULL,
    observation_uuid BLOB NOT NULL, poll_uuid BLOB NOT NULL, device_key INTEGER NOT NULL,
    ifindex_observed INTEGER NOT NULL, admin_status INTEGER, oper_status INTEGER,
    in_octets INTEGER, out_octets INTEGER, in_packets INTEGER, out_packets INTEGER,
    in_errors INTEGER, out_errors INTEGER,
    in_bps REAL, out_bps REAL, in_pps REAL, out_pps REAL,
    in_error_delta INTEGER, out_error_delta INTEGER, in_error_rate REAL, out_error_rate REAL,
    sample_elapsed_us INTEGER, counter_family INTEGER, counter_discontinuity_ticks INTEGER,
    baseline_state INTEGER NOT NULL, quality_flags INTEGER NOT NULL,
    PRIMARY KEY (series_key, collector_key, ts_us, observation_uuid)
) STRICT, WITHOUT ROWID'),
('obs_icmp', 1, 'CREATE TABLE {table} (
    series_key INTEGER NOT NULL, collector_key INTEGER NOT NULL, ts_us INTEGER NOT NULL,
    observation_uuid BLOB NOT NULL, poll_uuid BLOB NOT NULL,
    address_family INTEGER NOT NULL, source_address TEXT, target_address TEXT NOT NULL,
    rtt_1_us INTEGER NOT NULL, rtt_2_us INTEGER, rtt_3_us INTEGER, rtt_4_us INTEGER,
    rtt_5_us INTEGER, rtt_more BLOB,
    sent_count INTEGER NOT NULL, received_count INTEGER NOT NULL, lost_count INTEGER NOT NULL,
    loss_percent REAL NOT NULL,
    min_rtt_us INTEGER, avg_rtt_us INTEGER, median_rtt_us INTEGER, max_rtt_us INTEGER,
    jitter_us INTEGER, state INTEGER NOT NULL, quality_flags INTEGER NOT NULL,
    PRIMARY KEY (series_key, collector_key, ts_us, observation_uuid)
) STRICT, WITHOUT ROWID'),
('obs_snmp', 1, 'CREATE TABLE {table} (
    series_key INTEGER NOT NULL, collector_key INTEGER NOT NULL, ts_us INTEGER NOT NULL,
    observation_uuid BLOB NOT NULL, poll_uuid BLOB NOT NULL,
    snmp_version INTEGER NOT NULL, request_count INTEGER NOT NULL,
    response_count INTEGER NOT NULL, timeout_count INTEGER NOT NULL,
    retry_count INTEGER NOT NULL, elapsed_ms INTEGER NOT NULL, sys_uptime_ticks INTEGER,
    uptime_continuity INTEGER NOT NULL, availability_state INTEGER NOT NULL,
    error_code TEXT, quality_flags INTEGER NOT NULL,
    PRIMARY KEY (series_key, collector_key, ts_us, observation_uuid)
) STRICT, WITHOUT ROWID'),
('obs_poll', 1, 'CREATE TABLE {table} (
    series_key INTEGER NOT NULL, collector_key INTEGER NOT NULL, ts_us INTEGER NOT NULL,
    observation_uuid BLOB NOT NULL, poll_uuid BLOB NOT NULL,
    configured_interval_s INTEGER NOT NULL, effective_interval_s INTEGER NOT NULL,
    reachability INTEGER NOT NULL, execution INTEGER NOT NULL, worker_uuid BLOB,
    started_offset_us INTEGER, completed_offset_us INTEGER, quality_flags INTEGER NOT NULL,
    PRIMARY KEY (series_key, collector_key, ts_us, observation_uuid)
) STRICT, WITHOUT ROWID'),
('obs_health', 1, 'CREATE TABLE {table} (
    series_key INTEGER NOT NULL, collector_key INTEGER NOT NULL, ts_us INTEGER NOT NULL,
    observation_uuid BLOB NOT NULL, state INTEGER NOT NULL, software_version TEXT NOT NULL,
    worker_uuid BLOB, clock_offset_us INTEGER, event_loop_lag_us INTEGER,
    queue_depth INTEGER, queue_capacity INTEGER, polls_completed INTEGER,
    polls_late INTEGER, polls_missed INTEGER, spool_bytes INTEGER, spool_used_pct REAL,
    active_devices INTEGER, active_interfaces INTEGER, quality_flags INTEGER NOT NULL,
    PRIMARY KEY (series_key, collector_key, ts_us, observation_uuid)
) STRICT, WITHOUT ROWID'),
-- Rollups: one row per subject, collector, and bucket. bucket_offset_s is seconds from the
-- month start, width_adj_s is bucket_seconds - 900 (so the 15-minute reference width costs no
-- bytes), and payload packs every metric of the row as rollup_codec describes.
('roll_iface', 1, 'CREATE TABLE {table} (
    series_key INTEGER NOT NULL, collector_key INTEGER NOT NULL,
    bucket_offset_s INTEGER NOT NULL, width_adj_s INTEGER NOT NULL, payload BLOB NOT NULL,
    PRIMARY KEY (series_key, collector_key, bucket_offset_s, width_adj_s)
) STRICT, WITHOUT ROWID'),
('roll_icmp', 1, 'CREATE TABLE {table} (
    series_key INTEGER NOT NULL, collector_key INTEGER NOT NULL,
    bucket_offset_s INTEGER NOT NULL, width_adj_s INTEGER NOT NULL, payload BLOB NOT NULL,
    PRIMARY KEY (series_key, collector_key, bucket_offset_s, width_adj_s)
) STRICT, WITHOUT ROWID');

-- Staging tables: the same columns as the day partitions, clustered on time first so the
-- 10-second commits append at the right edge of the B-tree (ADR-021).
CREATE TABLE obs_iface_hot (
    series_key INTEGER NOT NULL, collector_key INTEGER NOT NULL, ts_us INTEGER NOT NULL,
    observation_uuid BLOB NOT NULL, poll_uuid BLOB NOT NULL, device_key INTEGER NOT NULL,
    ifindex_observed INTEGER NOT NULL, admin_status INTEGER, oper_status INTEGER,
    in_octets INTEGER, out_octets INTEGER, in_packets INTEGER, out_packets INTEGER,
    in_errors INTEGER, out_errors INTEGER,
    in_bps REAL, out_bps REAL, in_pps REAL, out_pps REAL,
    in_error_delta INTEGER, out_error_delta INTEGER, in_error_rate REAL, out_error_rate REAL,
    sample_elapsed_us INTEGER, counter_family INTEGER, counter_discontinuity_ticks INTEGER,
    baseline_state INTEGER NOT NULL, quality_flags INTEGER NOT NULL,
    PRIMARY KEY (ts_us, series_key, collector_key, observation_uuid)
) STRICT, WITHOUT ROWID;

CREATE TABLE obs_icmp_hot (
    series_key INTEGER NOT NULL, collector_key INTEGER NOT NULL, ts_us INTEGER NOT NULL,
    observation_uuid BLOB NOT NULL, poll_uuid BLOB NOT NULL,
    address_family INTEGER NOT NULL, source_address TEXT, target_address TEXT NOT NULL,
    rtt_1_us INTEGER NOT NULL, rtt_2_us INTEGER, rtt_3_us INTEGER, rtt_4_us INTEGER,
    rtt_5_us INTEGER, rtt_more BLOB,
    sent_count INTEGER NOT NULL, received_count INTEGER NOT NULL, lost_count INTEGER NOT NULL,
    loss_percent REAL NOT NULL,
    min_rtt_us INTEGER, avg_rtt_us INTEGER, median_rtt_us INTEGER, max_rtt_us INTEGER,
    jitter_us INTEGER, state INTEGER NOT NULL, quality_flags INTEGER NOT NULL,
    PRIMARY KEY (ts_us, series_key, collector_key, observation_uuid)
) STRICT, WITHOUT ROWID;

CREATE TABLE obs_snmp_hot (
    series_key INTEGER NOT NULL, collector_key INTEGER NOT NULL, ts_us INTEGER NOT NULL,
    observation_uuid BLOB NOT NULL, poll_uuid BLOB NOT NULL,
    snmp_version INTEGER NOT NULL, request_count INTEGER NOT NULL,
    response_count INTEGER NOT NULL, timeout_count INTEGER NOT NULL,
    retry_count INTEGER NOT NULL, elapsed_ms INTEGER NOT NULL, sys_uptime_ticks INTEGER,
    uptime_continuity INTEGER NOT NULL, availability_state INTEGER NOT NULL,
    error_code TEXT, quality_flags INTEGER NOT NULL,
    PRIMARY KEY (ts_us, series_key, collector_key, observation_uuid)
) STRICT, WITHOUT ROWID;

CREATE TABLE obs_poll_hot (
    series_key INTEGER NOT NULL, collector_key INTEGER NOT NULL, ts_us INTEGER NOT NULL,
    observation_uuid BLOB NOT NULL, poll_uuid BLOB NOT NULL,
    configured_interval_s INTEGER NOT NULL, effective_interval_s INTEGER NOT NULL,
    reachability INTEGER NOT NULL, execution INTEGER NOT NULL, worker_uuid BLOB,
    started_offset_us INTEGER, completed_offset_us INTEGER, quality_flags INTEGER NOT NULL,
    PRIMARY KEY (ts_us, series_key, collector_key, observation_uuid)
) STRICT, WITHOUT ROWID;

CREATE TABLE obs_health_hot (
    series_key INTEGER NOT NULL, collector_key INTEGER NOT NULL, ts_us INTEGER NOT NULL,
    observation_uuid BLOB NOT NULL, state INTEGER NOT NULL, software_version TEXT NOT NULL,
    worker_uuid BLOB, clock_offset_us INTEGER, event_loop_lag_us INTEGER,
    queue_depth INTEGER, queue_capacity INTEGER, polls_completed INTEGER,
    polls_late INTEGER, polls_missed INTEGER, spool_bytes INTEGER, spool_used_pct REAL,
    active_devices INTEGER, active_interfaces INTEGER, quality_flags INTEGER NOT NULL,
    PRIMARY KEY (ts_us, series_key, collector_key, observation_uuid)
) STRICT, WITHOUT ROWID;
