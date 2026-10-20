-- description: Initial metadata schema: sites, tags, groups, devices, interfaces and their
--   annotations, collection policies, collectors, identity history, and audit events.
-- destructive: false
-- transactional: true
-- recovery: Runs only on an empty database. If it fails, its transaction rolls back and the
--   file stays empty; remove metadata.db and its -wal and -shm files and run `vakr db migrate`.
-- data-volume: Creates empty tables and indexes; finishes in milliseconds.
-- test-impact: MIG-001 (fresh install); the DB-001, DB-002, DB-003, DB-007, and DB-008 suites
--   run on this schema.

-- Conventions, shared by every table:
-- * Public identities are 16-byte UUID blobs (META-001, META-002). Tables are WITHOUT ROWID,
--   so no row number exists that could leak into a read model (DB-007, API-002).
-- * Blob order equals canonical UUID text order and BINARY collation equals code-point order,
--   so the indexes below match the list orders the ports define.
-- * Timestamps are integer microseconds since 1970-01-01T00:00:00Z, UTC (invariant 11).
-- * Editable records carry `version` for optimistic concurrency (DB-003) and `managed_by_*`
--   for ownership (META-008).
-- * Entity rows are never deleted; retiring is a state change (META-009, LIFE-002). Identity
--   history and audit events are append-only (IFID-005, SEC-018). Triggers enforce both.
--   Membership tables hold sets and change freely.
-- * Enumerated values are not CHECK-constrained: the domain validates them, and SQLite cannot
--   alter a CHECK without rebuilding the table, so a new enum member would need one.

CREATE TABLE tag (
    id                BLOB    NOT NULL PRIMARY KEY CHECK (length(id) = 16),
    name              TEXT    NOT NULL UNIQUE,
    description       TEXT    NOT NULL,
    managed_by_kind   TEXT    NOT NULL,
    managed_by_source TEXT    NOT NULL,
    version           INTEGER NOT NULL CHECK (version >= 1)
) STRICT, WITHOUT ROWID;

CREATE TABLE device_group (
    id                BLOB    NOT NULL PRIMARY KEY CHECK (length(id) = 16),
    name              TEXT    NOT NULL UNIQUE,
    description       TEXT    NOT NULL,
    managed_by_kind   TEXT    NOT NULL,
    managed_by_source TEXT    NOT NULL,
    version           INTEGER NOT NULL CHECK (version >= 1)
) STRICT, WITHOUT ROWID;

CREATE TABLE site (
    id                BLOB    NOT NULL PRIMARY KEY CHECK (length(id) = 16),
    name              TEXT    NOT NULL UNIQUE,
    code              TEXT    NOT NULL,
    region            TEXT,
    timezone          TEXT,
    description       TEXT    NOT NULL,
    managed_by_kind   TEXT    NOT NULL,
    managed_by_source TEXT    NOT NULL,
    version           INTEGER NOT NULL CHECK (version >= 1)
) STRICT, WITHOUT ROWID;

CREATE TABLE site_tag (
    site_id BLOB NOT NULL REFERENCES site (id),
    tag_id  BLOB NOT NULL REFERENCES tag (id),
    PRIMARY KEY (site_id, tag_id)
) STRICT, WITHOUT ROWID;
CREATE INDEX site_tag_by_tag ON site_tag (tag_id, site_id);

CREATE TABLE collector (
    id                BLOB    NOT NULL PRIMARY KEY CHECK (length(id) = 16),
    name              TEXT    NOT NULL UNIQUE,
    position          TEXT    NOT NULL,
    enabled           INTEGER NOT NULL CHECK (enabled IN (0, 1)),
    managed_by_kind   TEXT    NOT NULL,
    managed_by_source TEXT    NOT NULL,
    version           INTEGER NOT NULL CHECK (version >= 1)
) STRICT, WITHOUT ROWID;

