"""The SQLite telemetry store: schema family, preflight, connections, and transactions.

``telemetry_family`` loads the packaged migrations under ``sql/``; the file
is ``telemetry.db`` beside ``metadata.db`` in the state directory, marked
with its own ``PRAGMA application_id`` so neither database can be mistaken
for the other. Opening a ``SqliteTelemetryStore`` runs the startup
preflight (16.3): the schema must be exactly the one this release runs
on, so a newer schema is refused with ``SchemaTooNewError`` (MIG-004).

The store owns what every port view shares: a bounded connection pool
tuned per E.21 (WAL, ``synchronous=FULL``, a 64 MiB cache), the bounded
single-writer gate, the dictionary key cache, and the retention policy.
``reading`` gives a connection inside one read transaction, so a query
sees the catalog, staging, and partitions from one snapshot; ``writing``
gives one ``BEGIN IMMEDIATE`` transaction that commits durably or not at
all (STORE-010).

Dependencies:
    importlib.resources: The packaged ``sql/*.sql`` files.
    vakr.adapters.sqlite.*: Connections, pool, gate, errors, and migrations.

Safety:
    Synchronous and blocking: async callers must use a worker thread (5.3).
    The store never migrates by itself; only ``vakr db migrate`` does.

Example:
    >>> TELEMETRY_FILE, hex(TELEMETRY_APPLICATION_ID)
    ('telemetry.db', '0x766b746c')
"""

import functools
import sqlite3
from collections.abc import Generator
from contextlib import contextmanager
from datetime import timedelta
from importlib.resources import files
from pathlib import Path

from vakr.adapters.sqlite.connection import DEFAULT_SETTINGS, SqliteSettings, begin, commit
from vakr.adapters.sqlite.errors import translated
from vakr.adapters.sqlite.migrations import SchemaFamily, SqliteMigrationManager, load_migrations
from vakr.adapters.sqlite.pool import ConnectionPool
from vakr.adapters.sqlite.telemetry.keys import KeyCache
from vakr.adapters.sqlite.telemetry.tuning import PRAGMAS
from vakr.adapters.sqlite.writer import WriterGate
from vakr.ports.clock import Clock
from vakr.ports.telemetry.results import RetentionPolicy

TELEMETRY_FILE = "telemetry.db"
"""File name of the telemetry database inside the state directory."""

TELEMETRY_APPLICATION_ID = 0x766B746C  # ASCII "vktl": marks a vakr telemetry database

COMPACT_POLICY = RetentionPolicy(native=timedelta(days=30), rollup=timedelta(days=400))
"""Compact retention when configuration says nothing else (5.1, E.10)."""


@functools.cache
def telemetry_family() -> SchemaFamily:
    """Return the telemetry schema family with its packaged migrations.

    Args:
        None

    Returns:
        SchemaFamily: Migrations ``0001`` onwards, loaded once per process.

    Raises:
        ValidationError: If a packaged migration file is malformed (a build bug).
    """
    migrations = load_migrations(files("vakr.adapters.sqlite.telemetry").joinpath("sql"))
    return SchemaFamily("telemetry", TELEMETRY_APPLICATION_ID, migrations)


def telemetry_migrations(
    path: Path, *, settings: SqliteSettings = DEFAULT_SETTINGS, clock: Clock | None = None
) -> SqliteMigrationManager:
    """Return the ``MigrationManager`` for a telemetry database file.

    Args:
        path: The file; it need not exist yet.
        settings: Connection timeouts.
        clock: Time source for the journal and backup names.

    Returns:
        SqliteMigrationManager: The manager.

    Raises:
        None
    """
    return SqliteMigrationManager(path, telemetry_family(), settings=settings, clock=clock)


def tune(conn: sqlite3.Connection) -> None:
    """Apply the E.21 telemetry PRAGMAs to a new pooled connection.

    Args:
        conn: A connection opened by ``open_connection``.

    Returns:
        None

    Raises:
        sqlite3.Error: If a PRAGMA fails; the pool closes the connection.
    """
    for pragma in PRAGMAS:
        conn.execute(pragma)


class SqliteTelemetryStore:
    """An open telemetry database shared by every port view on it.

    Attributes:
        path: The database file.
        policy: Retention policy in force.
        keys: Committed dictionary keys, shared by readers and the writer.
    """

    def __init__(
        self,
        path: Path,
        settings: SqliteSettings = DEFAULT_SETTINGS,
        *,
        policy: RetentionPolicy = COMPACT_POLICY,
        family: SchemaFamily | None = None,
    ) -> None:
        """Run the startup preflight and prepare the pool and writer gate.

        Args:
            path: A migrated telemetry database.
            settings: Connection bounds (E.20).
            policy: Retention policy, normally ``retention.retention_policy(config)``.
            family: Schema family to require; tests pass a synthetic later
                release. Defaults to ``telemetry_family()``.

        Returns:
            None

        Raises:
            SchemaTooNewError: If a newer release wrote the schema (MIG-004).
            MigrationError: If the schema is missing, older, interrupted, or mismatched.
            StorageError: If the file cannot be read.
        """
        chosen = family if family is not None else telemetry_family()
        SqliteMigrationManager(path, chosen, settings=settings).require_current()
        self.path = path
        self.policy = policy
        self.keys = KeyCache()
        self._pool = ConnectionPool(path, settings, prepare=tune)
        self._gate = WriterGate(settings)

    @contextmanager
    def reading(self) -> Generator[sqlite3.Connection]:
        """Lend a connection inside one read transaction (one snapshot).

        Args:
            None

        Returns:
            Generator[sqlite3.Connection]: A context manager yielding the connection.

        Raises:
            StorageError: If no connection is free in time, or a statement fails.
        """
        conn = self._pool.acquire()
        try:
            begin(conn)
            with translated():
                yield conn
        finally:
            self._pool.release(conn)

    @contextmanager
    def writing(self) -> Generator[sqlite3.Connection]:
        """Run the block as one ``BEGIN IMMEDIATE`` transaction through the writer gate.

        The transaction commits when the block ends normally and rolls back
        when it raises; ``COMMIT`` returns only once the data is durable
        (``synchronous=FULL``).

        Args:
            None

        Returns:
            Generator[sqlite3.Connection]: A context manager yielding the connection.

        Raises:
            StorageError: If the gate or a connection is not free in time, or a
                statement or the commit fails; nothing is then committed.
        """
        self._gate.acquire()
        try:
            conn = self._pool.acquire()
            try:
                begin(conn, immediate=True)
                with translated():
                    yield conn
                commit(conn)
            finally:
                self._pool.release(conn)
        finally:
            self._gate.release()

    def checkpoint(self) -> tuple[int, int, int]:
        """Run ``PRAGMA wal_checkpoint(TRUNCATE)`` while holding the writer gate.

        Args:
            None

        Returns:
            tuple[int, int, int]: SQLite's ``(busy, log frames, checkpointed frames)``.

        Raises:
            StorageError: If the gate or a connection is not free in time.
        """
        self._gate.acquire()
        try:
            conn = self._pool.acquire()
            try:
                with translated():
                    row = conn.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
                return int(row[0]), int(row[1]), int(row[2])
            finally:
                self._pool.release(conn)
        finally:
            self._gate.release()

    def close(self) -> None:
        """Close idle connections; borrowed ones close when returned.

        Args:
            None

        Returns:
            None

        Raises:
            None
        """
        self._pool.close()
