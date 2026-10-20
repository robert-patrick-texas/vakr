-- description: Add identity reviews: discovered interfaces whose persistent identity waits for
--   an administrator (IFID-004, IFID-007).
-- destructive: false
-- transactional: true
-- recovery: Adds one table, its triggers, and indexes in one transaction; on failure nothing
--   changes and the database stays at schema 1. Fix the cause and rerun `vakr db migrate`, or
--   restore the backup `vakr db migrate` took first.
-- data-volume: Creates an empty table; existing rows are not touched. Finishes in milliseconds.
-- test-impact: MIG-002 (schema 1 upgrades with data intact); the reviews, ownership-transfer,
--   and change-counter contract suites (IFID-T05, IFID-T06, IFID-T07) run on this schema.

-- One row per discovered interface reconciliation could not match with certainty. The observed
-- row is kept so a resolution never needs another poll. `candidates` and `evidence` are JSON
-- arrays; `addresses` too, as in `interface`. A review is closed once: `state` leaves 'open' and
-- `closed_at` is set. Rows are never deleted (META-009); closed rows never change again.
CREATE TABLE identity_review (
    id            BLOB    NOT NULL PRIMARY KEY CHECK (length(id) = 16),
    device_id     BLOB    NOT NULL REFERENCES device (id),
    opened_at     INTEGER NOT NULL,
    outcome       TEXT    NOT NULL,
    if_index      INTEGER NOT NULL,
    if_name       TEXT    NOT NULL,
    if_descr      TEXT    NOT NULL,
    if_alias      TEXT    NOT NULL,
    if_type       INTEGER,
    mac           TEXT,
    if_speed      INTEGER,
    if_high_speed INTEGER,
    addresses     TEXT    NOT NULL CHECK (json_valid(addresses)),
    candidates    TEXT    NOT NULL CHECK (json_valid(candidates)),
    evidence      TEXT    NOT NULL CHECK (json_valid(evidence)),
    state         TEXT    NOT NULL,
    closed_at     INTEGER,
    resolution    BLOB    REFERENCES interface (id),
    actor         TEXT,
    version       INTEGER NOT NULL CHECK (version >= 1)
) STRICT, WITHOUT ROWID;
CREATE INDEX identity_review_by_time ON identity_review (opened_at, id);
CREATE INDEX identity_review_by_device ON identity_review (device_id, state, opened_at, id);

CREATE TRIGGER identity_review_never_deleted BEFORE DELETE ON identity_review
BEGIN SELECT RAISE(ABORT, 'metadata records are retired, never deleted'); END;
CREATE TRIGGER identity_review_closed_is_final BEFORE UPDATE ON identity_review
WHEN OLD.state <> 'open'
BEGIN SELECT RAISE(ABORT, 'a closed identity review never changes'); END;
