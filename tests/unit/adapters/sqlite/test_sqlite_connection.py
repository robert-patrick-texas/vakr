"""Shared SQLite layer: pragmas, encodings, and driver-error translation (5.3, 5.2 rule 2).

Dependencies:
    pytest, sqlite3; vakr.adapters.sqlite.connection, errors.

Example:
    $ uv run pytest tests/unit/adapters/sqlite/test_sqlite_connection.py
"""

import sqlite3
import uuid
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path

import pytest

from vakr.adapters.sqlite.connection import (
    OpenMode,
    SqliteSettings,
    begin,
    commit,
    open_connection,
    rollback,
)
from vakr.adapters.sqlite.connection import _configure as configure
from vakr.adapters.sqlite.encoding import (
    blob_to_uuid,
    decode_time,
    encode_time,
    optional_blob,
    optional_uuid,
    uuid_to_blob,
)
from vakr.adapters.sqlite.errors import ErrorContext, translate_error, translated
from vakr.domain.errors import (
    ConflictError,
    DuplicateError,
    NotFoundError,
    StorageError,
    ValidationError,
)


def _pragma(conn: sqlite3.Connection, name: str) -> object:
    """Read one pragma value.

    Args:
        conn: Connection.
        name: Pragma name.

    Returns:
        object: Its value.

    Raises:
        None
    """
    return conn.execute(f"PRAGMA {name}").fetchone()[0]


def test_read_write_connection_pragmas(tmp_path: Path) -> None:
    """5.3: WAL, foreign keys, the busy timeout, and synchronous=NORMAL on every connection.

    Args:
        tmp_path: Directory for the database.

    Returns:
        None

    Raises:
        None
    """
    settings = SqliteSettings(busy_timeout_ms=1234)
    with closing(open_connection(tmp_path / "x.db", settings, mode=OpenMode.CREATE)) as conn:
        assert conn.autocommit is True  # the adapter, not the driver, owns transactions
        values = [_pragma(conn, p) for p in ("journal_mode", "foreign_keys", "busy_timeout")]
        assert values == ["wal", 1, 1234]
        assert _pragma(conn, "synchronous") == 1  # NORMAL
    with closing(open_connection(tmp_path / "x.db", mode=OpenMode.READ_ONLY)) as ro:
        assert _pragma(ro, "query_only") == 1
        with pytest.raises(StorageError) as caught, translated():
            ro.execute("CREATE TABLE t (x INTEGER)")
        assert not caught.value.retryable


def test_open_refuses_missing_files_and_non_wal_databases(tmp_path: Path) -> None:
    """5.3: services never create a database by accident, and never run without WAL.

    Args:
        tmp_path: Directory for the database.

    Returns:
        None

    Raises:
        None
    """
    with pytest.raises(StorageError, match="cannot be opened"):
        open_connection(tmp_path / "missing.db")
    assert not (tmp_path / "missing.db").exists()
    junk = tmp_path / "junk.db"
    junk.write_bytes(b"not a database" * 100)
    with pytest.raises(StorageError, match="not a SQLite database"):
        open_connection(junk)  # the half-configured connection is closed, not leaked
    memory = sqlite3.connect(":memory:", autocommit=True)
    with closing(memory), pytest.raises(StorageError, match="cannot use WAL mode"):
        configure(memory, SqliteSettings(), OpenMode.READ_WRITE)


def test_settings_are_bounded() -> None:
    """Every SQLite bound is a positive integer within its range (17.2: bounded limits).

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    for bad in ({"busy_timeout_ms": 0}, {"lock_wait_ms": -1}, {"max_connections": 0}):
        with pytest.raises(ValidationError):
            SqliteSettings(**bad)


def test_uuid_blobs_round_trip_and_sort_like_canonical_text() -> None:
    """DB-007, storage.md: blob order equals canonical UUID text order.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    ids = [uuid.uuid4() for _ in range(200)]
    assert all(blob_to_uuid(uuid_to_blob(i)) == i for i in ids)
    assert sorted(ids, key=uuid_to_blob) == sorted(ids, key=str)
    assert (optional_blob(None), optional_uuid(None)) == (None, None)
    assert optional_uuid(optional_blob(ids[0])) == ids[0]
    for bad in (b"short", "0190a3c4-0000-7000-8000-000000000001", 7):
        with pytest.raises(StorageError):
            blob_to_uuid(bad)


