"""Rows of the SQLite ``operation`` table: columns, statements, and record codecs (E.32).

``vakr.adapters.sqlite.metadata.operations`` reads and writes the table
(migration ``0004``) with these constants and converters, kept apart so the
repository stays within the file-size limit.

Dependencies:
    vakr.adapters.sqlite.encoding, metadata.paging: Value codecs and the listing.
    vakr.domain.operations: The record.

Safety:
    Statements are fixed text; every value is bound by the caller.

Example:
    >>> LISTING.order.columns
    ('requested_at', 'id')
"""

import sqlite3
from datetime import datetime

from vakr.adapters.sqlite.encoding import blob_to_uuid, decode_time, encode_time
from vakr.adapters.sqlite.metadata.paging import KeyOrder, Listing, Part
from vakr.domain.errors import StorageError
from vakr.domain.ids import CollectorId, DeviceId, OperationId
from vakr.domain.operations import (
    FINISHED_STATES,
    Operation,
    OperationKind,
    OperationResult,
    OperationState,
)

COLUMNS = (
    "id", "kind", "device_id", "state", "requested_by", "requested_at", "started_at",
    "finished_at", "collector_id", "interfaces", "new_interfaces", "renumbered", "missing",
    "reviews_opened", "capability_changes", "partial", "duration_ms", "error", "version",
)  # fmt: skip
LISTING = Listing(
    "operation",
    ", ".join(COLUMNS),
    KeyOrder(("requested_at", "id"), (Part.INTEGER, Part.UUID), descending=True),
)
SELECT = f"SELECT {LISTING.select} FROM operation"  # noqa: S608  # constant columns
INSERT = (
    f"INSERT INTO operation ({LISTING.select}) "  # noqa: S608  # constant columns
    f"VALUES ({', '.join('?' for _ in COLUMNS)})"
)
UPDATE = (
    "UPDATE operation SET state = ?, started_at = ?, finished_at = ?, collector_id = ?, "
    "interfaces = ?, new_interfaces = ?, renumbered = ?, missing = ?, reviews_opened = ?, "
    "capability_changes = ?, partial = ?, duration_ms = ?, error = ?, version = ? "
    "WHERE id = ? AND version = ?"
)
ACTIVE = ("queued", "running")
FINISHED = tuple(sorted(s.value for s in FINISHED_STATES))
IN_FINISHED = f"state IN ({', '.join('?' for _ in FINISHED)})"


def _time(value: object) -> datetime | None:
    """Decode an optional stored time.

    Args:
        value: Column value.

    Returns:
        datetime | None: The time, or ``None``.

    Raises:
        ValueError: If it is not a stored time.
    """
    return None if value is None else decode_time(value)


def decode(r: sqlite3.Row) -> Operation:
    """Decode an operation row.

    Args:
        r: The row.

    Returns:
        Operation: The record.

    Raises:
        StorageError: If the row no longer forms a valid record.
    """
    try:
        result = None
        if r["interfaces"] is not None:
            result = OperationResult(r["interfaces"], r["new_interfaces"], r["renumbered"],
                                     r["missing"], r["reviews_opened"], r["capability_changes"],
                                     bool(r["partial"]), r["duration_ms"])  # fmt: skip
        collector = r["collector_id"]
        return Operation(
            id=OperationId(blob_to_uuid(r["id"])),
            kind=OperationKind(r["kind"]),
            device_id=DeviceId(blob_to_uuid(r["device_id"])),
            state=OperationState(r["state"]),
            requested_by=r["requested_by"],
            requested_at=decode_time(r["requested_at"]),
            started_at=_time(r["started_at"]),
            finished_at=_time(r["finished_at"]),
            collector_id=None if collector is None else CollectorId(blob_to_uuid(collector)),
            result=result,
            error=r["error"],
            version=r["version"],
        )
    except TypeError, ValueError:
        raise StorageError("a stored operation is not valid", retryable=False) from None


def changing(op: Operation) -> tuple[object, ...]:
    """Return the values of the columns an update may change, in ``UPDATE`` order.

    Args:
        op: The operation.

    Returns:
        tuple[object, ...]: Values from ``state`` through ``version``.

    Raises:
        None
    """
    res = op.result
    counts: tuple[object, ...] = (None,) * 8
    if res is not None:
        counts = (res.interfaces, res.new, res.renumbered, res.missing, res.reviews_opened,
                  res.capability_changes, int(res.partial), res.duration_ms)  # fmt: skip
    started = None if op.started_at is None else encode_time(op.started_at)
    finished = None if op.finished_at is None else encode_time(op.finished_at)
    collector = None if op.collector_id is None else op.collector_id.bytes
    return (op.state.value, started, finished, collector, *counts, op.error, op.version)
