"""One unit of work's SQLite transaction (5.2 rule 4, DB-002, DB-003, DB-008).

A unit of work begins with a *deferred* transaction, so opening one takes
no lock. Its first read fixes a WAL snapshot: later reads are consistent
with it, and none of them waits for a writer.

The first write turns the unit of work into *the* writer. It takes the
in-process ``WriterGate``, rolls back the read snapshot (nothing has been
written yet, so nothing is lost), and issues ``BEGIN IMMEDIATE``, which
takes SQLite's write lock with a fresh snapshot, waiting up to
``busy_timeout`` for another process. Starting the write transaction from
the newest data means it can never hit ``SQLITE_BUSY_SNAPSHOT``, and the
version and duplicate checks that follow see what is really stored. Reads
made before the first write may be older; that is the read-committed
behaviour of the reference adapter, and the version check on every update
is what prevents lost updates (DB-003).

``commit`` and ``rollback`` end the transaction, release the gate, and
start a new deferred transaction, so the unit of work can continue.

Dependencies:
    sqlite3: Rows and connections.
    vakr.adapters.sqlite.connection, errors, pool, writer: The shared layer.

Safety:
    Every statement runs through ``translated``; a write without
    ``begin_write`` is a programming error and raises ``RuntimeError``.

Example:
    >>> tx = SqliteTransaction(pool, gate)  # doctest: +SKIP
    >>> tx.open(); tx.begin_write(); tx.commit(); tx.close()  # doctest: +SKIP
"""

import sqlite3
from collections.abc import Generator, Sequence
from contextlib import contextmanager

from vakr.adapters.sqlite.connection import begin, commit, rollback
from vakr.adapters.sqlite.errors import DATABASE, ErrorContext, translated
from vakr.adapters.sqlite.pool import ConnectionPool
from vakr.adapters.sqlite.writer import WriterGate

type Params = Sequence[object]
"""Bound parameters of one statement."""


class SqliteTransaction:
    """The connection and transaction state of one unit of work.

    Attributes:
        None beyond the private connection, gate, and writer flag.
    """

    def __init__(self, pool: ConnectionPool, gate: WriterGate) -> None:
        """Bind to a store's pool and writer gate; nothing opens yet.

        Args:
            pool: Where to borrow a connection.
            gate: The store's single-writer path.

        Returns:
            None

        Raises:
            None
        """
        self._pool = pool
        self._gate = gate
        self._conn: sqlite3.Connection | None = None
        self._writing = False

    def open(self) -> None:
        """Borrow a connection and begin a deferred transaction.

        Args:
            None

        Returns:
            None

        Raises:
            StorageError: If no connection is available in time.
        """
        if self._conn is not None:
            return
        conn = self._pool.acquire()
        try:
            begin(conn)
        except BaseException:
            self._pool.release(conn)
            raise
        self._conn = conn

    def close(self) -> None:
        """Roll back anything uncommitted and return the connection.

        Args:
            None

        Returns:
            None

        Raises:
            None
        """
        conn, self._conn = self._conn, None
        self._end_write()
        if conn is not None:
            self._pool.release(conn)

    def query(
        self, sql: str, params: Params = (), context: ErrorContext = DATABASE
    ) -> list[sqlite3.Row]:
        """Run a read statement in the current snapshot.

        Args:
            sql: A fixed statement with ``?`` placeholders.
            params: Bound values.
            context: What is being read, for translated errors.

        Returns:
            list[sqlite3.Row]: Every result row.

        Raises:
            DomainError: Translated from any driver error.
        """
        conn = self._connection()
        with translated(context):
            return conn.execute(sql, params).fetchall()

    def execute(self, sql: str, params: Params = (), context: ErrorContext = DATABASE) -> int:
        """Run a write statement; ``begin_write`` must have been called.

        Args:
            sql: A fixed statement with ``?`` placeholders.
            params: Bound values.
            context: What is being written, for translated errors.

        Returns:
            int: Rows changed.

        Raises:
            DomainError: Translated from any driver error.
            RuntimeError: If the unit of work is not the writer yet.
        """
        conn = self._connection()
        if not self._writing:
            raise RuntimeError("a repository wrote without calling begin_write first")
        with translated(context):
            return conn.execute(sql, params).rowcount

    @contextmanager
    def atomic(self) -> Generator[None]:
        """Make one repository call's writes all-or-nothing inside the transaction.

        A call writes a row and then its membership rows. If a later
        statement fails, the savepoint undoes the earlier ones, so a caller
        that catches the error and commits never stores half a call, as the
        reference adapter guarantees by staging each call whole.

        Args:
            None

        Returns:
            Generator[None]: A context manager; ``begin_write`` must have run.

        Raises:
            DomainError: Whatever the block raises, after undoing its writes.
        """
        self.execute("SAVEPOINT repository_call")
        try:
            yield
        except BaseException:
            self.execute("ROLLBACK TO repository_call")
            self.execute("RELEASE repository_call")
            raise
        self.execute("RELEASE repository_call")

    def begin_write(self) -> None:
        """Become the writer: take the gate, then SQLite's write lock.

        Args:
            None

        Returns:
            None

        Raises:
            StorageError: If the gate or the write lock stays busy past its
                bound; the unit of work stays usable for reads.
        """
        if self._writing:
            return
        conn = self._connection()
        self._gate.acquire()
        try:
            rollback(conn)
            begin(conn, immediate=True)
        except BaseException:
            self._gate.release()
            self._restart(conn)
            raise
        self._writing = True

    def commit(self) -> None:
        """Commit, release the writer path, and begin a new deferred transaction.

        Args:
            None

        Returns:
            None

        Raises:
            StorageError: If the commit fails; nothing is committed then.
        """
        conn = self._connection()
        try:
            commit(conn)
        finally:
            self._end_write()
            self._restart(conn)

    def rollback(self) -> None:
        """Discard uncommitted changes and begin a new deferred transaction.

        Args:
            None

        Returns:
            None

        Raises:
            StorageError: If the rollback fails.
        """
        conn = self._connection()
        try:
            rollback(conn)
        finally:
            self._end_write()
            self._restart(conn)

    def _restart(self, conn: sqlite3.Connection) -> None:
        """Begin a deferred transaction if none is open.

        Args:
            conn: The unit of work's connection.

        Returns:
            None

        Raises:
            StorageError: If the connection is unusable.
        """
        if not conn.in_transaction:
            begin(conn)

    def _end_write(self) -> None:
        """Release the writer path if this unit of work holds it.

        Args:
            None

        Returns:
            None

        Raises:
            None
        """
        if self._writing:
            self._writing = False
            self._gate.release()

    def _connection(self) -> sqlite3.Connection:
        """Return the open connection.

        Args:
            None

        Returns:
            sqlite3.Connection: The borrowed connection.

        Raises:
            RuntimeError: If the unit of work is used outside its ``with`` block.
        """
        if self._conn is None:
            raise RuntimeError("use the unit of work inside its with block")
        return self._conn
