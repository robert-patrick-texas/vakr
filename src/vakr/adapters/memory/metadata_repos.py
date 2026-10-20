"""In-memory metadata repositories over a ``Transaction`` (DB-001..003, META-008/009).

``MemoryTable`` implements the five shared operations for any table from
its ``TableSpec``; devices add admin-state changes. Interfaces and audit
live in ``vakr.adapters.memory.metadata_interfaces``. Checks run in the order the port
documents: existence, ownership, version, fixed fields, then natural key and
references.

Dependencies:
    vakr.adapters.memory.metadata_store, metadata_specs, paging.
    vakr.domain: Records, errors, and checks.

Safety:
    Every stored record is a frozen copy; callers cannot mutate storage.

Example:
    >>> from vakr.adapters.memory.metadata_specs import TAG
    >>> from vakr.adapters.memory.metadata_store import MemoryMetadataStore, Transaction
    >>> MemoryTable(Transaction(MemoryMetadataStore()), TAG).list_page(PageRequest()).total
    0
"""

from dataclasses import replace
from datetime import datetime
from typing import Any
from uuid import UUID

from vakr.adapters.memory.metadata_specs import TableSpec
from vakr.adapters.memory.metadata_store import Transaction
from vakr.adapters.memory.paging import paginate
from vakr.domain.checks import require_unchanged
from vakr.domain.enums import AdminState
from vakr.domain.errors import (
    ConflictError,
    DuplicateError,
    NotFoundError,
    OwnershipError,
    ValidationError,
)
from vakr.domain.ids import DeviceId
from vakr.domain.inventory import DEVICE_FIXED_FIELDS, Device, ManagedBy
from vakr.domain.paging import Page, PageRequest
from vakr.ports.metadata.filters import DeviceFilter


class MemoryTable[E, I: UUID, F]:
    """Get, list, add, and update for one table, as ``NamedRepository`` documents."""

    def __init__(self, tx: Transaction, spec: TableSpec) -> None:
        """Bind the table to a transaction.

        Args:
            tx: The unit of work's transaction.
            spec: The table's rules.

        Returns:
            None

        Raises:
            None
        """
        self._tx = tx
        self._spec = spec

    def get(self, entity_id: I) -> E | None:
        """Return a record by ID.

        Args:
            entity_id: Record ID.

        Returns:
            E | None: The record, or ``None``.

        Raises:
            None
        """
        return self._tx.get(self._spec.table, entity_id)

    def get_by_name(self, name: str) -> E | None:
        """Return a record by exact name.

        Args:
            name: Natural key.

        Returns:
            E | None: The record, or ``None``.

        Raises:
            None
        """
        return next((r for r in self._tx.records(self._spec.table) if r.name == name), None)

    def list_page(self, page: PageRequest, where: F | None = None) -> Page[E]:
        """Return one page of matching records.

        Args:
            page: Limit and cursor.
            where: Filter, or ``None``.

        Returns:
            Page[E]: The page, with ``total``.

        Raises:
            ValidationError: If the cursor is invalid.
        """
        matching = [r for r in self._tx.records(self._spec.table) if self._spec.matches(r, where)]
        return paginate(matching, self._spec.sort_key, page)

    def add(self, entity: E) -> E:
        """Stage a new record.

        Args:
            entity: The record, ``version == 1``.

        Returns:
            E: The record.

        Raises:
            DuplicateError: If the ID or name is taken.
            NotFoundError: If a reference is missing.
            ValidationError: If ``version`` is not 1.
        """
        record: Any = entity
        if record.version != 1:
            raise ValidationError("a new record must have version 1", field="version")
        if self._tx.get(self._spec.table, record.id) is not None:
            raise DuplicateError(self._spec.table, "id", record.id)
        self._check_links(record)
        self._tx.put(self._spec.table, record, None)
        return entity

    def update(self, entity: E, *, writer: ManagedBy) -> E:
        """Stage a changed record after ownership and version checks.

        Args:
            entity: New content based on ``entity.version``.
            writer: The source making the change.

        Returns:
            E: The stored record.

        Raises:
            NotFoundError: If the record or a reference is missing.
            OwnershipError: If ``writer`` does not own the record.
            ConflictError: If the version is stale.
            ValidationError: If a fixed field changed.
            DuplicateError: If the new name is taken.
        """
        record: Any = entity
        return self._replace(self._stored(record.id), record, writer, self._spec.fixed)

    def _stored(self, key: UUID) -> Any:
        """Return the current record or raise.

        Args:
            key: Record ID.

        Returns:
            Any: The record.

        Raises:
            NotFoundError: If it does not exist.
        """
        stored = self._tx.get(self._spec.table, key)
        if stored is None:
            raise NotFoundError(self._spec.table, key)
        return stored

    def _replace(
        self, stored: Any, record: Any, writer: ManagedBy | None, fixed: tuple[str, ...]
    ) -> Any:
        """Check and stage a replacement of ``stored`` by ``record``.

        Args:
            stored: Current record.
            record: New content; ``version`` is the version it was based on.
            writer: Owner check to apply, or ``None`` for discovery writes.
            fixed: Fields that must not change.

        Returns:
            Any: The staged record with its new version.

        Raises:
            OwnershipError, ConflictError, ValidationError, DuplicateError,
            NotFoundError: As documented on ``update``.
        """
        table = self._spec.table
        if writer is not None and not stored.managed_by.permits(writer):
            raise OwnershipError(table, stored.id, owner=str(stored.managed_by), writer=str(writer))
        if record.version != stored.version:
            raise ConflictError(table, stored.id, expected_version=record.version,
                                actual_version=stored.version)  # fmt: skip
        require_unchanged(stored, record, fixed)
        self._check_links(record)
        new = replace(record, version=stored.version + 1)
        self._tx.put(table, new, stored.version)
        return new

    def _check_links(self, record: Any) -> None:
        """Check the natural key is free and every reference exists.

        Args:
            record: The record to stage.

        Returns:
            None

        Raises:
            DuplicateError: If another record has the same name.
            NotFoundError: If a referenced record is missing.
        """
        if self._spec.named:
            other: Any = self.get_by_name(record.name)
            if other is not None and other.id != record.id:
                raise DuplicateError(self._spec.table, "name", record.name)
        for table, key in self._spec.references(record):
            if self._tx.get(table, key) is None:
                raise NotFoundError(table, key)


class MemoryDeviceRepository(MemoryTable[Device, DeviceId, DeviceFilter]):
    """Devices, with admin-state changes."""

    def set_admin_state(
        self, device_id: DeviceId, state: AdminState, *, expected_version: int, writer: ManagedBy,
        at: datetime,
    ) -> Device:  # fmt: skip
        """Change admin state; see ``DeviceRepository.set_admin_state``.

        Args:
            device_id: The device.
            state: New state.
            expected_version: Version the caller read.
            writer: The source making the change.
            at: Change time (UTC).

        Returns:
            Device: The stored device.

        Raises:
            NotFoundError, OwnershipError, ConflictError: As the port documents.
        """
        stored = self._stored(device_id)
        changed = replace(stored, admin_state=state, updated_at=at, version=expected_version)
        return self._replace(stored, changed, writer, DEVICE_FIXED_FIELDS)
