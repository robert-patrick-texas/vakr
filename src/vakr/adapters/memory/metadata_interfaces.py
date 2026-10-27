"""In-memory interface and audit repositories (META-002, IFID-005, SEC-018).

Interfaces have three write paths with different field sets: operator edits
(owned fields, ownership-checked), discovery (observed fields, no owner),
and lifecycle changes (state and stale-clock start together, LIFE-003).
Identity history and audit events are append-only.

Dependencies:
    vakr.adapters.memory.metadata_repos: The generic table.
    vakr.domain: Records, errors, and field groups.

Safety:
    Nothing here deletes; retiring an interface keeps its history (LIFE-002).

Example:
    >>> from vakr.adapters.memory.metadata_store import MemoryMetadataStore, Transaction
    >>> from vakr.domain.paging import PageRequest
    >>> MemoryAuditRepository(Transaction(MemoryMetadataStore())).list_page(PageRequest()).total
    0
"""

from dataclasses import replace
from datetime import datetime

from vakr.adapters.memory.metadata_repos import MemoryTable
from vakr.adapters.memory.metadata_store import Transaction
from vakr.adapters.memory.paging import paginate
from vakr.domain.control import AuditEvent, IdentityHistoryEntry
from vakr.domain.enums import InterfaceLifecycle
from vakr.domain.errors import DuplicateError, ValidationError
from vakr.domain.ids import InterfaceId
from vakr.domain.interfaces import (
    INTERFACE_FIXED_FIELDS,
    INTERFACE_LIFECYCLE_FIELDS,
    INTERFACE_OBSERVED_FIELDS,
    INTERFACE_OWNED_FIELDS,
    Interface,
)
from vakr.domain.inventory import ManagedBy
from vakr.domain.paging import Page, PageRequest
from vakr.domain.timestamps import to_epoch_us
from vakr.ports.metadata.filters import AuditFilter, InterfaceFilter

_LIFECYCLE_FIXED = tuple(f for f in INTERFACE_FIXED_FIELDS if f not in INTERFACE_LIFECYCLE_FIELDS)
_OWNER_FIXED = (
    *(f for f in INTERFACE_FIXED_FIELDS if f != "managed_by"),
    *INTERFACE_OBSERVED_FIELDS,
    *INTERFACE_OWNED_FIELDS,
)


