"""SQLite admin-operation repository (API-011, Appendix E.32).

Operations live in ``operation`` (migration ``0004``). The partial unique
index ``operation_one_active`` allows one queued or running operation of a
kind per device, and every update is a compare-and-set on ``version``
inside the store's single write path, so two collectors can never both
claim one operation. Purges are bounded by a ``LIMIT`` subquery.

Dependencies:
    vakr.adapters.sqlite.encoding, errors, metadata.paging, metadata.transaction.
    vakr.domain: The operation record, IDs, and errors.

Safety:
    SQL is fixed text with bound values; ``error`` is stored as given (operator text).

Example:
    >>> SqliteOperationRepository(tx).count_active()  # doctest: +SKIP
    0
"""

from datetime import datetime

from vakr.adapters.sqlite.encoding import encode_time
from vakr.adapters.sqlite.errors import ErrorContext
from vakr.adapters.sqlite.metadata.operation_rows import (
    ACTIVE,
    FINISHED,
    IN_FINISHED,
    INSERT,
    LISTING,
    SELECT,
    UPDATE,
    changing,
    decode,
)
from vakr.adapters.sqlite.metadata.paging import Clause, every, fetch_page
from vakr.adapters.sqlite.metadata.transaction import SqliteTransaction
from vakr.domain.errors import ConflictError, DuplicateError, NotFoundError
from vakr.domain.ids import CollectorId, DeviceId, OperationId
from vakr.domain.operations import Operation, OperationKind
from vakr.domain.paging import Page, PageRequest
from vakr.ports.metadata.operations import OperationFilter