-- `rules` is the policy document as canonical JSON (vakr.domain.json_values), stored verbatim.
CREATE TABLE collection_policy (
    id                BLOB    NOT NULL PRIMARY KEY CHECK (length(id) = 16),
    name              TEXT    NOT NULL UNIQUE,
    eval_order        INTEGER NOT NULL,
    scope             TEXT    NOT NULL,
    rules             TEXT    NOT NULL CHECK (json_valid(rules)),
    enabled           INTEGER NOT NULL CHECK (enabled IN (0, 1)),
    managed_by_kind   TEXT    NOT NULL,
    managed_by_source TEXT    NOT NULL,
    version           INTEGER NOT NULL CHECK (version >= 1)
) STRICT, WITHOUT ROWID;
CREATE INDEX collection_policy_by_order ON collection_policy (eval_order, name, id);

CREATE TABLE device (
    id                   BLOB    NOT NULL PRIMARY KEY CHECK (length(id) = 16),
    name                 TEXT    NOT NULL UNIQUE,
    poll_address         TEXT    NOT NULL,
    address_family       TEXT,
    site_id              BLOB    REFERENCES site (id),
    admin_state          TEXT    NOT NULL,
    poll_interval_s      INTEGER,
    discovery_interval_s INTEGER,
    reachability         TEXT,
    credential_ref       TEXT,   -- the credential's name only, never the secret (SEC-001)
    comment              TEXT    NOT NULL,
    managed_by_kind      TEXT    NOT NULL,
    managed_by_source    TEXT    NOT NULL,
    created_at           INTEGER NOT NULL,
    updated_at           INTEGER NOT NULL,
    version              INTEGER NOT NULL CHECK (version >= 1)
) STRICT, WITHOUT ROWID;
CREATE INDEX device_by_site ON device (site_id, name, id);

CREATE TABLE device_tag (
    device_id BLOB NOT NULL REFERENCES device (id),
    tag_id    BLOB NOT NULL REFERENCES tag (id),
    PRIMARY KEY (device_id, tag_id)
) STRICT, WITHOUT ROWID;
CREATE INDEX device_tag_by_tag ON device_tag (tag_id, device_id);

CREATE TABLE device_group_member (
    device_id BLOB NOT NULL REFERENCES device (id),
    group_id  BLOB NOT NULL REFERENCES device_group (id),
    PRIMARY KEY (device_id, group_id)
) STRICT, WITHOUT ROWID;
CREATE INDEX device_group_member_by_group ON device_group_member (group_id, device_id);

-- One row per logical interface. Its UUID, not if_index, is the identity (invariant 6).
-- `addresses` is a JSON array of interface addresses in observed order.
-- Columns from `interest` to `display_rank` are the operator annotation (META-007, META-011).
CREATE TABLE interface (
    id                    BLOB    NOT NULL PRIMARY KEY CHECK (length(id) = 16),
    device_id             BLOB    NOT NULL REFERENCES device (id),
    if_index              INTEGER,
    if_name               TEXT    NOT NULL,
    if_descr              TEXT    NOT NULL,
    if_alias              TEXT    NOT NULL,
    if_type               INTEGER,
    mac                   TEXT,
    if_speed              INTEGER,
    if_high_speed         INTEGER,
    addresses             TEXT    NOT NULL CHECK (json_valid(addresses)),
    lifecycle             TEXT    NOT NULL,
    comment               TEXT    NOT NULL,
    interest              TEXT    NOT NULL,
    sticky                INTEGER NOT NULL CHECK (sticky IN (0, 1)),
    is_wan_circuit        INTEGER NOT NULL CHECK (is_wan_circuit IN (0, 1)),
    provider              TEXT,
    circuit_id            TEXT,
    contracted_bps        INTEGER,
    capacity_override_bps INTEGER,
    site_override         BLOB    REFERENCES site (id),
    public_dashboard      INTEGER NOT NULL CHECK (public_dashboard IN (0, 1)),
    display_rank          INTEGER,
    managed_by_kind       TEXT    NOT NULL,
    managed_by_source     TEXT    NOT NULL,
    created_at            INTEGER NOT NULL,
    updated_at            INTEGER NOT NULL,
    version               INTEGER NOT NULL CHECK (version >= 1)
) STRICT, WITHOUT ROWID;
CREATE INDEX interface_by_device ON interface (device_id, if_name, id);

