"""Shapes of the operation endpoints: one operation and a page of them (API-011, Appendix E.32).

An operation is the progress and result of an asynchronous admin request:
``queued`` until a collector claims it, ``running`` while it runs, then
``succeeded`` with ``result`` or ``failed``/``expired`` with ``error``.
``device_name`` is the device's current name, for display; the device's
identity is ``device_id``.

Dependencies:
    vakr.api.paging: Paging facts.
    vakr.domain.operations: The record and its result.

Safety:
    No field holds a secret; ``error`` is operator text capped at 500 characters.

Example:
    >>> sorted(OperationPage.__dataclass_fields__)
    ['page', 'rows']
"""

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from vakr.api.paging import PageInfo
from vakr.domain.operations import Operation, OperationResult


@dataclass(frozen=True, slots=True)
class OperationOut:
    """One admin operation (API-011).

    Attributes:
        id: Operation UUID; ``GET /api/v1/operations/{id}`` follows it.
        kind: ``rediscovery``.
        device_id: The device.
        device_name: Its current display name; ``null`` if it cannot be read.
        state: ``queued``, ``running``, ``succeeded``, ``failed``, or ``expired``.
        requested_by: Username, ``local:<account>``, or ``cli:<login>``.
        requested_at: When it was asked for (UTC).
        started_at: When a collector claimed it.
        finished_at: When it ended.
        collector_id: The collector that ran it.
        result: What a succeeded rediscovery found and changed.
        error: Why a failed or expired operation did not succeed.
    """

    id: UUID
    kind: str
    device_id: UUID
    device_name: str | None
    state: str
    requested_by: str
    requested_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    collector_id: UUID | None
    result: OperationResult | None
    error: str | None

    @classmethod
    def of(cls, op: Operation, device_name: str | None) -> OperationOut:
        """Describe a stored operation.

        Args:
            op: The record.
            device_name: The device's name, if known.

        Returns:
            OperationOut: The description.

        Raises:
            None
        """
        return cls(op.id, op.kind.value, op.device_id, device_name, op.state.value,
                   op.requested_by, op.requested_at, op.started_at, op.finished_at,
                   op.collector_id, op.result, op.error)  # fmt: skip


@dataclass(frozen=True, slots=True)
class OperationPage:
    """One page of operations, newest first (API-005).

    Attributes:
        rows: The operations.
        page: Paging facts.
    """

    rows: tuple[OperationOut, ...]
    page: PageInfo
