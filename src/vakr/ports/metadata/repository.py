"""Repository protocols for inventory and control records (blueprint 5.2, DB-001..003).

Every repository works inside a ``MetadataUnitOfWork``: changes become
visible to other units of work only on ``commit()`` (rule 4). The shared
behaviour every backend must reproduce, and the contract suites check:

- ``get`` / ``get_by_name`` return ``None`` when nothing matches;
- ``add`` requires ``version == 1`` and raises ``DuplicateError`` for a
  reused ID or natural key and ``NotFoundError`` for a missing referenced
  site, tag, group, or device;
- ``update`` raises, in this order, ``NotFoundError`` (no such record),
  ``OwnershipError`` (``stored.managed_by.permits(writer)`` is false),
  ``ConflictError`` (``record.version != stored.version``), and
  ``ValidationError`` (a fixed field changed); on success it stores the
  record with ``version = stored.version + 1`` and returns what it stored;
- lists are pages in a stable order (below) with ``total`` filled in;
- nothing is ever hard-deleted: retiring is a state change (META-009).

Named records sort by ``(name, id)``; policies by ``(order, name, id)``;
interfaces by ``(device_id, if_name, id)``. Strings compare by Unicode code
point and UUIDs by their canonical text, so PostgreSQL must use
``COLLATE "C"`` for parity.

Dependencies:
    vakr.domain: Records, IDs, enums, and paging types.
    vakr.ports.metadata.base, filters: Generic protocols and list filters.

Safety:
    Repositories return domain records only; driver rows and row IDs never
    escape (rule 2, API-002).

``InterfaceRepository.transfer_owner`` was added with the M3 inventory
loader (port extension, E.26).

Example:
    >>> "get_by_name" in dir(DeviceRepository), "set_lifecycle" in dir(InterfaceRepository)
    (True, True)
"""

from datetime import datetime
from typing import Protocol

from vakr.domain.control import AuditEvent, CollectionPolicy, IdentityHistoryEntry
from vakr.domain.enums import AdminState, InterfaceLifecycle
from vakr.domain.ids import CollectorId, DeviceId, GroupId, InterfaceId, PolicyId, SiteId, TagId
from vakr.domain.interfaces import Interface
from vakr.domain.inventory import Collector, Device, Group, ManagedBy, Site, Tag
from vakr.domain.paging import Page, PageRequest
from vakr.ports.metadata.base import EntityRepository, NamedRepository
from vakr.ports.metadata.filters import AuditFilter, DeviceFilter, InterfaceFilter, NoFilter


class DeviceRepository(NamedRepository[Device, DeviceId, DeviceFilter], Protocol):
    """Devices. Fixed fields: ``id``, ``managed_by``, ``created_at``."""

    def set_admin_state(
        self,
        device_id: DeviceId,
        state: AdminState,
        *,
        expected_version: int,
        writer: ManagedBy,
        at: datetime,
    ) -> Device:
        """Change a device's admin state; ``RETIRED`` is the soft delete (META-009).

        Args:
            device_id: The device.
            state: New admin state.
            expected_version: Version the caller read.
            writer: The source making the change.
            at: Change time (UTC), stored as ``updated_at``.

        Returns:
            Device: The stored device, with ``version`` incremented.

        Raises:
            NotFoundError: If the device does not exist.
            OwnershipError: If ``writer`` does not own the device.
            ConflictError: If ``expected_version`` is stale.
        """
        ...


class SiteRepository(NamedRepository[Site, SiteId, NoFilter], Protocol):
    """Sites. Fixed fields: ``id``, ``managed_by``."""


class TagRepository(NamedRepository[Tag, TagId, NoFilter], Protocol):
    """Tags (the tag half of 5.2's ``TagGroupRepository``)."""


class GroupRepository(NamedRepository[Group, GroupId, NoFilter], Protocol):
    """Groups (the group half of 5.2's ``TagGroupRepository``)."""


class PolicyRepository(NamedRepository[CollectionPolicy, PolicyId, NoFilter], Protocol):
    """Collection policies, listed in evaluation order ``(order, name, id)``."""


class CollectorRepository(NamedRepository[Collector, CollectorId, NoFilter], Protocol):
    """Collectors (polling positions)."""


