-- description: Metadata schema 5: add custom dashboards, named, ordered widget lists that
--   admins define (UI-004, DASH-007, Appendix E.31).
-- destructive: false
-- transactional: true
-- recovery: Adds one table in one transaction; on failure nothing changes and
--   the database stays at schema 4. Fix the cause and rerun `vakr db migrate`, or
--   restore the backup `vakr db migrate` took first.
-- data-volume: Creates an empty table; existing rows are not touched. Finishes in milliseconds.
-- test-impact: MIG-002 (schema 4 upgrades with data intact); the dashboards contract
--   suite (UI-004, DB-003) runs on this schema.

-- Custom dashboards (5.x Dashboard). `widgets` is a JSON array in the version `widget_schema`
-- names, upgraded on read by vakr.domain.dashboard_codec (DASH-007). `managed_by_*` and
-- `visibility` are stored for file-managed and public dashboards, which arrive later (META-008,
-- 27.4). Dashboards are presentation, not inventory, so rows are deleted rather than retired;
-- the audit record keeps the name. `version` is the optimistic-concurrency version (DB-003).
-- The table is not part of the collection change counter: a dashboard edit must not make the
-- collector reload its inventory.
CREATE TABLE dashboard (
    id                BLOB    NOT NULL PRIMARY KEY CHECK (length(id) = 16),
    name              TEXT    NOT NULL UNIQUE,
    description       TEXT    NOT NULL,
    managed_by_kind   TEXT    NOT NULL,
    managed_by_source TEXT    NOT NULL,
    visibility        TEXT    NOT NULL,
    refresh_s         INTEGER NOT NULL CHECK (refresh_s BETWEEN 15 AND 3600),
    widget_schema     INTEGER NOT NULL CHECK (widget_schema >= 1),
    widgets           TEXT    NOT NULL,
    created_by        TEXT    NOT NULL,
    created_at        INTEGER NOT NULL,
    updated_by        TEXT    NOT NULL,
    updated_at        INTEGER NOT NULL,
    version           INTEGER NOT NULL CHECK (version >= 1)
) STRICT, WITHOUT ROWID;