def test_timestamps_are_exact_integer_microseconds() -> None:
    """Invariant 11: UTC times store as integer microseconds and round-trip exactly.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    moment = datetime(2026, 10, 20, 3, 15, 0, 123456, tzinfo=UTC)
    assert decode_time(encode_time(moment)) == moment
    for bad in (True, 1.5, "1", 10**20):
        with pytest.raises(StorageError):
            decode_time(bad)


def _error(code: int, name: str) -> sqlite3.Error:
    """Build a driver error carrying an extended result code.

    Args:
        code: Extended result code.
        name: Its symbolic name.

    Returns:
        sqlite3.Error: The error, with a message that must never be shown.

    Raises:
        None
    """
    exc = sqlite3.OperationalError("secret-looking driver text")
    exc.sqlite_errorcode = code
    exc.sqlite_errorname = name
    return exc


@pytest.mark.parametrize(
    ("code", "name", "kind", "retryable"),
    [
        (sqlite3.SQLITE_BUSY, "SQLITE_BUSY", StorageError, True),
        (sqlite3.SQLITE_LOCKED, "SQLITE_LOCKED", StorageError, True),
        (sqlite3.SQLITE_FULL, "SQLITE_FULL", StorageError, False),
        (sqlite3.SQLITE_CORRUPT, "SQLITE_CORRUPT", StorageError, False),
        (sqlite3.SQLITE_IOERR_WRITE, "SQLITE_IOERR_WRITE", StorageError, False),
        (sqlite3.SQLITE_INTERNAL, "SQLITE_INTERNAL", StorageError, False),
        (sqlite3.SQLITE_CONSTRAINT_CHECK, "SQLITE_CONSTRAINT_CHECK", ValidationError, None),
        (sqlite3.SQLITE_BUSY_SNAPSHOT, "SQLITE_BUSY_SNAPSHOT", ConflictError, None),
    ],
)
def test_driver_errors_translate_without_driver_text(
    code: int, name: str, kind: type[Exception], retryable: bool | None
) -> None:
    """5.2 rule 2: result codes map to domain errors; driver messages never surface.

    Args:
        code: Extended result code.
        name: Its symbolic name.
        kind: Expected domain error type.
        retryable: Expected ``StorageError.retryable``, if a ``StorageError``.

    Returns:
        None

    Raises:
        None
    """
    err = translate_error(_error(code, name), ErrorContext("device", "d-1", 3))
    assert isinstance(err, kind)
    assert "secret-looking" not in str(err)
    if isinstance(err, StorageError):
        assert err.retryable is retryable
        assert name in str(err)
    if isinstance(err, ConflictError):
        assert (err.entity, err.expected_version) == ("device", 3)


def test_real_constraint_errors_translate(tmp_path: Path) -> None:
    """Unique, foreign-key, overflow, Unicode, and closed-connection errors from a live driver.

    Args:
        tmp_path: Directory for the database.

    Returns:
        None

    Raises:
        None
    """
    conn = open_connection(tmp_path / "x.db", mode=OpenMode.CREATE)
    conn.execute("CREATE TABLE p (id INTEGER PRIMARY KEY, name TEXT UNIQUE)")
    conn.execute("CREATE TABLE c (p INTEGER REFERENCES p (id))")
    conn.execute("INSERT INTO p VALUES (1, 'a')")
    cases = [
        ("INSERT INTO p VALUES (2, 'a')", (), DuplicateError),
        ("INSERT INTO c VALUES (9)", (), NotFoundError),
        ("INSERT INTO p VALUES (?, 'b')", (2**70,), ValidationError),
        ("INSERT INTO p VALUES (3, ?)", ("\ud800",), ValidationError),
    ]
    for sql, params, kind in cases:
        with pytest.raises(kind) as caught, translated(ErrorContext("p", "k")):
            conn.execute(sql, params)
        assert "constraint failed" not in str(caught.value)
    conn.close()
    with pytest.raises(StorageError, match="ProgrammingError"), translated():
        conn.execute("SELECT 1")
    with pytest.raises(StorageError):
        rollback(conn)  # a closed connection is reported, not leaked as a driver error
    passthrough = NotFoundError("x", 1)
    assert translate_error(passthrough) is passthrough


def test_transaction_helpers(tmp_path: Path) -> None:
    """``begin``, ``commit``, and ``rollback`` drive explicit transactions.

    Args:
        tmp_path: Directory for the database.

    Returns:
        None

    Raises:
        None
    """
    with closing(open_connection(tmp_path / "x.db", mode=OpenMode.CREATE)) as conn:
        begin(conn, immediate=True)
        conn.execute("CREATE TABLE t (x INTEGER)")
        commit(conn)
        begin(conn)
        conn.execute("INSERT INTO t VALUES (1)")
        rollback(conn)
        rollback(conn)  # no transaction: nothing to do
        assert conn.execute("SELECT count(*) FROM t").fetchone()[0] == 0
        with pytest.raises(StorageError):
            commit(conn)  # nothing to commit: reported, and the connection stays usable
        assert not conn.in_transaction
