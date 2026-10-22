-- description: Metadata schema 6: add replication tokens, the opaque bearer tokens a partner
--   node presents to pull metadata and observations from this one (MPOS-006, ADR-024).
-- destructive: false
-- transactional: true
-- recovery: Adds one table in one transaction; on failure nothing changes and
--   the database stays at schema 5. Fix the cause and rerun `vakr db migrate`, or
--   restore the backup `vakr db migrate` took first.
-- data-volume: Creates an empty table; existing rows are not touched. Finishes in milliseconds.
-- test-impact: MIG-002 (schema 5 upgrades with data intact); the replication token
--   tests (MPOS-006) run on this schema.

-- The kiosk_token shape (0003): only the SHA-256 digest of a token is stored. A token
-- grants the replication scopes and nothing else. The table is node-local: it is never
-- itself replicated, so each node issues the token its partner presents.
CREATE TABLE replication_token (
    id           BLOB    NOT NULL PRIMARY KEY CHECK (length(id) = 16),
    token_hash   BLOB    NOT NULL UNIQUE CHECK (length(token_hash) = 32),
    label        TEXT    NOT NULL UNIQUE,
    created_by   TEXT    NOT NULL,
    created_at   INTEGER NOT NULL,
    expires_at   INTEGER NOT NULL,
    last_used_at INTEGER
) STRICT, WITHOUT ROWID;
CREATE INDEX replication_token_by_expiry ON replication_token (expires_at, id);
