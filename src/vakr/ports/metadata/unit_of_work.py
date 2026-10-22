"""The metadata unit of work: one transaction over every inventory repository (5.2 rule 4).

Transactions belong to a unit of work, never to a helper function, so an
operation that adds a device, tags it, and writes an audit event either
commits all three or none (DB-002). Usage::

    with uow_factory() as uow:
        uow.devices.add(device)
        uow.audit.append(event)
        uow.commit()

Leaving the ``with`` block without ``commit()`` rolls back, and so does an
exception, which then propagates. After ``commit()`` the unit of work may be
used for further changes and committed again. A ``ConflictError`` or
``DuplicateError`` caused by another unit of work may surface from the
repository call or from ``commit()``; either way nothing from this unit of
work is committed.

Dependencies:
    vakr.ports.metadata.repository, reviews, auth: The repositories it exposes.

Safety:
    Adapters must map driver errors to domain errors before they cross this
    boundary, and must never hold a transaction open outside the block.

``reviews`` and ``change_counter`` were added with the M3 inventory loader
(port extension, E.26); the ``AuthRepositories`` base with M4 local
authentication (E.12); the ``OperationRepositories`` base with M4 admin
operations (E.32); the ``DashboardRepositories`` base with M4 custom
dashboards (E.31).

Example:
    >>> sorted(n for n in dir(MetadataUnitOfWork) if not n.startswith("_"))[:3]
    ['audit', 'change_counter', 'collectors']
"""

from collections.abc import Callable
from types import TracebackType
from typing import Protocol

from vakr.ports.metadata.auth import AuthRepositories
from vakr.ports.metadata.dashboards import DashboardRepositories
from vakr.ports.metadata.operations import OperationRepositories
from vakr.ports.metadata.repository import (
    AuditRepository,
    CollectorRepository,
    DeviceRepository,
    GroupRepository,
    InterfaceRepository,
    PolicyRepository,
    SiteRepository,
    TagRepository,
)
from vakr.ports.metadata.reviews import IdentityReviewRepository


class MetadataUnitOfWork(AuthRepositories, OperationRepositories, DashboardRepositories, Protocol):
    """A transaction spanning all metadata repositories, including authentication's."""

    @property
    def devices(self) -> DeviceRepository:
        """Devices in this unit of work.

        Args:
            None

        Returns:
            DeviceRepository: The repository.

        Raises:
            None
        """
        ...

    @property
    def interfaces(self) -> InterfaceRepository:
        """Interfaces and identity history in this unit of work.

        Args:
            None

        Returns:
            InterfaceRepository: The repository.

        Raises:
            None
        """
        ...

    @property
    def sites(self) -> SiteRepository:
        """Sites in this unit of work.

        Args:
            None

        Returns:
            SiteRepository: The repository.

        Raises:
            None
        """
        ...

    @property
    def tags(self) -> TagRepository:
        """Tags in this unit of work.

        Args:
            None

        Returns:
            TagRepository: The repository.

        Raises:
            None
        """
        ...

    @property
    def groups(self) -> GroupRepository:
        """Groups in this unit of work.

        Args:
            None

        Returns:
            GroupRepository: The repository.

        Raises:
            None
        """
        ...

    @property
    def policies(self) -> PolicyRepository:
        """Collection policies in this unit of work.

        Args:
            None

        Returns:
            PolicyRepository: The repository.

        Raises:
            None
        """
        ...

    @property
    def collectors(self) -> CollectorRepository:
        """Collectors in this unit of work.

        Args:
            None

        Returns:
            CollectorRepository: The repository.

        Raises:
            None
        """
        ...

    @property
    def audit(self) -> AuditRepository:
        """Audit events in this unit of work.

        Args:
            None

        Returns:
            AuditRepository: The repository.

        Raises:
            None
        """
        ...

    @property
    def reviews(self) -> IdentityReviewRepository:
        """Identity reviews in this unit of work (IFID-007).

        Args:
            None

        Returns:
            IdentityReviewRepository: The repository.

        Raises:
            None
        """
        ...

    def change_counter(self) -> int:
        """Return a number that grows whenever collection-relevant metadata changes.

        It covers devices, interfaces, sites, tags, groups, policies, and
        identity reviews: each record contributes one for existing plus its
        ``version``. Records are never deleted and versions only grow, so
        the value is monotonic and identical for every process reading the
        same committed data; a collector compares it between cycles instead
        of reloading inventory (6.2, "metadata change counter").

        Args:
            None

        Returns:
            int: The counter as this unit of work sees the data.

        Raises:
            StorageError: If the database cannot be read.
        """
        ...

    def commit(self) -> None:
        """Make every change in this unit of work durable and visible.

        Args:
            None

        Returns:
            None

        Raises:
            ConflictError: If another unit of work changed a record first.
            DuplicateError: If another unit of work took an ID or name first.
        """
        ...

    def rollback(self) -> None:
        """Discard every uncommitted change.

        Args:
            None

        Returns:
            None

        Raises:
            None
        """
        ...

    def __enter__(self) -> MetadataUnitOfWork:
        """Begin the unit of work.

        Args:
            None

        Returns:
            MetadataUnitOfWork: This unit of work.

        Raises:
            None
        """
        ...

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        """Roll back anything not committed and release resources.

        Args:
            exc_type: Exception type, if the block raised.
            exc: Exception, if the block raised.
            tb: Traceback, if the block raised.

        Returns:
            None

        Raises:
            None
        """
        ...


type MetadataUnitOfWorkFactory = Callable[[], MetadataUnitOfWork]
"""Opens a new unit of work on one metadata store; the contract-suite fixture type."""
