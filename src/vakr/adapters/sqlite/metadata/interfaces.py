"""SQLite interface repository with identity history (META-002, META-008, IFID-005, LIFE-002).

Interfaces have four write paths, each allowed to change its own fields
only: operator edits (``update``: owned fields, ownership-checked),
discovery (``update_observed``: observed fields, no owner),
``set_lifecycle``, and the audited ownership transfer (``transfer_owner``).
Identity history is append-only, and the schema's triggers refuse any
update or delete of it. Retiring an interface keeps the
row, its history, and its telemetry.

Dependencies:
    vakr.adapters.sqlite.connection, metadata.paging, metadata.table.
    vakr.domain: Interface and history records, errors, and field groups.

Safety:
    Nothing here deletes. History evidence is stored as a JSON array.

Example:
    >>> from vakr.adapters.sqlite.metadata.specs_interface import INTERFACE
    >>> SqliteInterfaceRepository(tx, INTERFACE).identity_history(iface_id, page)  # doctest: +SKIP
"""

import json
import sqlite3
from dataclasses import replace
from datetime import datetime

from vakr.adapters.sqlite.encoding import blob_to_uuid, decode_time, encode_time, optional_blob
from vakr.adapters.sqlite.errors import ErrorContext
from vakr.adapters.sqlite.metadata.paging import Clause, KeyOrder, Listing, Part, fetch_page
from vakr.adapters.sqlite.metadata.table import SqliteTable
from vakr.domain.control import IdentityHistoryEntry
from vakr.domain.enums import IdentityDecisionSource, InterfaceLifecycle, ReconcileOutcome
from vakr.domain.errors import DuplicateError, StorageError, ValidationError
from vakr.domain.ids import DeviceId, IdentityHistoryId, InterfaceId
from vakr.domain.interfaces import (
    INTERFACE_FIXED_FIELDS,
    INTERFACE_OBSERVED_FIELDS,
    INTERFACE_OWNED_FIELDS,
    Interface,
)
from vakr.domain.inventory import ManagedBy
from vakr.domain.paging import Page, PageRequest
from vakr.ports.metadata.filters import InterfaceFilter

_LIFECYCLE_FIXED = tuple(f for f in INTERFACE_FIXED_FIELDS if f != "lifecycle")
_OWNER_FIXED = (
    *(f for f in INTERFACE_FIXED_FIELDS if f != "managed_by"),
    *INTERFACE_OBSERVED_FIELDS,
    *INTERFACE_OWNED_FIELDS,
)
_HISTORY_COLUMNS = (
    "id", "interface_id", "device_id", "decided_at", "outcome", "source", "previous_if_index",
    "observed_if_index", "score", "runner_up_score", "evidence", "actor",
)  # fmt: skip
_HISTORY = Listing(
    "identity_history",
    ", ".join(_HISTORY_COLUMNS),
    KeyOrder(("decided_at", "id"), (Part.INTEGER, Part.UUID)),
)
_HISTORY_INSERT = (
    f"INSERT INTO identity_history ({_HISTORY.select}) "  # noqa: S608  # constant columns
    f"VALUES ({', '.join('?' for _ in _HISTORY_COLUMNS)})"
)


def _encode_history(e: IdentityHistoryEntry) -> tuple[object, ...]:
    """Encode an identity decision as a row.

    Args:
        e: The entry.

    Returns:
        tuple[object, ...]: Values in ``_HISTORY_COLUMNS`` order.

    Raises:
        None
    """
    return (
        e.id.bytes, e.interface_id.bytes, e.device_id.bytes, encode_time(e.decided_at),
        e.outcome.value, e.source.value, e.previous_if_index, e.observed_if_index, e.score,
        e.runner_up_score, json.dumps(list(e.evidence), ensure_ascii=False), e.actor,
    )  # fmt: skip


def _decode_history(r: sqlite3.Row) -> IdentityHistoryEntry:
    """Decode an identity-history row.

    Args:
        r: The row.

    Returns:
        IdentityHistoryEntry: The entry.

    Raises:
        StorageError: If the row no longer forms a valid entry.
    """
    try:
        return IdentityHistoryEntry(
            id=IdentityHistoryId(blob_to_uuid(r["id"])),
            interface_id=InterfaceId(blob_to_uuid(r["interface_id"])),
            device_id=DeviceId(blob_to_uuid(r["device_id"])),
            decided_at=decode_time(r["decided_at"]),
            outcome=ReconcileOutcome(r["outcome"]), source=IdentityDecisionSource(r["source"]),
            previous_if_index=r["previous_if_index"], observed_if_index=r["observed_if_index"],
            score=r["score"], runner_up_score=r["runner_up_score"],
            evidence=tuple(json.loads(r["evidence"])), actor=r["actor"],
        )  # fmt: skip
    except ValueError, TypeError:
        raise StorageError(
            "a stored identity-history entry is not valid", retryable=False
        ) from None


