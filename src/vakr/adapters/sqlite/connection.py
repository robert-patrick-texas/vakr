"""Open SQLite databases the way every vakr SQLite adapter needs them (5.3).

Each read-write connection runs in WAL mode, so readers never wait for the
writer and the writer never waits for readers. It enforces foreign keys,
waits a bounded ``busy_timeout`` for another process's write lock, and uses
``synchronous=NORMAL``: in WAL mode that still cannot corrupt the database,
and a power cut loses at most the last commits. Connections run with
``autocommit=True`` so the driver never opens or commits a transaction
behind the adapter's back; adapters call ``begin``, ``commit``, and
``rollback`` themselves. Value encodings live in
``vakr.adapters.sqlite.encoding``.

Dependencies:
    sqlite3: The driver; only ``vakr.adapters`` may import it (ARCH-001).
    vakr.adapters.sqlite.errors: Driver-error translation.
    vakr.domain: Checks and errors.

Safety:
    Everything here blocks. Never call it on the asyncio event loop: async
    callers (M3) run it in a bounded worker thread or the dedicated writer
    thread (5.3). Pragma values are validated integers, never caller text.

Example:
    >>> conn = open_connection(Path("/var/lib/vakr/metadata.db"))  # doctest: +SKIP
    >>> begin(conn); conn.execute("SELECT 1"); commit(conn)  # doctest: +SKIP
"""

import sqlite3
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from vakr.adapters.sqlite.errors import translated
from vakr.domain.checks import require_int
from vakr.domain.errors import StorageError


class OpenMode(StrEnum):
    """How to open a database file (SQLite URI ``mode``)."""

    READ_ONLY = "ro"  # inspection, health checks, and backups; never creates or writes
    READ_WRITE = "rw"  # the services; the file must already exist (migrated)
    CREATE = "rwc"  # migrations only; creates a missing file


@dataclass(frozen=True, slots=True)
class SqliteSettings:
    """Bounds for one SQLite database (5.3; proposed Appendix E.20).

    Attributes:
        busy_timeout_ms: How long SQLite waits for another process's lock.
        lock_wait_ms: How long a unit of work waits in this process for the
            writer path or a free connection.
        max_waiting_writers: Units of work that may queue for the writer
            path; one more fails at once instead of queueing.
        max_connections: Open connections per database in this process.
    """

    busy_timeout_ms: int = 5_000
    lock_wait_ms: int = 10_000
    max_waiting_writers: int = 32
    max_connections: int = 8

    def __post_init__(self) -> None:
        """Validate the bounds.

        Args:
            None

        Returns:
            None

        Raises:
            ValidationError: If a bound is not a positive integer in range.
        """
        require_int(self.busy_timeout_ms, "busy_timeout_ms", minimum=1, maximum=600_000)
        require_int(self.lock_wait_ms, "lock_wait_ms", minimum=1, maximum=600_000)
        require_int(self.max_waiting_writers, "max_waiting_writers", minimum=0, maximum=10_000)
        require_int(self.max_connections, "max_connections", minimum=1, maximum=256)


DEFAULT_SETTINGS = SqliteSettings()
"""The bounds used unless configuration says otherwise."""


def open_connection(
    path: Path, settings: SqliteSettings = DEFAULT_SETTINGS, *, mode: OpenMode = OpenMode.READ_WRITE
) -> sqlite3.Connection:
    """Open ``path`` with vakr's pragmas.

    Args:
        path: Database file.
        settings: Timeouts.
        mode: Read-only, read-write (file must exist), or create.

    Returns:
        sqlite3.Connection: An ``autocommit=True`` connection usable from any
        one thread at a time.

    Raises:
        StorageError: If the file cannot be opened, is not a database, or
            cannot use WAL mode.
    """
    uri = f"{path.absolute().as_uri()}?mode={mode.value}"
    with translated():
        # check_same_thread=False: pools hand a connection to one thread at a
        # time, and the M3 writer thread is not the thread that opened it.
        conn = sqlite3.connect(uri, uri=True, autocommit=True, check_same_thread=False)
        try:
            _configure(conn, settings, mode)
        except BaseException:
            conn.close()
            raise
    return conn


def _configure(conn: sqlite3.Connection, settings: SqliteSettings, mode: OpenMode) -> None:
    """Apply the per-connection pragmas.

    Args:
        conn: A fresh connection.
        settings: Timeouts.
        mode: How it was opened.

    Returns:
        None

    Raises:
        StorageError: If WAL mode cannot be enabled.
        sqlite3.Error: If a pragma fails; the caller translates it.
    """
    conn.execute(f"PRAGMA busy_timeout = {int(settings.busy_timeout_ms)}")
    if mode is OpenMode.READ_ONLY:
        conn.execute("PRAGMA query_only = ON")
        return
    journal = conn.execute("PRAGMA journal_mode = WAL").fetchone()[0]
    if journal != "wal":
        # WAL needs shared memory; some network filesystems cannot provide it.
        raise StorageError(
            f"database {conn_name(conn)} cannot use WAL mode (journal mode stays {journal}); "
            "keep vakr state on a local filesystem",
            retryable=False,
        )
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA synchronous = NORMAL")


def conn_name(conn: sqlite3.Connection) -> str:
    """Return the file name of a connection's main database, for messages.

    Args:
        conn: An open connection.

    Returns:
        str: The file's base name, or ``"(memory)"``.

    Raises:
        None
    """
    row = conn.execute("PRAGMA database_list").fetchone()
    return Path(row[2]).name if row and row[2] else "(memory)"


def begin(conn: sqlite3.Connection, *, immediate: bool = False) -> None:
    """Start a transaction.

    A deferred transaction takes no lock until it reads (a snapshot) or
    writes; ``immediate`` takes the write lock now, waiting up to the busy
    timeout for another process.

    Args:
        conn: Connection without an open transaction.
        immediate: Take the write lock now.

    Returns:
        None

    Raises:
        StorageError: If the write lock stays busy past the timeout.
    """
    with translated():
        conn.execute("BEGIN IMMEDIATE" if immediate else "BEGIN DEFERRED")


def commit(conn: sqlite3.Connection) -> None:
    """Commit the open transaction.

    Args:
        conn: Connection with an open transaction.

    Returns:
        None

    Raises:
        StorageError: If the commit fails; the transaction is then rolled back.
    """
    try:
        with translated():
            conn.execute("COMMIT")
    except BaseException:
        rollback(conn)
        raise


def rollback(conn: sqlite3.Connection) -> None:
    """Roll back the open transaction, if any.

    Args:
        conn: Connection.

    Returns:
        None

    Raises:
        StorageError: If the rollback fails or the connection is already closed.
    """
    with translated():
        if conn.in_transaction:
            conn.execute("ROLLBACK")