class InterfaceRepository(EntityRepository[Interface, InterfaceId, InterfaceFilter], Protocol):
    """Interfaces and their identity history.

    ``update`` is the operator path: only ``INTERFACE_OWNED_FIELDS`` may
    differ from the stored record. ``update_observed`` is the discovery path:
    only ``INTERFACE_OBSERVED_FIELDS`` (and ``updated_at``) may differ, and
    no ownership check applies because discovered attributes belong to no
    configuration source. ``set_lifecycle`` is the only way to change
    ``lifecycle`` and ``inactive_since``, so no other write moves the stale
    clock (LIFE-003).
    """

    def update_observed(self, interface: Interface) -> Interface:
        """Store discovered attributes (IFID-001, invariant 6).

        Args:
            interface: The record with new observed attributes; ``version`` is
                the version it was based on.

        Returns:
            Interface: The stored record, with ``version`` incremented.

        Raises:
            NotFoundError: If the interface or a description tag does not exist.
            ConflictError: If the record changed since ``interface.version``.
            ValidationError: If a field outside the observed set changed.
        """
        ...

    def set_lifecycle(
        self,
        interface_id: InterfaceId,
        lifecycle: InterfaceLifecycle,
        *,
        expected_version: int,
        at: datetime,
        inactive_since: datetime | None,
    ) -> Interface:
        """Change an interface's lifecycle; history is always kept (LIFE-002).

        Args:
            interface_id: The interface.
            lifecycle: New lifecycle state.
            expected_version: Version the caller read.
            at: Change time (UTC), stored as ``updated_at``.
            inactive_since: Start of the unbroken INACTIVE/STALE period, as
                ``vakr.logic.lifecycle`` computed it; ``None`` for ACTIVE and RETIRED.

        Returns:
            Interface: The stored interface, with ``version`` incremented.

        Raises:
            NotFoundError: If the interface does not exist.
            ConflictError: If ``expected_version`` is stale.
            ValidationError: If ``inactive_since`` does not fit ``lifecycle``.
        """
        ...

    def transfer_owner(
        self,
        interface_id: InterfaceId,
        owner: ManagedBy,
        *,
        expected_version: int,
        at: datetime,
    ) -> Interface:
        """Hand an interface's operator fields to another source (META-008, E.19).

        Discovered interfaces start API-managed. This is the explicit
        ownership transfer that lets a file annotation govern one (``vakr
        interface claim``), or hands it back; the caller audits it (SEC-018).
        Only ``managed_by`` and ``updated_at`` change.

        Args:
            interface_id: The interface.
            owner: The new owning source.
            expected_version: Version the caller read.
            at: Change time (UTC), stored as ``updated_at``.

        Returns:
            Interface: The stored interface, with ``version`` incremented.

        Raises:
            NotFoundError: If the interface does not exist.
            ConflictError: If ``expected_version`` is stale.
        """
        ...

    def append_identity_history(self, entry: IdentityHistoryEntry) -> None:
        """Record an identity decision (IFID-005).

        Args:
            entry: The decision; its interface must exist on ``entry.device_id``.

        Returns:
            None

        Raises:
            NotFoundError: If the interface does not exist.
            DuplicateError: If the entry ID is already used.
            ValidationError: If the interface belongs to another device.
        """
        ...

    def identity_history(
        self, interface_id: InterfaceId, page: PageRequest
    ) -> Page[IdentityHistoryEntry]:
        """List an interface's identity decisions, oldest first ``(decided_at, id)``.

        Args:
            interface_id: The interface.
            page: Limit and cursor.

        Returns:
            Page[IdentityHistoryEntry]: The entries; empty for unknown interfaces.

        Raises:
            ValidationError: If ``page.cursor`` is invalid.
        """
        ...


class AuditRepository(Protocol):
    """Append-only audit events (SEC-018), listed newest first ``(occurred_at, id)``."""

    def append(self, event: AuditEvent) -> None:
        """Record an event in the current unit of work.

        Args:
            event: The redacted event.

        Returns:
            None

        Raises:
            DuplicateError: If the event ID is already used.
        """
        ...

    def list_page(self, page: PageRequest, where: AuditFilter | None = None) -> Page[AuditEvent]:
        """List events, newest first.

        Args:
            page: Limit and cursor.
            where: Filter, or ``None`` for all events.

        Returns:
            Page[AuditEvent]: The events and the total match count.

        Raises:
            ValidationError: If ``page.cursor`` is invalid.
        """
        ...
