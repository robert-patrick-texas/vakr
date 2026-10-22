"""In-memory admin-operation repository (API-011, Appendix E.32).

The reference implementation of ``OperationRepository``: versioned rows
staged in the unit of work's transaction. The version check at commit makes
an update a compare-and-set, so of two units of work that claim the same
queued operation only one commits; the other gets ``ConflictError``.

Dependencies:
    vakr.adapters.memory.metadata_store, paging: The transaction and pages.
    vakr.domain: The operation record and errors.

Safety:
    Records are frozen; nothing here logs.

Example:
    >>> from vakr.adapters.memory.metadata_store import MemoryMetadataStore, Transaction
    >>> MemoryOperationRepository(Transaction(MemoryMetadataStore())).count_active()
    0
"""

from datetime import datetime

from vakr.adapters.memory.metadata_store import Transaction
from vakr.adapters.memory.paging import paginate
from vakr.domain.errors import ConflictError, DuplicateError, NotFoundError, ValidationError
from vakr.domain.ids import CollectorId, DeviceId, OperationId
from vakr.domain.operations import (
    FINISHED_STATES,
    Operation,
    OperationKind,
    OperationState,
)
from vakr.domain.paging import Page, PageRequest
from vakr.domain.timestamps import to_epoch_us
from vakr.ports.metadata.operations import OperationFilter

_TABLE = "operation"


def _oldest(op: Operation) -> tuple[datetime, int]:
    """Sort key of the oldest-first lists.

    Args:
        op: An operation.

    Returns:
        tuple[datetime, int]: ``(requested_at, id)``.

    Raises:
        None
    """
    return op.requested_at, op.id.int


class MemoryOperationRepository:
    """Admin operations, by ``(requested_at, id)``."""

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

    def _all(self) -> list[Operation]:
        """Return every operation as this transaction sees it.

        Args:
            None

        Returns:
            list[Operation]: The records.

        Raises:
            None
        """
        return self._tx.records(_TABLE)

    def add(self, operation: Operation) -> None:
        """Stage a new operation.

        Args:
            operation: The record.

        Returns:
            None

        Raises:
            DuplicateError: If the ID is taken or the device has an active one of this kind.
            NotFoundError: If the device does not exist.
        """
        op = operation
        if self.get(op.id) is not None:
            raise DuplicateError("operation", "id", op.id)
        if self._tx.get("device", op.device_id) is None:
            raise NotFoundError("device", op.device_id)
        if op.active and self.active_for_device(op.device_id, op.kind) is not None:
            raise DuplicateError("operation", "active device", op.device_id)
        self._tx.put(_TABLE, op, None)

    def get(self, operation_id: OperationId) -> Operation | None:
        """Return one operation.

        Args:
            operation_id: The UUID.

        Returns:
            Operation | None: The record, or ``None``.

        Raises:
            None
        """
        return self._tx.get(_TABLE, operation_id)

    def active_for_device(self, device_id: DeviceId, kind: OperationKind) -> Operation | None:
        """Return the device's queued or running operation of one kind.

        Args:
            device_id: The device.
            kind: The kind.

        Returns:
            Operation | None: The operation, or ``None``.

        Raises:
            None
        """
        found = (o for o in self._all() if o.device_id == device_id and o.kind is kind)
        return next((o for o in found if o.active), None)

    def update(self, operation: Operation) -> Operation:
        """Stage a changed operation, checking the version now and again at commit.

        Args:
            operation: The new content, one version after the stored one.

        Returns:
            Operation: The staged record.

        Raises:
            NotFoundError: If it does not exist.
            ConflictError: If the stored version moved.
            ValidationError: If the stored operation has finished.
        """
        stored = self.get(operation.id)
        if stored is None:
            raise NotFoundError("operation", operation.id)
        based_on = operation.version - 1
        if stored.version != based_on:
            raise ConflictError("operation", operation.id, expected_version=based_on,
                                actual_version=stored.version)  # fmt: skip
        if stored.state in FINISHED_STATES:
            raise ValidationError("a finished operation never changes", field="state")
        self._tx.put(_TABLE, operation, based_on)
        return operation

    def queued(self, *, limit: int) -> tuple[Operation, ...]:
        """List queued operations, oldest first.

        Args:
            limit: Most to return.

        Returns:
            tuple[Operation, ...]: The operations.

        Raises:
            None
        """
        found = sorted((o for o in self._all() if o.state is OperationState.QUEUED), key=_oldest)
        return tuple(found[: max(limit, 0)])

    def running_on(self, collector_id: CollectorId, *, limit: int) -> tuple[Operation, ...]:
        """List operations one collector is running, oldest first.

        Args:
            collector_id: The collector.
            limit: Most to return.

        Returns:
            tuple[Operation, ...]: The operations.

        Raises:
            None
        """
        found = sorted(
            (o for o in self._all()
             if o.state is OperationState.RUNNING and o.collector_id == collector_id),
            key=_oldest,
        )  # fmt: skip
        return tuple(found[: max(limit, 0)])

    def count_active(self) -> int:
        """Return how many operations are queued or running.

        Args:
            None

        Returns:
            int: The count.

        Raises:
            None
        """
        return sum(1 for o in self._all() if o.active)

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
        found = [
            o for o in self._all()
            if (w.device_id is None or o.device_id == w.device_id)
            and (not w.states or o.state in w.states)
        ]  # fmt: skip
        # Negated keys give descending order through the shared ascending paginator.
        return paginate(found, lambda o: (-to_epoch_us(o.requested_at), -o.id.int), page)

    def purge_finished(self, *, before: datetime, keep: int, limit: int) -> int:
        """Delete finished operations older than ``before`` or beyond the newest ``keep``.

        Args:
            before: Finished rows that ended earlier go.
            keep: Finished rows kept at most.
            limit: Most rows to delete, oldest first.

        Returns:
            int: Operations deleted.

        Raises:
            None
        """
        done = sorted(
            (o for o in self._all() if o.state in FINISHED_STATES),
            key=lambda o: (o.finished_at, o.id.int),
        )
        old = sum(1 for o in done if o.finished_at is not None and o.finished_at < before)
        doomed = min(max(len(done) - max(keep, 0), old, 0), max(limit, 0))
        for op in done[:doomed]:
            self._tx.delete(_TABLE, op.id)
        return doomed
