-- description: Observation replication between two positions (MPOS-004, MPOS-005, ADR-024,
--   E.13): an ingestion log that lets a node serve its own collector's observations to the
--   other position in a durable sequence order, and the cursor a node keeps for each partner
--   collector it pulls from. Adds two empty tables; no existing table changes.
-- destructive: false
-- transactional: true
-- recovery: Creates two empty tables in one transaction; if it fails, nothing changes and
--   `vakr db migrate` can run again. Stepping back to a release without this schema means
--   dropping both tables and deleting the journal row of version 3 (the CHANGELOG's notes for
--   operators give the commands). Observations a partner replicated stay in the observation
--   tables either way; they are ordinary rows of the partner's collector.
-- data-volume: DDL only; finishes in milliseconds. Afterwards every locally ingested
--   observation adds one log row of about 40 bytes, about 3 GB per collector over 30 days at
--   the MVP workload (1,900 observations a minute), purged with native retention.
-- test-impact: MIG-001 and the tripwire in test_telemetry_schema.py;
--   tests/unit/replication/test_observation_replication.py and test_replication_log.py.
-- compat: additive

-- Additive (E.14): two new tables only. A release rolled back onto this schema keeps working,
-- but it does not log what it ingests meanwhile, so those observations never reach the partner
-- (ADR-024 records the limit). Releases without the floor logic refuse schema 3 outright, so
-- the release that ships it carries manual-update-required.

-- One row per observation this node's own spool ingested, in ingestion order. seq is the
-- replication cursor a partner pulls by (27.5 item 3): AUTOINCREMENT, so a seq is never
-- reused even after retention empties the table. The row points at the observation's primary
-- key (series_key, collector_key, ts_us, observation_uuid) in its family's staging table or
-- day partition. Observations replicated in from the partner are not logged: a node serves
-- only its own collector's observations. family: 1 obs_iface, 2 obs_icmp, 3 obs_snmp,
-- 4 obs_poll, 5 obs_health.
CREATE TABLE repl_log (
    seq              INTEGER PRIMARY KEY AUTOINCREMENT,
    collector_key    INTEGER NOT NULL,
    family           INTEGER NOT NULL CHECK (family BETWEEN 1 AND 5),
    series_key       INTEGER NOT NULL,
    ts_us            INTEGER NOT NULL,
    observation_uuid BLOB    NOT NULL CHECK (length(observation_uuid) = 16)
) STRICT;

-- The newest partner seq whose observations this node has stored, per partner collector. It is
-- written in the transaction that stores those observations, so a crash never skips or loses
-- one; a replayed page is a no-op (INSERT OR IGNORE on the observation keys).
CREATE TABLE repl_cursor (
    source_collector BLOB    NOT NULL PRIMARY KEY CHECK (length(source_collector) = 16),
    last_seq         INTEGER NOT NULL CHECK (last_seq >= 0),
    updated_us       INTEGER NOT NULL
) STRICT, WITHOUT ROWID;