class SqliteInterfaceRepository(SqliteTable[Interface, InterfaceId, InterfaceFilter]):
    """Interfaces and their identity history."""

    def update(self, entity: Interface, *, writer: ManagedBy) -> Interface:
        """Store operator-owned fields; observed fields must not change.

        Args:
            entity: New content based on ``entity.version``.
            writer: The source making the change.

        Returns:
            Interface: The stored interface.

        Raises:
            NotFoundError: If the interface or a tag is missing.
            OwnershipError: If ``writer`` does not own the interface.
            ConflictError: If the version is stale.
            ValidationError: If a fixed or observed field changed.
        """
        self._tx.begin_write()
        fixed = INTERFACE_FIXED_FIELDS + INTERFACE_OBSERVED_FIELDS
        return self._replace(self._stored(entity.id), entity, writer, fixed)

    def update_observed(self, interface: Interface) -> Interface:
        """Store discovered attributes; no ownership check (IFID-001).

        Args:
            interface: New content based on ``interface.version``.

        Returns:
            Interface: The stored interface.

        Raises:
            NotFoundError: If the interface or a description tag is missing.
            ConflictError: If the version is stale.
            ValidationError: If a field outside the observed set changed.
        """
        self._tx.begin_write()
        fixed = INTERFACE_FIXED_FIELDS + INTERFACE_OWNED_FIELDS
        return self._replace(self._stored(interface.id), interface, None, fixed)

    def set_lifecycle(
        self,
        interface_id: InterfaceId,
        lifecycle: InterfaceLifecycle,
        *,
        expected_version: int,
        at: datetime,
    ) -> Interface:
        """Change lifecycle; history is always kept (LIFE-002).

        Args:
            interface_id: The interface.
            lifecycle: New state.
            expected_version: Version the caller read.
            at: Change time (UTC), stored as ``updated_at``.

        Returns:
            Interface: The stored interface.

        Raises:
            NotFoundError: If the interface does not exist.
            ConflictError: If ``expected_version`` is stale.
        """
        self._tx.begin_write()
        stored = self._stored(interface_id)
        changed = replace(stored, lifecycle=lifecycle, updated_at=at, version=expected_version)
        return self._replace(stored, changed, None, _LIFECYCLE_FIXED)

    def transfer_owner(
        self,
        interface_id: InterfaceId,
        owner: ManagedBy,
        *,
        expected_version: int,
        at: datetime,
    ) -> Interface:
        """Hand the operator fields to another source (META-008, E.19).

        Args:
            interface_id: The interface.
            owner: The new owning source.
            expected_version: Version the caller read.
            at: Change time (UTC), stored as ``updated_at``.

        Returns:
            Interface: The stored interface.

        Raises:
            NotFoundError: If the interface does not exist.
            ConflictError: If ``expected_version`` is stale.
        """
        self._tx.begin_write()
        stored = self._stored(interface_id)
        changed = replace(stored, managed_by=owner, updated_at=at, version=expected_version)
        return self._replace(stored, changed, None, _OWNER_FIXED)

    def append_identity_history(self, entry: IdentityHistoryEntry) -> None:
        """Record an identity decision (IFID-005).

        Args:
            entry: The decision.

        Returns:
            None

        Raises:
            NotFoundError: If the interface does not exist.
            ValidationError: If the interface belongs to another device.
            DuplicateError: If the entry ID is taken.
        """
        self._tx.begin_write()
        interface = self._stored(entry.interface_id)
        if interface.device_id != entry.device_id:
            raise ValidationError(
                "history entry device does not own the interface", field="device_id"
            )
        if self._exists("identity_history", entry.id):
            raise DuplicateError("identity_history", "id", entry.id)
        context = ErrorContext("identity_history", entry.id)
        self._tx.execute(_HISTORY_INSERT, _encode_history(entry), context)

    def identity_history(
        self, interface_id: InterfaceId, page: PageRequest
    ) -> Page[IdentityHistoryEntry]:
        """List an interface's decisions, oldest first ``(decided_at, id)``.

        Args:
            interface_id: The interface.
            page: Limit and cursor.

        Returns:
            Page[IdentityHistoryEntry]: The entries; empty for unknown interfaces.

        Raises:
            ValidationError: If the cursor is invalid.
        """
        where = Clause("interface_id = ?", (optional_blob(interface_id),))
        rows, cursor, total = fetch_page(self._tx, _HISTORY, where, page)
        return Page(tuple(_decode_history(r) for r in rows), cursor, total)
