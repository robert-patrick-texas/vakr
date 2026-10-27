-- description: Interface status records (E.6, E.35, MVP-ACC-10): the admin and oper status of
--   an interface each time it changes at Stage 1, and every interface's status at the first
--   Stage 1 of a device after the collector starts. Down interfaces still get no traffic
--   samples (E.27 #7); this is the evidence that explains their gaps. Adds one empty table and
--   two indexes; no existing table changes.
-- destructive: false
-- transactional: true
-- recovery: Creates one empty table and its indexes in one transaction; if it fails, nothing
--   changes and `vakr db migrate` can run again. Stepping back to a release without this
--   schema needs nothing: older releases never read the table.
-- data-volume: DDL only; finishes in milliseconds. Afterwards a few thousand rows a day per
--   collector at the MVP workload (status changes plus one baseline per interface at each
--   collector start), about 90 bytes each: tens of megabytes per node at 30 days (E.35).
-- test-impact: MIG-001, the additive check, the older-reader compat test, and
--   tests/contract/contract_interface_status.py.
-- compat: additive

-- Additive (E.14): a new table only. The family is never moved to day partitions: it is small
-- and keyed for per-interface reads, and a new partition template would be breaking (E.14).
-- Retention deletes rows behind the native horizon in bounded batches instead of dropping
-- partitions. Replication serves these rows attached to the SNMP device observation of the
-- same poll, because repl_log's family CHECK admits only families 1 to 5.
CREATE TABLE obs_ifstat_hot (
    series_key       INTEGER NOT NULL,
    collector_key    INTEGER NOT NULL,
    ts_us            INTEGER NOT NULL,
    observation_uuid BLOB    NOT NULL CHECK (length(observation_uuid) = 16),
    poll_uuid        BLOB    NOT NULL,
    device_key       INTEGER NOT NULL,
    ifindex_observed INTEGER NOT NULL,
    admin_status     INTEGER,
    oper_status      INTEGER,
    baseline         INTEGER NOT NULL CHECK (baseline IN (0, 1)),
    quality_flags    INTEGER NOT NULL,
    PRIMARY KEY (series_key, collector_key, ts_us, observation_uuid)
) STRICT, WITHOUT ROWID;

-- Retention deletes by time; replication finds a poll's records by device and poll.
CREATE INDEX obs_ifstat_by_time ON obs_ifstat_hot (ts_us);
CREATE INDEX obs_ifstat_by_poll ON obs_ifstat_hot (device_key, poll_uuid);