-- `kind` separates operator tags from description-derived ones (META-012, META-013).
CREATE TABLE interface_tag (
    interface_id BLOB NOT NULL REFERENCES interface (id),
    kind         TEXT NOT NULL CHECK (kind IN ('operator', 'description')),
    tag_id       BLOB NOT NULL REFERENCES tag (id),
    PRIMARY KEY (interface_id, kind, tag_id)
) STRICT, WITHOUT ROWID;
CREATE INDEX interface_tag_by_tag ON interface_tag (tag_id, interface_id);

-- `evidence` is a JSON array of evidence strings (IFID-005, IFID-007).
CREATE TABLE identity_history (
    id                BLOB    NOT NULL PRIMARY KEY CHECK (length(id) = 16),
    interface_id      BLOB    NOT NULL REFERENCES interface (id),
    device_id         BLOB    NOT NULL REFERENCES device (id),
    decided_at        INTEGER NOT NULL,
    outcome           TEXT    NOT NULL,
    source            TEXT    NOT NULL,
    previous_if_index INTEGER,
    observed_if_index INTEGER,
    score             REAL,
    runner_up_score   REAL,
    evidence          TEXT    NOT NULL CHECK (json_valid(evidence)),
    actor             TEXT
) STRICT, WITHOUT ROWID;
CREATE INDEX identity_history_by_interface ON identity_history (interface_id, decided_at, id);

-- `object_id` has no foreign key: an event may name any kind of object.
CREATE TABLE audit_event (
    id          BLOB    NOT NULL PRIMARY KEY CHECK (length(id) = 16),
    occurred_at INTEGER NOT NULL,
    actor       TEXT    NOT NULL,
    action      TEXT    NOT NULL,
    object_type TEXT    NOT NULL,
    object_id   BLOB,
    outcome     TEXT    NOT NULL,
    summary     TEXT    NOT NULL,   -- already redacted by the caller
    request_id  TEXT
) STRICT, WITHOUT ROWID;
CREATE INDEX audit_event_by_time ON audit_event (occurred_at, id);
CREATE INDEX audit_event_by_actor ON audit_event (actor, occurred_at, id);
CREATE INDEX audit_event_by_object ON audit_event (object_id, occurred_at, id);

-- No hard deletes (META-009). DROP TABLE in a later migration does not fire these.
CREATE TRIGGER tag_never_deleted BEFORE DELETE ON tag
BEGIN SELECT RAISE(ABORT, 'metadata records are retired, never deleted'); END;
CREATE TRIGGER device_group_never_deleted BEFORE DELETE ON device_group
BEGIN SELECT RAISE(ABORT, 'metadata records are retired, never deleted'); END;
CREATE TRIGGER site_never_deleted BEFORE DELETE ON site
BEGIN SELECT RAISE(ABORT, 'metadata records are retired, never deleted'); END;
CREATE TRIGGER collector_never_deleted BEFORE DELETE ON collector
BEGIN SELECT RAISE(ABORT, 'metadata records are retired, never deleted'); END;
CREATE TRIGGER collection_policy_never_deleted BEFORE DELETE ON collection_policy
BEGIN SELECT RAISE(ABORT, 'metadata records are retired, never deleted'); END;
CREATE TRIGGER device_never_deleted BEFORE DELETE ON device
BEGIN SELECT RAISE(ABORT, 'metadata records are retired, never deleted'); END;
CREATE TRIGGER interface_never_deleted BEFORE DELETE ON interface
BEGIN SELECT RAISE(ABORT, 'metadata records are retired, never deleted'); END;

-- Append-only history (IFID-005, SEC-018).
CREATE TRIGGER identity_history_append_only_update BEFORE UPDATE ON identity_history
BEGIN SELECT RAISE(ABORT, 'identity history is append-only'); END;
CREATE TRIGGER identity_history_append_only_delete BEFORE DELETE ON identity_history
BEGIN SELECT RAISE(ABORT, 'identity history is append-only'); END;
CREATE TRIGGER audit_event_append_only_update BEFORE UPDATE ON audit_event
BEGIN SELECT RAISE(ABORT, 'audit events are append-only'); END;
CREATE TRIGGER audit_event_append_only_delete BEFORE DELETE ON audit_event
BEGIN SELECT RAISE(ABORT, 'audit events are append-only'); END;
