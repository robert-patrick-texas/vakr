"""Writing metadata records to SQLite through their ``Spec`` (DB-001..003, META-008/009).

``SqliteTable`` adds ``add`` and ``update`` to ``SqliteReader``; devices add
``set_admin_state``. Every write first makes the unit of work the writer
(``begin_write``), so the checks that follow read the newest committed data
under the write lock, and then checks in the order the port documents:
existence, ownership, version, fixed fields, natural key, references. The
``UPDATE`` itself repeats the version check (``WHERE id = ? AND version = ?``)
and a changed-row count other than one is a ``ConflictError``. Membership
rows change by difference, so unchanged links are not rewritten. Nothing
here deletes a record (META-009).

Dependencies:
    vakr.adapters.sqlite.errors, metadata.reader, specs.
    vakr.domain: Records, errors, and checks.

Safety:
    SQL is built from ``Spec`` constants; every value is bound.

Example:
    >>> from vakr.adapters.sqlite.metadata.specs import TAG
    >>> SqliteTable(tx, TAG).add(tag)  # doctest: +SKIP
"""

from dataclasses import replace
from datetime import datetime
from typing import Any
from uuid import UUID

from vakr.adapters.sqlite.errors import ErrorContext
from vakr.adapters.sqlite.metadata.reader import SqliteReader
from vakr.adapters.sqlite.metadata.specs import REF_TABLES, Link
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
from vakr.ports.metadata.filters import DeviceFilter


class SqliteTable[E, I: UUID, F](SqliteReader[E, I, F]):
    """Get, list, add, and update for one record type, as ``NamedRepository`` documents."""

    def add(self, entity: E) -> E:
        """Store a new record.

        Args:
            entity: The record, with ``version == 1``.

        Returns:
            E: The record.

        Raises:
            ValidationError: If ``version`` is not 1.
            DuplicateError: If the ID or name is taken.
            NotFoundError: If a referenced record is missing.
            StorageError: If the write path stays busy or the write fails.
        """
        record: Any = entity
        if record.version != 1:
            raise ValidationError("a new record must have version 1", field="version")
        self._tx.begin_write()
        if self._exists(self._spec.table, record.id):
            raise DuplicateError(self._spec.entity, "id", record.id)
        self._check_links(record)
        values = self._spec.encode(record)
        marks = ", ".join("?" for _ in values)
        columns = ", ".join(self._spec.columns)
        sql = f"INSERT INTO {self._spec.table} ({columns}) VALUES ({marks})"  # noqa: S608
        with self._tx.atomic():
            self._tx.execute(sql, values, self._context(record))
            self._sync_links(record, None)
        return entity

    def update(self, entity: E, *, writer: ManagedBy) -> E:
        """Replace a record after the ownership and version checks.

        Args:
            entity: New content based on ``entity.version``.
            writer: The source making the change.

        Returns:
            E: The stored record, with ``version`` incremented.

        Raises:
            NotFoundError: If the record or a reference is missing.
            OwnershipError: If ``writer`` does not own the record.
            ConflictError: If the version is stale.
            ValidationError: If a fixed field changed.
            DuplicateError: If the new name is taken.
            StorageError: If the write path stays busy or the write fails.
        """
        record: Any = entity
        self._tx.begin_write()
        return self._replace(self._stored(record.id), record, writer, self._spec.fixed)

    def _replace(
        self, stored: Any, record: Any, writer: ManagedBy | None, fixed: tuple[str, ...]
    ) -> Any:
        """Check and write a replacement of ``stored`` by ``record``.

        Args:
            stored: Current record, read under the write lock.
            record: New content; ``version`` is the version it was based on.
            writer: Owner check to apply, or ``None`` for discovery and lifecycle writes.
            fixed: Fields that must not change.

        Returns:
            Any: The stored record with its new version.

        Raises:
            OwnershipError, ConflictError, ValidationError, DuplicateError,
            NotFoundError: As documented on ``update``.
        """
        entity = self._spec.entity
        if writer is not None and not stored.managed_by.permits(writer):
            raise OwnershipError(
                entity, stored.id, owner=str(stored.managed_by), writer=str(writer)
            )
        if record.version != stored.version:
            raise ConflictError(entity, stored.id, expected_version=record.version,
                                actual_version=stored.version)  # fmt: skip
        require_unchanged(stored, record, fixed)
        self._check_links(record)
        new = replace(record, version=stored.version + 1)
        values = self._spec.encode(new)
        sets = ", ".join(f"{column} = ?" for column in self._spec.columns[1:])
        sql = f"UPDATE {self._spec.table} SET {sets} WHERE id = ? AND version = ?"  # noqa: S608
        with self._tx.atomic():
            changed = self._tx.execute(
                sql, (*values[1:], values[0], stored.version), self._context(new)
            )
            if changed == 1:
                self._sync_links(new, stored)
        if changed != 1:
            # Unreachable while the write lock is held; kept as the DB-003 backstop.
            actual = self._stored(stored.id).version
            raise ConflictError(entity, stored.id, expected_version=record.version,
                                actual_version=actual)  # fmt: skip
        return new

    def _check_links(self, record: Any) -> None:
        """Check the natural key is free and every reference exists.

        Args:
            record: The record about to be written.

        Returns:
            None

        Raises:
            DuplicateError: If another record has the same name.
            NotFoundError: If a referenced record is missing.
        """
        if self._spec.named:
            other: Any = self.get_by_name(record.name)
            if other is not None and other.id != record.id:
                raise DuplicateError(self._spec.entity, "name", record.name)
        for entity, key in self._spec.references(record):
            if not self._exists(REF_TABLES[entity], key):
                raise NotFoundError(entity, key)

    def _sync_links(self, new: Any, old: Any | None) -> None:
        """Make the membership tables match ``new``'s set-valued fields.

        Args:
            new: The record just written.
            old: The record it replaced, or ``None`` for an insert.

        Returns:
            None

        Raises:
            StorageError: If a membership write fails.
        """
        context = ErrorContext(self._spec.entity, new.id)
        for link in self._spec.links:
            wanted: frozenset[UUID] = getattr(new, link.attr)
            had: frozenset[UUID] = frozenset() if old is None else getattr(old, link.attr)
            delete, insert = _link_statements(link)
            # A table holding several sets (interface_tag) keys each row by kind too.
            kind = () if link.kind is None else (link.kind,)
            for member in had - wanted:
                self._tx.execute(delete, (new.id.bytes, member.bytes, *kind), context)
            for member in wanted - had:
                self._tx.execute(insert, (new.id.bytes, member.bytes, *kind), context)

    def _context(self, record: Any) -> ErrorContext:
        """Describe a write for translated errors.

        Args:
            record: The record being written.

        Returns:
            ErrorContext: Entity, ID, and version.

        Raises:
            None
        """
        return ErrorContext(self._spec.entity, record.id, record.version)


