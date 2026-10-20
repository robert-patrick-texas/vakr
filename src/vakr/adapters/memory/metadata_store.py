"""Committed metadata state and the per-unit-of-work transaction overlay.

``MemoryMetadataStore`` holds committed records, one dict per table. A
``Transaction`` layers a unit of work's uncommitted changes over it: reads
see the latest committed data plus the unit's own changes (read committed),
and ``commit`` re-checks every change against committed state under a lock
before applying all of them, so a concurrent edit becomes a
``ConflictError`` instead of a lost update (DB-003) and a failed commit
applies nothing (DB-002).

Dependencies:
    threading: The commit lock.
    vakr.domain.errors: Conflict and duplicate errors.

Safety:
    Records are frozen dataclasses, so sharing them between transactions
    cannot leak uncommitted changes.

Example:
    >>> from uuid import uuid7
    >>> Transaction(MemoryMetadataStore()).get("device", uuid7()) is None
    True
"""

import threading
from typing import Any
from uuid import UUID

from vakr.domain.errors import ConflictError, DuplicateError

TABLES = (
    "device", "interface", "site", "tag", "group", "policy", "collector",
    "identity_history", "audit", "review",
)  # fmt: skip
NAMED = frozenset({"device", "site", "tag", "group", "policy", "collector"})


class MemoryMetadataStore:
    """Committed metadata records, shared by every unit of work on this store.

    Attributes:
        lock: Serialises commits.
        tables: Table name to ``{id: record}``.
    """

    def __init__(self) -> None:
        """Create an empty store.

        Args:
            None

        Returns:
            None

        Raises:
            None
        """
        self.lock = threading.Lock()
        self.tables: dict[str, dict[UUID, Any]] = {name: {} for name in TABLES}


class Transaction:
    """Uncommitted changes of one unit of work, layered over the store."""

    def __init__(self, store: MemoryMetadataStore) -> None:
        """Start an empty transaction.

        Args:
            store: The committed state.

        Returns:
            None

        Raises:
            None
        """
        self.store = store
        self.pending: dict[str, dict[UUID, Any]] = {name: {} for name in TABLES}
        self.based_on: dict[tuple[str, UUID], int | None] = {}

    def get(self, table: str, key: UUID) -> Any:
        """Return a record as this transaction sees it.

        Args:
            table: Table name.
            key: Record ID.

        Returns:
            Any: The record, or ``None``.

        Raises:
            None
        """
        if key in self.pending[table]:
            return self.pending[table][key]
        return self.store.tables[table].get(key)

    def records(self, table: str) -> list[Any]:
        """Return every record of a table as this transaction sees it.

        Args:
            table: Table name.

        Returns:
            list[Any]: Committed records overlaid with pending ones.

        Raises:
            None
        """
        merged = dict(self.store.tables[table])
        merged.update(self.pending[table])
        return list(merged.values())

    def put(self, table: str, record: Any, based_on: int | None) -> None:
        """Stage a new (``based_on=None``) or changed record.

        Args:
            table: Table name.
            record: The record to store.
            based_on: Committed version the change was based on.

        Returns:
            None

        Raises:
            None
        """
        slot = (table, record.id)
        if slot not in self.based_on:
            self.based_on[slot] = based_on
        self.pending[table][record.id] = record

    def commit(self) -> None:
        """Apply every staged change atomically, or none.

        Args:
            None

        Returns:
            None

        Raises:
            ConflictError: If a changed record was committed by someone else first.
            DuplicateError: If an ID or name was taken by someone else first.
        """
        try:
            with self.store.lock:
                for (table, key), based_on in self.based_on.items():
                    self._check(table, key, based_on)
                for table, records in self.pending.items():
                    self.store.tables[table].update(records)
        finally:
            # A failed commit leaves nothing staged, like a failed SQL COMMIT.
            self.rollback()

    def rollback(self) -> None:
        """Discard every staged change.

        Args:
            None

        Returns:
            None

        Raises:
            None
        """
        self.pending = {name: {} for name in TABLES}
        self.based_on = {}

    def _check(self, table: str, key: UUID, based_on: int | None) -> None:
        """Re-validate one staged change against committed state.

        Args:
            table: Table name.
            key: Record ID.
            based_on: Committed version the change was based on, or ``None`` if new.

        Returns:
            None

        Raises:
            ConflictError: If the committed version moved.
            DuplicateError: If the record is new but its ID or name is taken.
        """
        committed = self.store.tables[table].get(key)
        if based_on is None and committed is not None:
            raise DuplicateError(table, "id", key)
        if based_on is not None and committed is not None and committed.version != based_on:
            raise ConflictError(
                table, key, expected_version=based_on, actual_version=committed.version
            )
        if table in NAMED:
            name = self.pending[table][key].name
            for other in self.store.tables[table].values():
                if other.name == name and other.id != key and other.id not in self.pending[table]:
                    raise DuplicateError(table, "name", name)
