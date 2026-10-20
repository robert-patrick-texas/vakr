"""The SQLite metadata store: schema family, preflight, and unit-of-work factory (5.6, 16.3).

``metadata_family`` loads the packaged migrations under ``sql/``. Opening a
``SqliteMetadataStore`` runs the startup preflight first: the schema must be
exactly the one this release runs on, so a newer schema is refused with the
actionable ``SchemaTooNewError`` (MIG-004) and an older one asks for
``vakr db migrate``. The store then owns the connection pool and the
writer gate that every unit of work shares.

Dependencies:
    importlib.resources: The packaged ``sql/*.sql`` files.
    vakr.adapters.sqlite.*: Pool, gate, migrations, and the unit of work.

Safety:
    The store never migrates by itself; only ``vakr db migrate`` changes a
    schema, after taking a backup.

Example:
    >>> store = SqliteMetadataStore(paths.state_dir / METADATA_FILE)  # doctest: +SKIP
    >>> with sqlite_uow_factory(store)() as uow:  # doctest: +SKIP
    ...     uow.devices.get_by_name("core1")
"""

import functools
import sqlite3
from importlib.resources import files
from pathlib import Path

from vakr.adapters.sqlite.connection import DEFAULT_SETTINGS, SqliteSettings
from vakr.adapters.sqlite.metadata.uow import SqliteUnitOfWork
from vakr.adapters.sqlite.migrations import SchemaFamily, SqliteMigrationManager, load_migrations
from vakr.adapters.sqlite.pool import ConnectionPool
from vakr.adapters.sqlite.writer import WriterGate
from vakr.ports.clock import Clock
from vakr.ports.metadata.unit_of_work import MetadataUnitOfWork, MetadataUnitOfWorkFactory

METADATA_FILE = "metadata.db"
"""File name of the metadata database inside the state directory."""

METADATA_APPLICATION_ID = 0x766B6D64  # ASCII "vkmd": marks a vakr metadata database


@functools.cache
def metadata_family() -> SchemaFamily:
    """Return the metadata schema family with its packaged migrations.

    Args:
        None

    Returns:
        SchemaFamily: Migrations ``0001`` onwards, loaded once per process.

    Raises:
        ValidationError: If a packaged migration file is malformed (a build bug).
    """
    migrations = load_migrations(files("vakr.adapters.sqlite.metadata").joinpath("sql"))
    return SchemaFamily("metadata", METADATA_APPLICATION_ID, migrations)


def metadata_migrations(
    path: Path, *, settings: SqliteSettings = DEFAULT_SETTINGS, clock: Clock | None = None
) -> SqliteMigrationManager:
    """Return the ``MigrationManager`` for a metadata database file.

    Args:
        path: The file; it need not exist yet.
        settings: Connection timeouts.
        clock: Time source for the journal and backup names.

    Returns:
        SqliteMigrationManager: The manager.

    Raises:
        None
    """
    return SqliteMigrationManager(path, metadata_family(), settings=settings, clock=clock)


def _row_factory(conn: sqlite3.Connection) -> None:
    """Make a pooled connection return rows addressable by column name.

    Args:
        conn: A new connection.

    Returns:
        None

    Raises:
        None
    """
    conn.row_factory = sqlite3.Row


class SqliteMetadataStore:
    """An open metadata database shared by every unit of work on it.

    Attributes:
        path: The database file.
    """

    def __init__(
        self,
        path: Path,
        settings: SqliteSettings = DEFAULT_SETTINGS,
        *,
        family: SchemaFamily | None = None,
    ) -> None:
        """Run the startup preflight and prepare the pool and writer gate.

        Args:
            path: A migrated metadata database.
            settings: Connection bounds.
            family: Schema family to require; tests pass a synthetic later
                release (MIG-002). Defaults to ``metadata_family()``.

        Returns:
            None

        Raises:
            SchemaTooNewError: If a newer release wrote the schema (MIG-004).
            MigrationError: If the schema is missing, older, interrupted, or mismatched.
            StorageError: If the file cannot be read.
        """
        chosen = family if family is not None else metadata_family()
        SqliteMigrationManager(path, chosen, settings=settings).require_current()
        self.path = path
        self._pool = ConnectionPool(path, settings, prepare=_row_factory)
        self._gate = WriterGate(settings)

    def unit_of_work(self) -> SqliteUnitOfWork:
        """Create a unit of work; enter it with ``with``.

        Args:
            None

        Returns:
            SqliteUnitOfWork: A new unit of work.

        Raises:
            None
        """
        return SqliteUnitOfWork(self._pool, self._gate)

    def close(self) -> None:
        """Close idle connections; units of work still open close theirs on exit.

        Args:
            None

        Returns:
            None

        Raises:
            None
        """
        self._pool.close()


def sqlite_uow_factory(store: SqliteMetadataStore) -> MetadataUnitOfWorkFactory:
    """Return a factory of units of work on ``store``.

    Args:
        store: An open store.

    Returns:
        MetadataUnitOfWorkFactory: Each call creates a new unit of work.

    Raises:
        None
    """

    def open_uow() -> MetadataUnitOfWork:
        """Create a unit of work on the store.

        Args:
            None

        Returns:
            MetadataUnitOfWork: A new unit of work.

        Raises:
            None
        """
        return store.unit_of_work()

    return open_uow
