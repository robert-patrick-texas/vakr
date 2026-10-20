"""A bounded pool of open connections to one SQLite database (5.3).

Opening a connection costs a file open, four pragmas, and a schema parse,
and closing the last one checkpoints and removes the WAL file, so units of
work borrow warm connections instead. The pool never holds more than
``max_connections`` (every concurrency limit is bounded); a borrower that
finds them all in use waits at most ``lock_wait_ms`` and then gets a
retryable ``StorageError``. A returned connection is rolled back first, so
no transaction or snapshot survives into the next borrower.

Dependencies:
    threading: The condition variable behind the bound.
    vakr.adapters.sqlite.connection: Opening and rolling back.
    vakr.domain.errors: ``StorageError``.

Safety:
    A connection is used by one borrower, in one thread, at a time. A
    connection whose rollback fails is closed, never reused.

Example:
    >>> pool = ConnectionPool(Path(tmp) / "x.db", mode=OpenMode.CREATE)  # doctest: +SKIP
    >>> conn = pool.acquire(); pool.release(conn); pool.close()  # doctest: +SKIP
"""

import sqlite3
import threading
from collections.abc import Callable
from pathlib import Path

from vakr.adapters.sqlite.connection import (
    DEFAULT_SETTINGS,
    OpenMode,
    SqliteSettings,
    open_connection,
    rollback,
)
from vakr.domain.errors import StorageError

type Prepare = Callable[[sqlite3.Connection], None]
"""Adapter-specific setup run once on each new connection, such as a row factory."""


def _no_prepare(_conn: sqlite3.Connection) -> None:
    """Leave a new connection as ``open_connection`` made it.

    Args:
        _conn: The connection.

    Returns:
        None

    Raises:
        None
    """


class ConnectionPool:
    """Up to ``max_connections`` reusable connections to one database file.

    Attributes:
        path: The database file.
    """

    def __init__(
        self,
        path: Path,
        settings: SqliteSettings = DEFAULT_SETTINGS,
        *,
        mode: OpenMode = OpenMode.READ_WRITE,
        prepare: Prepare = _no_prepare,
    ) -> None:
        """Create an empty pool; connections open on demand.

        Args:
            path: The database file.
            settings: Pragmas and bounds.
            mode: How each connection opens.
            prepare: Setup for each new connection.

        Returns:
            None

        Raises:
            None
        """
        self.path = path
        self._settings = settings
        self._mode = mode
        self._prepare = prepare
        self._cond = threading.Condition()
        self._idle: list[sqlite3.Connection] = []
        self._open = 0
        self._closed = False

    def acquire(self) -> sqlite3.Connection:
        """Borrow a connection, opening one if the bound allows.

        Args:
            None

        Returns:
            sqlite3.Connection: A connection with no open transaction.

        Raises:
            StorageError: If the pool is closed, every connection stays busy
                past ``lock_wait_ms``, or a new connection cannot open.
        """
        idle = self._reserve()
        if idle is not None:
            return idle
        try:
            conn = open_connection(self.path, self._settings, mode=self._mode)
        except BaseException:
            self._forget()
            raise
        try:
            self._prepare(conn)
        except BaseException:
            conn.close()
            self._forget()
            raise
        return conn

    def release(self, conn: sqlite3.Connection) -> None:
        """Return a borrowed connection, rolled back.

        Args:
            conn: A connection from ``acquire``.

        Returns:
            None

        Raises:
            None
        """
        try:
            rollback(conn)
        except StorageError:
            conn.close()
            self._forget()
            return
        with self._cond:
            if not self._closed:
                self._idle.append(conn)
                self._cond.notify()
                return
        conn.close()
        self._forget()

    def close(self) -> None:
        """Close idle connections; borrowed ones close when returned.

        Args:
            None

        Returns:
            None

        Raises:
            None
        """
        with self._cond:
            self._closed = True
            idle, self._idle = self._idle, []
            self._open -= len(idle)
            self._cond.notify_all()
        for conn in idle:
            conn.close()

    def _reserve(self) -> sqlite3.Connection | None:
        """Take an idle connection, or reserve a slot for a new one.

        Args:
            None

        Returns:
            sqlite3.Connection | None: An idle connection, or ``None`` when
            the caller should open one in the reserved slot.

        Raises:
            StorageError: If the pool is closed or no slot frees up in time.
        """
        limit = self._settings.max_connections
        with self._cond:
            ready = self._cond.wait_for(
                lambda: self._closed or self._idle or self._open < limit,
                self._settings.lock_wait_ms / 1000,
            )
            if self._closed:
                raise StorageError(f"database {self.path.name} is closed", retryable=False)
            if not ready:
                raise StorageError(
                    f"all {limit} connections to {self.path.name} are busy; retry shortly",
                    retryable=True,
                )
            if self._idle:
                return self._idle.pop()
            self._open += 1
            return None

    def _forget(self) -> None:
        """Give back the slot of a connection that is gone.

        Args:
            None

        Returns:
            None

        Raises:
            None
        """
        with self._cond:
            self._open -= 1
            self._cond.notify()