class MemoryInterfaceRepository(MemoryTable[Interface, InterfaceId, InterfaceFilter]):
    """Interfaces and their identity history."""

    def update(self, entity: Interface, *, writer: ManagedBy) -> Interface:
        """Store operator-owned fields; see ``InterfaceRepository``.

        Args:
            entity: New content based on ``entity.version``.
            writer: The source making the change.

        Returns:
            Interface: The stored interface.

        Raises:
            NotFoundError, OwnershipError, ConflictError, ValidationError: As the
            port documents; a changed observed field is a ``ValidationError``.
        """
        fixed = INTERFACE_FIXED_FIELDS + INTERFACE_OBSERVED_FIELDS
        return self._replace(self._stored(entity.id), entity, writer, fixed)

    def update_observed(self, interface: Interface) -> Interface:
        """Store discovered attributes; no ownership check.

        Args:
            interface: New content based on ``interface.version``.

        Returns:
            Interface: The stored interface.

        Raises:
            NotFoundError, ConflictError, ValidationError: As the port documents.
        """
        fixed = INTERFACE_FIXED_FIELDS + INTERFACE_OWNED_FIELDS
        return self._replace(self._stored(interface.id), interface, None, fixed)

    def set_lifecycle(
        self,
        interface_id: InterfaceId,
        lifecycle: InterfaceLifecycle,
        *,
        expected_version: int,
        at: datetime,
        inactive_since: datetime | None,
    ) -> Interface:
        """Change lifecycle; see ``InterfaceRepository.set_lifecycle``.

        Args:
            interface_id: The interface.
            lifecycle: New state.
            expected_version: Version the caller read.
            at: Change time (UTC).
            inactive_since: Start of the unbroken INACTIVE/STALE period, or ``None``.

        Returns:
            Interface: The stored interface.

        Raises:
            NotFoundError, ConflictError, ValidationError: As the port documents.
        """
        stored = self._stored(interface_id)
        changed = replace(stored, lifecycle=lifecycle, inactive_since=inactive_since,
                          updated_at=at, version=expected_version)  # fmt: skip
        return self._replace(stored, changed, None, _LIFECYCLE_FIXED)

    def transfer_owner(
        self,
        interface_id: InterfaceId,
        owner: ManagedBy,
        *,
        expected_version: int,
        at: datetime,
    ) -> Interface:
        """Hand the operator fields to another source; see ``InterfaceRepository``.

        Args:
            interface_id: The interface.
            owner: The new owning source.
            expected_version: Version the caller read.
            at: Change time (UTC).

        Returns:
            Interface: The stored interface.

        Raises:
            NotFoundError, ConflictError: As the port documents.
        """
        stored = self._stored(interface_id)
        changed = replace(stored, managed_by=owner, updated_at=at, version=expected_version)
        return self._replace(stored, changed, None, _OWNER_FIXED)

    def append_identity_history(self, entry: IdentityHistoryEntry) -> None:
        """Stage an identity decision.

        Args:
            entry: The decision.

        Returns:
            None

        Raises:
            NotFoundError: If the interface does not exist.
            DuplicateError: If the entry ID is taken.
            ValidationError: If the interface is on another device.
        """
        interface = self._stored(entry.interface_id)
        if interface.device_id != entry.device_id:
            raise ValidationError(
                "history entry device does not own the interface", field="device_id"
            )
        if self._tx.get("identity_history", entry.id) is not None:
            raise DuplicateError("identity_history", "id", entry.id)
        self._tx.put("identity_history", entry, None)

    def identity_history(
        self, interface_id: InterfaceId, page: PageRequest
    ) -> Page[IdentityHistoryEntry]:
        """List an interface's decisions, oldest first.

        Args:
            interface_id: The interface.
            page: Limit and cursor.

        Returns:
            Page[IdentityHistoryEntry]: The entries.

        Raises:
            ValidationError: If the cursor is invalid.
        """
        entries = [
            e for e in self._tx.records("identity_history") if e.interface_id == interface_id
        ]
        return paginate(entries, lambda e: (to_epoch_us(e.decided_at), str(e.id)), page)


class MemoryAuditRepository:
    """Append-only audit events, newest first."""

    def __init__(self, tx: Transaction) -> None:
        """Bind the repository to a transaction.

        Args:
            tx: The unit of work's transaction.

        Returns:
            None

        Raises:
            None
        """
        self._tx = tx

    def append(self, event: AuditEvent) -> None:
        """Stage an event.

        Args:
            event: The redacted event.

        Returns:
            None

        Raises:
            DuplicateError: If the event ID is taken.
        """
        if self._tx.get("audit", event.id) is not None:
            raise DuplicateError("audit", "id", event.id)
        self._tx.put("audit", event, None)

    def list_page(self, page: PageRequest, where: AuditFilter | None = None) -> Page[AuditEvent]:
        """List events newest first, by ``(occurred_at, id)`` descending.

        Args:
            page: Limit and cursor.
            where: Filter, or ``None``.

        Returns:
            Page[AuditEvent]: The page, with ``total``.

        Raises:
            ValidationError: If the cursor is invalid.
        """
        w = where or AuditFilter()
        events = [
            e for e in self._tx.records("audit")
            if (w.object_id is None or e.object_id == w.object_id)
            and (w.actor is None or e.actor == w.actor)
        ]  # fmt: skip
        # Negated keys give descending order through the shared ascending paginator.
        return paginate(events, lambda e: (-to_epoch_us(e.occurred_at), -e.id.int), page)
