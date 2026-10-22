"""SQLite ``MetadataUnitOfWork`` (5.2 rule 4, DB-002).

Each unit of work owns one ``SqliteTransaction`` on a connection borrowed
from its store's pool. Leaving the ``with`` block without ``commit()`` rolls
back and returns the connection, so a failed operation commits nothing.

Dependencies:
    vakr.adapters.sqlite.metadata.*: Transaction, specs, and repositories.

Safety:
    Uncommitted changes are visible only inside their own unit of work. Use
    one unit of work from one thread at a time.

Example:
    >>> with SqliteUnitOfWork(pool, gate) as uow:  # doctest: +SKIP
    ...     uow.tags.add(tag)
    ...     uow.commit()
"""

from types import TracebackType

from vakr.adapters.sqlite.metadata import specs
from vakr.adapters.sqlite.metadata.audit import SqliteAuditRepository
from vakr.adapters.sqlite.metadata.dashboards import SqliteDashboardRepository
from vakr.adapters.sqlite.metadata.interfaces import SqliteInterfaceRepository
from vakr.adapters.sqlite.metadata.kiosk import SqliteKioskTokenRepository
from vakr.adapters.sqlite.metadata.login_failures import SqliteLoginFailureRepository
from vakr.adapters.sqlite.metadata.reviews import SqliteReviewRepository, change_counter
from vakr.adapters.sqlite.metadata.sessions import SqliteSessionRepository
from vakr.adapters.sqlite.metadata.specs_interface import INTERFACE
from vakr.adapters.sqlite.metadata.specs_inventory import DEVICE
from vakr.adapters.sqlite.metadata.table import SqliteDeviceRepository, SqliteTable
from vakr.adapters.sqlite.metadata.transaction import SqliteTransaction
from vakr.adapters.sqlite.metadata.users import SqliteUserRepository
from vakr.adapters.sqlite.pool import ConnectionPool
from vakr.adapters.sqlite.writer import WriterGate
from vakr.domain.catalog import Collector, Group, Tag
from vakr.domain.control import CollectionPolicy
from vakr.domain.ids import CollectorId, GroupId, PolicyId, SiteId, TagId
from vakr.domain.inventory import Site
from vakr.ports.metadata.filters import NoFilter


class SqliteUnitOfWork:
    """A unit of work over one SQLite metadata database.

    Attributes:
        devices: Device repository.
        interfaces: Interface and identity-history repository.
        sites: Site repository.
        tags: Tag repository.
        groups: Group repository.
        policies: Collection-policy repository.
        collectors: Collector repository.
        audit: Audit repository.
        reviews: Identity-review repository.
        users: Local-account repository.
        login_failures: Login throttle state.
        sessions: Login sessions.
        kiosk_tokens: Kiosk display tokens.
        dashboards: Custom dashboards.
    """

    def __init__(self, pool: ConnectionPool, gate: WriterGate) -> None:
        """Create the repositories; the connection is borrowed on ``__enter__``.

        Args:
            pool: The store's connection pool.
            gate: The store's single-writer path.

        Returns:
            None

        Raises:
            None
        """
        self._tx = SqliteTransaction(pool, gate)
        self.devices = SqliteDeviceRepository(self._tx, DEVICE)
        self.interfaces = SqliteInterfaceRepository(self._tx, INTERFACE)
        self.sites: SqliteTable[Site, SiteId, NoFilter] = SqliteTable(self._tx, specs.SITE)
        self.tags: SqliteTable[Tag, TagId, NoFilter] = SqliteTable(self._tx, specs.TAG)
        self.groups: SqliteTable[Group, GroupId, NoFilter] = SqliteTable(self._tx, specs.GROUP)
        self.policies: SqliteTable[CollectionPolicy, PolicyId, NoFilter] = SqliteTable(
            self._tx, specs.POLICY
        )
        self.collectors: SqliteTable[Collector, CollectorId, NoFilter] = SqliteTable(
            self._tx, specs.COLLECTOR
        )
        self.audit = SqliteAuditRepository(self._tx)
        self.reviews = SqliteReviewRepository(self._tx)
        self.users = SqliteUserRepository(self._tx)
        self.login_failures = SqliteLoginFailureRepository(self._tx)
        self.sessions = SqliteSessionRepository(self._tx)
        self.kiosk_tokens = SqliteKioskTokenRepository(self._tx)
        self.dashboards = SqliteDashboardRepository(self._tx)

    def change_counter(self) -> int:
        """Return the metadata change counter; see ``MetadataUnitOfWork``.

        Args:
            None

        Returns:
            int: ``1 + version`` summed over every counted record.

        Raises:
            StorageError: If the database cannot be read.
        """
        return change_counter(self._tx)

    def commit(self) -> None:
        """Make every change in this unit of work durable and visible.

        Args:
            None

        Returns:
            None

        Raises:
            StorageError: If the commit fails; nothing is committed then.
        """
        self._tx.commit()

    def rollback(self) -> None:
        """Discard every uncommitted change.

        Args:
            None

        Returns:
            None

        Raises:
            StorageError: If the rollback fails.
        """
        self._tx.rollback()

    def __enter__(self) -> SqliteUnitOfWork:
        """Borrow a connection and begin a deferred transaction.

        Args:
            None

        Returns:
            SqliteUnitOfWork: This unit of work.

        Raises:
            StorageError: If no connection is available in time.
        """
        self._tx.open()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        """Roll back anything not committed and return the connection.

        Args:
            exc_type: Exception type, if the block raised.
            exc: Exception, if the block raised.
            tb: Traceback, if the block raised.

        Returns:
            None

        Raises:
            None
        """
        self._tx.close()