class SqliteOperationRepository:
    """Admin operations in one unit of work."""

    def __init__(self, tx: SqliteTransaction) -> None:
        """Bind to a unit of work's transaction.

        Args:
            tx: The transaction.

        Returns:
            None

        Raises:
            None
        """
        self._tx = tx

    def add(self, operation: Operation) -> None:
        """Insert a new operation.

        Args:
            operation: The record.

        Returns:
            None

        Raises:
            DuplicateError: If the ID is taken or the device has an active one of this kind.
            NotFoundError: If the device does not exist.
            StorageError: If the write fails.
        """
        self._tx.begin_write()
        op = operation
        if self.get(op.id) is not None:
            raise DuplicateError("operation", "id", op.id)
        if not self._tx.query("SELECT 1 FROM device WHERE id = ?", (op.device_id.bytes,)):
            raise NotFoundError("device", op.device_id)
        if op.active and self.active_for_device(op.device_id, op.kind) is not None:
            raise DuplicateError("operation", "active device", op.device_id)
        state, started, finished, collector, *rest = changing(op)
        values = (op.id.bytes, op.kind.value, op.device_id.bytes, state, op.requested_by,
                  encode_time(op.requested_at), started, finished, collector, *rest)  # fmt: skip
        self._tx.execute(INSERT, values, ErrorContext("operation", op.id))

    def get(self, operation_id: OperationId) -> Operation | None:
        """Return one operation.

        Args:
            operation_id: The UUID.

        Returns:
            Operation | None: The record, or ``None``.

        Raises:
            StorageError: If the row cannot be read or decoded.
        """
        rows = self._tx.query(f"{SELECT} WHERE id = ?", (operation_id.bytes,))
        return decode(rows[0]) if rows else None

    def active_for_device(self, device_id: DeviceId, kind: OperationKind) -> Operation | None:
        """Return the device's queued or running operation of one kind.

        Args:
            device_id: The device.
            kind: The kind.

        Returns:
            Operation | None: The operation, or ``None``.

        Raises:
            StorageError: If the row cannot be read or decoded.
        """
        rows = self._tx.query(f"{SELECT} WHERE device_id = ? AND kind = ? AND state IN (?, ?)",
                              (device_id.bytes, kind.value, *ACTIVE))  # fmt: skip
        return decode(rows[0]) if rows else None

    def update(self, operation: Operation) -> Operation:
        """Store a changed operation with a compare-and-set on ``version``.

        Args:
            operation: The new content, one version after the stored one.

        Returns:
            Operation: The stored record.

        Raises:
            NotFoundError: If it does not exist.
            ConflictError: If the stored version moved.
            ValidationError: If the stored operation has finished (the trigger refuses).
            StorageError: If the write fails.
        """
        self._tx.begin_write()
        based_on = operation.version - 1
        context = ErrorContext("operation", operation.id, based_on)
        params = (*changing(operation), operation.id.bytes, based_on)
        if self._tx.execute(UPDATE, params, context) == 1:
            return operation
        stored = self.get(operation.id)
        if stored is None:
            raise NotFoundError("operation", operation.id)
        raise ConflictError("operation", operation.id, expected_version=based_on,
                            actual_version=stored.version)  # fmt: skip

    def queued(self, *, limit: int) -> tuple[Operation, ...]:
        """List queued operations, oldest first.

        Args:
            limit: Most to return.

        Returns:
            tuple[Operation, ...]: The operations.

        Raises:
            StorageError: If the rows cannot be read or decoded.
        """
        rows = self._tx.query(f"{SELECT} WHERE state = 'queued' "
                              "ORDER BY requested_at, id LIMIT ?", (max(limit, 0),))  # fmt: skip
        return tuple(decode(r) for r in rows)

    def running_on(self, collector_id: CollectorId, *, limit: int) -> tuple[Operation, ...]:
        """List operations one collector is running, oldest first.

        Args:
            collector_id: The collector.
            limit: Most to return.

        Returns:
            tuple[Operation, ...]: The operations.

        Raises:
            StorageError: If the rows cannot be read or decoded.
        """
        rows = self._tx.query(f"{SELECT} WHERE state = 'running' AND collector_id = ? "
                              "ORDER BY requested_at, id LIMIT ?",
                              (collector_id.bytes, max(limit, 0)))  # fmt: skip
        return tuple(decode(r) for r in rows)

    def count_active(self) -> int:
        """Return how many operations are queued or running.

        Args:
            None

        Returns:
            int: The count.

        Raises:
            StorageError: If the table cannot be read.
        """
        sql = "SELECT count(*) FROM operation WHERE state IN (?, ?)"
        return int(self._tx.query(sql, ACTIVE)[0][0])

    def list_page(self, page: PageRequest, where: OperationFilter | None = None) -> Page[Operation]:
        """List operations newest first.

        Args:
            page: Limit and cursor.
            where: Filter, or ``None``.

        Returns:
            Page[Operation]: The page, with ``total``.

        Raises:
            ValidationError: If the cursor is invalid.
        """
        w = where or OperationFilter()
        parts: list[Clause] = []
        if w.device_id is not None:
            parts.append(Clause("device_id = ?", (w.device_id.bytes,)))
        if w.states:
            values = tuple(sorted(s.value for s in w.states))
            parts.append(Clause(f"state IN ({', '.join('?' for _ in values)})", values))
        rows, cursor, total = fetch_page(self._tx, LISTING, every(parts), page)
        return Page(tuple(decode(r) for r in rows), cursor, total)

    def purge_finished(self, *, before: datetime, keep: int, limit: int) -> int:
        """Delete finished operations older than ``before`` or beyond the newest ``keep``.

        Args:
            before: Finished rows that ended earlier go.
            keep: Finished rows kept at most.
            limit: Most rows to delete, oldest first.

        Returns:
            int: Operations deleted.

        Raises:
            StorageError: If the write fails.
        """
        self._tx.begin_write()
        # Both sets are prefixes of the finished rows in (finished_at, id) order, so their
        # union is the longer prefix: delete that many of the oldest, at most ``limit``.
        count_sql = f"SELECT count(*) FROM operation WHERE {IN_FINISHED}"  # noqa: S608
        count = int(self._tx.query(count_sql, FINISHED)[0][0])
        old_sql = f"{count_sql} AND finished_at < ?"
        old = int(self._tx.query(old_sql, (*FINISHED, encode_time(before)))[0][0])
        doomed = min(max(count - max(keep, 0), old, 0), max(limit, 0))
        if doomed == 0:
            return 0
        sql = (
            "DELETE FROM operation WHERE id IN (SELECT id FROM operation "  # noqa: S608
            f"WHERE {IN_FINISHED} ORDER BY finished_at, id LIMIT ?)"
        )
        return self._tx.execute(sql, (*FINISHED, doomed))
