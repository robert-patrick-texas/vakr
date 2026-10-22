-- description: Add admin operation records: manual rediscovery requested through the web UI, API,
--   or CLI, with its progress and result (API-011, DISC-003, DISC-010, Appendix E.32).
-- destructive: false
-- transactional: true
-- recovery: Adds one table, its trigger, and indexes in one transaction; on failure nothing
--   changes and the database stays at schema 3. Fix the cause and rerun `vakr db migrate`, or
--   restore the backup `vakr db migrate` took first.
-- data-volume: Creates an empty table; existing rows are not touched. Finishes in milliseconds.
-- test-impact: MIG-002 (schema 3 upgrades with data intact); the operations contract suite
--   (DISC-T03, API-T06) runs on this schema.

-- One row per admin operation. A request inserts it `queued`; a collector moves it to `running`
-- with a compare-and-set on `version`, so two collectors never run the same one, then to
-- `succeeded` (with the result counts) or `failed` (with `error`). A request no collector picks up
-- in time becomes `expired`. Finished rows are an operational log, not inventory: they are
-- purged after 30 days, at most 10,000 kept (E.32), and do not count toward the change counter.
CREATE TABLE operation (
    id                 BLOB    NOT NULL PRIMARY KEY CHECK (length(id) = 16),
    kind               TEXT    NOT NULL CHECK (kind IN ('rediscovery')),
    device_id          BLOB    NOT NULL REFERENCES device (id),
    state              TEXT    NOT NULL
        CHECK (state IN ('queued', 'running', 'succeeded', 'failed', 'expired')),
    requested_by       TEXT    NOT NULL,
    requested_at       INTEGER NOT NULL,
    started_at         INTEGER,
    finished_at        INTEGER,
    collector_id       BLOB    REFERENCES collector (id),
    interfaces         INTEGER,
    new_interfaces     INTEGER,
    renumbered         INTEGER,
    missing            INTEGER,
    reviews_opened     INTEGER,
    capability_changes INTEGER,
    partial            INTEGER CHECK (partial IN (0, 1)),
    duration_ms        INTEGER,
    error              TEXT    CHECK (length(error) <= 500),
    version            INTEGER NOT NULL CHECK (version >= 1)
) STRICT, WITHOUT ROWID;
-- The pickup query (oldest queued first), the per-device list, the newest-first list, and purges.
CREATE INDEX operation_by_state ON operation (state, requested_at, id);
CREATE INDEX operation_by_device ON operation (device_id, requested_at, id);
CREATE INDEX operation_by_time ON operation (requested_at, id);
CREATE INDEX operation_by_finished ON operation (finished_at, id) WHERE finished_at IS NOT NULL;
-- One active operation of a kind per device, even between two writers (E.32).
CREATE UNIQUE INDEX operation_one_active ON operation (device_id, kind)
    WHERE state IN ('queued', 'running');

CREATE TRIGGER operation_finished_is_final BEFORE UPDATE ON operation
WHEN OLD.state IN ('succeeded', 'failed', 'expired')
BEGIN SELECT RAISE(ABORT, 'a finished operation never changes'); END;
