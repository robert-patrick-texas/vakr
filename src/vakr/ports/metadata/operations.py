"""Repository protocol for admin operations, and the unit-of-work part that exposes it (API-011).

Operations are asynchronous admin requests, such as a manual rediscovery
(``vakr.domain.operations``). Every backend must reproduce, and
``tests/contract/contract_metadata_operations.py`` checks:

- ``add`` raises ``DuplicateError`` for a reused ID and for a second active
  operation of the same kind on one device (one active per device, E.32),
  and ``NotFoundError`` for a device that does not exist;
- ``update`` is a compare-and-set on ``version``: a stale record raises
  ``ConflictError``, from the call or from ``commit()``, so two collectors
  can never both claim one operation; a finished operation never changes;
- ``queued`` lists oldest first, ``list_page`` newest first, both bounded;
- ``purge_finished`` deletes at most ``limit`` finished rows per call.

Operations are an operational log, not inventory: they never move the
metadata change counter.

Dependencies:
    vakr.domain: Operation records, IDs, and paging.

Safety:
    Records carry no secret; ``error`` is operator text capped by the domain.

Example:
    >>> OperationFilter().states
    frozenset()
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol

from vakr.domain.ids import CollectorId, DeviceId, OperationId
from vakr.domain.operations import Operation, OperationKind, OperationState
from vakr.domain.paging import Page, PageRequest


@dataclass(frozen=True, slots=True)
class OperationFilter:
    """Which operations a list returns.

    Attributes:
        device_id: Only this device's, if set.
        states: Only these states; empty means every state.
    """

    device_id: DeviceId | None = None
    states: frozenset[OperationState] = field(default_factory=frozenset)


class OperationRepository(Protocol):
    """Admin operations (API-011), by ``(requested_at, id)``."""

    def add(self, operation: Operation) -> None:
        """Store a new operation.

        Args:
            operation: The operation, normally ``queued``.

        Returns:
            None

        Raises:
            DuplicateError: If the ID is used, or the device already has an
                active operation of this kind.
            NotFoundError: If the device does not exist.
        """
        ...

    def get(self, operation_id: OperationId) -> Operation | None:
        """Return one operation.

        Args:
            operation_id: Its UUID.

        Returns:
            Operation | None: The record, or ``None``.

        Raises:
            None
        """
        ...

    def active_for_device(self, device_id: DeviceId, kind: OperationKind) -> Operation | None:
        """Return the device's queued or running operation of one kind.

        Args:
            device_id: The device.
            kind: The kind.

        Returns:
            Operation | None: The active operation, or ``None``.

        Raises:
            None
        """
        ...

    def update(self, operation: Operation) -> Operation:
        """Store a changed operation if nobody changed it since it was read.

        Args:
            operation: The new content, one version after the stored one.

        Returns:
            Operation: The stored record.

        Raises:
            NotFoundError: If it does not exist.
            ConflictError: If the stored version is not ``operation.version - 1``.
            ValidationError: If the stored operation has already finished.
        """
        ...

    def queued(self, *, limit: int) -> tuple[Operation, ...]:
        """List queued operations, oldest first ``(requested_at, id)``.

        Args:
            limit: Most to return.

        Returns:
            tuple[Operation, ...]: The operations.

        Raises:
            None
        """
        ...

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
        ...

    def count_active(self) -> int:
        """Return how many operations are queued or running.

        Args:
            None

        Returns:
            int: The count.

        Raises:
            None
        """
        ...

    def list_page(self, page: PageRequest, where: OperationFilter | None = None) -> Page[Operation]:
        """List operations newest first, by ``(requested_at, id)`` descending.

        Args:
            page: Limit and cursor.
            where: Filter, or ``None``.

        Returns:
            Page[Operation]: The page, with ``total``.

        Raises:
            ValidationError: If the cursor is invalid.
        """
        ...

    def purge_finished(self, *, before: datetime, keep: int, limit: int) -> int:
        """Delete finished operations that ended before ``before`` or beyond the newest ``keep``.

        Args:
            before: Finished rows that ended earlier go.
            keep: Finished rows kept at most, newest first; older ones go.
            limit: Most rows to delete in this call, oldest first.

        Returns:
            int: Operations deleted.

        Raises:
            None
        """
        ...


class OperationRepositories(Protocol):
    """The operations part of a metadata unit of work (port extension, E.32)."""

    @property
    def operations(self) -> OperationRepository:
        """Admin operations in this unit of work.

        Args:
            None

        Returns:
            OperationRepository: The repository.

        Raises:
            None
        """
        ...
