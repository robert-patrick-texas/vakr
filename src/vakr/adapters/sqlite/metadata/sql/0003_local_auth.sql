-- description: Add local authentication: user accounts, login sessions, kiosk display tokens,
--   and login-failure throttle state (SEC-005, SEC-009, SEC-010, SEC-012, Appendix E.12).
-- destructive: false
-- transactional: true
-- recovery: Adds four tables, their triggers, and indexes in one transaction; on failure nothing
--   changes and the database stays at schema 2. Fix the cause and rerun `vakr db migrate`, or
--   restore the backup `vakr db migrate` took first.
-- data-volume: Creates empty tables; existing rows are not touched. Finishes in milliseconds.
-- test-impact: MIG-002 (schema 2 upgrades with data intact); the auth users and sessions
--   contract suites (AUTH-002, AUTH-004) run on this schema.

-- Local accounts (7.1 LocalUser). `username` is canonical (trimmed, lower case ASCII, E.12), so
-- the UNIQUE index enforces case-insensitive uniqueness and serves the (username, id) list
-- order. `password_hash` is an Argon2id PHC string that carries its own parameters (SEC-009).
-- Accounts are disabled, never deleted (META-009), so audit records keep their actor.
CREATE TABLE local_user (
    id                  BLOB    NOT NULL PRIMARY KEY CHECK (length(id) = 16),
    username            TEXT    NOT NULL UNIQUE,
    display_name        TEXT    NOT NULL,
    role                TEXT    NOT NULL,
    enabled             INTEGER NOT NULL CHECK (enabled IN (0, 1)),
    password_hash       TEXT    NOT NULL,
    created_at          INTEGER NOT NULL,
    updated_at          INTEGER NOT NULL,
    password_changed_at INTEGER NOT NULL,
    last_login_at       INTEGER,
    version             INTEGER NOT NULL CHECK (version >= 1)
) STRICT, WITHOUT ROWID;
CREATE TRIGGER local_user_never_deleted BEFORE DELETE ON local_user
BEGIN SELECT RAISE(ABORT, 'metadata records are retired, never deleted'); END;

-- Server-side sessions (SEC-010). Only the SHA-256 digest of the opaque token is stored, so a
-- copy of this file cannot be replayed as a login. Rows are deleted on logout, revocation, and
-- expiry; the indexes serve the digest lookup, per-user lists, and bounded expiry purges.
CREATE TABLE auth_session (
    id           BLOB    NOT NULL PRIMARY KEY CHECK (length(id) = 16),
    token_hash   BLOB    NOT NULL UNIQUE CHECK (length(token_hash) = 32),
    user_id      BLOB    NOT NULL REFERENCES local_user (id),
    created_at   INTEGER NOT NULL,
    last_seen_at INTEGER NOT NULL,
    expires_at   INTEGER NOT NULL,
    source       TEXT    NOT NULL
) STRICT, WITHOUT ROWID;
CREATE INDEX auth_session_by_user ON auth_session (user_id, created_at, id);
CREATE INDEX auth_session_by_expiry ON auth_session (expires_at, id);
CREATE INDEX auth_session_by_last_seen ON auth_session (last_seen_at);

-- Operator-role tokens for unattended displays (E.12): no idle expiry, an absolute one only.
CREATE TABLE kiosk_token (
    id           BLOB    NOT NULL PRIMARY KEY CHECK (length(id) = 16),
    token_hash   BLOB    NOT NULL UNIQUE CHECK (length(token_hash) = 32),
    label        TEXT    NOT NULL UNIQUE,
    created_by   TEXT    NOT NULL,
    created_at   INTEGER NOT NULL,
    expires_at   INTEGER NOT NULL,
    last_used_at INTEGER
) STRICT, WITHOUT ROWID;
CREATE INDEX kiosk_token_by_expiry ON kiosk_token (expires_at, id);

-- Consecutive login failures per attempted account and client address (SEC-012, E.12).
-- `account_key` is a keyed digest of the attempted username, so a password typed into the
-- username field is never stored readable. Throttle state, not history: rows are replaced,
-- cleared on success, and purged once quiet.
CREATE TABLE login_failure (
    account_key     TEXT    NOT NULL CHECK (length(account_key) = 64),
    source          TEXT    NOT NULL,
    failures        INTEGER NOT NULL CHECK (failures >= 1),
    first_failed_at INTEGER NOT NULL,
    last_failed_at  INTEGER NOT NULL,
    PRIMARY KEY (account_key, source)
) STRICT, WITHOUT ROWID;
CREATE INDEX login_failure_by_time ON login_failure (last_failed_at);