def _link_statements(link: Link) -> tuple[str, str]:
    """Build the delete and insert statements for one membership table.

    Args:
        link: The membership table.

    Returns:
        tuple[str, str]: ``DELETE`` and ``INSERT`` statements taking
        ``(owner, member)`` and, for a table with ``kind``, the kind last.

    Raises:
        None
    """
    kind_where = "" if link.kind is None else " AND kind = ?"
    kind_column, kind_mark = ("", "") if link.kind is None else (", kind", ", ?")
    # Identifiers come from Link constants; every value is bound.
    delete = f"DELETE FROM {link.table} WHERE {link.owner} = ? AND {link.member} = ?{kind_where}"  # noqa: S608
    insert = (
        f"INSERT INTO {link.table} ({link.owner}, {link.member}{kind_column}) "  # noqa: S608
        f"VALUES (?, ?{kind_mark})"
    )
    return delete, insert


class SqliteDeviceRepository(SqliteTable[Device, DeviceId, DeviceFilter]):
    """Devices, with admin-state changes."""

    def set_admin_state(
        self, device_id: DeviceId, state: AdminState, *, expected_version: int, writer: ManagedBy,
        at: datetime,
    ) -> Device:  # fmt: skip
        """Change admin state; ``RETIRED`` is the soft delete (META-009).

        Args:
            device_id: The device.
            state: New state.
            expected_version: Version the caller read.
            writer: The source making the change.
            at: Change time (UTC), stored as ``updated_at``.

        Returns:
            Device: The stored device.

        Raises:
            NotFoundError: If the device does not exist.
            OwnershipError: If ``writer`` does not own it.
            ConflictError: If ``expected_version`` is stale.
        """
        self._tx.begin_write()
        stored = self._stored(device_id)
        changed = replace(stored, admin_state=state, updated_at=at, version=expected_version)
        return self._replace(stored, changed, writer, DEVICE_FIXED_FIELDS)
