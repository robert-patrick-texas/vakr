"""In-memory ``MetadataUnitOfWork`` (5.2 rule 4, DB-002).

Each unit of work owns one ``Transaction`` over a shared
``MemoryMetadataStore``. Leaving the ``with`` block without ``commit()``
discards the transaction, so a failed operation commits nothing.

Dependencies:
    vakr.adapters.memory.metadata_store, metadata_repos, metadata_interfaces,
    metadata_reviews, metadata_specs, metadata_users, metadata_failures,
    metadata_sessions, metadata_kiosk, metadata_dashboards: Storage, repositories,
    and table rules.

Safety:
    Uncommitted changes are visible only to their own unit of work.

Example:
    >>> uow = MemoryUnitOfWork(MemoryMetadataStore())
    >>> with uow:
    ...     uow.commit()
"""

from types import TracebackType

from vakr.adapters.memory import metadata_specs as specs
from vakr.adapters.memory.metadata_dashboards import MemoryDashboardRepository
from vakr.adapters.memory.metadata_failures import MemoryLoginFailureRepository
from vakr.adapters.memory.metadata_interfaces import (
    MemoryAuditRepository,
    MemoryInterfaceRepository,
)
from vakr.adapters.memory.metadata_kiosk import MemoryKioskTokenRepository
from vakr.adapters.memory.metadata_repos import MemoryDeviceRepository, MemoryTable
from vakr.adapters.memory.metadata_reviews import MemoryReviewRepository, change_counter
from vakr.adapters.memory.metadata_sessions import MemorySessionRepository
from vakr.adapters.memory.metadata_store import MemoryMetadataStore, Transaction
from vakr.adapters.memory.metadata_users import MemoryUserRepository
from vakr.domain.catalog import Collector, Group, Tag
from vakr.domain.control import CollectionPolicy
from vakr.domain.ids import CollectorId, GroupId, PolicyId, SiteId, TagId
from vakr.domain.inventory import Site
from vakr.ports.metadata.filters import NoFilter


class MemoryUnitOfWork:
    """A unit of work over a ``MemoryMetadataStore``.

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

    def __init__(self, store: MemoryMetadataStore) -> None:
        """Open a unit of work on ``store``.

        Args:
            store: The shared committed state.

        Returns:
            None

        Raises:
            None
        """
        self._tx = Transaction(store)
        self.devices = MemoryDeviceRepository(self._tx, specs.DEVICE)
        self.interfaces = MemoryInterfaceRepository(self._tx, specs.INTERFACE)
        self.sites: MemoryTable[Site, SiteId, NoFilter] = MemoryTable(self._tx, specs.SITE)
        self.tags: MemoryTable[Tag, TagId, NoFilter] = MemoryTable(self._tx, specs.TAG)
        self.groups: MemoryTable[Group, GroupId, NoFilter] = MemoryTable(self._tx, specs.GROUP)
        self.policies: MemoryTable[CollectionPolicy, PolicyId, NoFilter] = MemoryTable(
            self._tx, specs.POLICY
        )
        self.collectors: MemoryTable[Collector, CollectorId, NoFilter] = MemoryTable(
            self._tx, specs.COLLECTOR
        )
        self.audit = MemoryAuditRepository(self._tx)
        self.reviews = MemoryReviewRepository(self._tx)
        self.users = MemoryUserRepository(self._tx)
        self.login_failures = MemoryLoginFailureRepository(self._tx)
        self.sessions = MemorySessionRepository(self._tx)
        self.kiosk_tokens = MemoryKioskTokenRepository(self._tx)
        self.dashboards = MemoryDashboardRepository(self._tx)

    def change_counter(self) -> int:
        """Return the metadata change counter; see ``MetadataUnitOfWork``.

        Args:
            None

        Returns:
            int: One plus ``version`` summed over every counted record.

        Raises:
            None
        """
        return change_counter(self._tx)

    def commit(self) -> None:
        """Apply every staged change atomically.

        Args:
            None

        Returns:
            None

        Raises:
            ConflictError: If a record was changed by another unit of work first.
            DuplicateError: If an ID or name was taken by another unit of work first.
        """
        self._tx.commit()

    def rollback(self) -> None:
        """Discard every staged change.

        Args:
            None

        Returns:
            None

        Raises:
            None
        """
        self._tx.rollback()

    def __enter__(self) -> MemoryUnitOfWork:
        """Begin the unit of work.

        Args:
            None

        Returns:
            MemoryUnitOfWork: This unit of work.

        Raises:
            None
        """
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        """Discard anything not committed.

        Args:
            exc_type: Exception type, if the block raised.
            exc: Exception, if the block raised.
            tb: Traceback, if the block raised.

        Returns:
            None

        Raises:
            None
        """
        self._tx.rollback()
