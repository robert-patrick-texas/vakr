"""Text and JSON shapes of admin operations for ``vakr inventory`` (API-011, DISC-T03).

``operation_row`` is the ``--json`` shape, the same fields the API answers;
``operation_lines`` is the table of ``vakr inventory operations``; the words
come from ``vakr.query.operation_text``.

Dependencies:
    vakr.domain.operations, vakr.query: The record, JSON times, and the words.

Safety:
    Pure; operations hold no secret.

Example:
    >>> operation_lines((), {})
    ['no operations']
"""

from collections.abc import Mapping
from datetime import UTC, datetime

from vakr.domain.ids import DeviceId
from vakr.domain.operations import Operation
from vakr.query.jsonable import iso_utc
from vakr.query.operation_text import outcome_words


def _utc(moment: datetime | None) -> str:
    """Format a time for the table.

    Args:
        moment: Aware time, or ``None``.

    Returns:
        str: Such as ``2026-10-21 14:03:07``, or ``-``.

    Raises:
        None
    """
    return "-" if moment is None else f"{moment.astimezone(UTC):%Y-%m-%d %H:%M:%S}"


def operation_row(op: Operation, device_name: str | None) -> dict[str, object]:
    """Return the ``--json`` shape of one operation (the API's fields).

    Args:
        op: The operation.
        device_name: The device's name, if known.

    Returns:
        dict[str, object]: JSON-ready values.

    Raises:
        None
    """
    res = op.result
    return {
        "id": str(op.id), "kind": op.kind.value, "device_id": str(op.device_id),
        "device_name": device_name, "state": op.state.value, "requested_by": op.requested_by,
        "requested_at": iso_utc(op.requested_at),
        "started_at": None if op.started_at is None else iso_utc(op.started_at),
        "finished_at": None if op.finished_at is None else iso_utc(op.finished_at),
        "collector_id": None if op.collector_id is None else str(op.collector_id),
        "result": None if res is None else {
            "interfaces": res.interfaces, "new": res.new, "renumbered": res.renumbered,
            "missing": res.missing, "reviews_opened": res.reviews_opened,
            "capability_changes": res.capability_changes, "partial": res.partial,
            "duration_ms": res.duration_ms,
        },
        "error": op.error,
    }  # fmt: skip


def operation_lines(ops: tuple[Operation, ...], names: Mapping[DeviceId, str]) -> list[str]:
    """Return the operations table, newest first.

    Args:
        ops: The operations.
        names: Device names by ID.

    Returns:
        list[str]: A header and one line per operation, or a single note.

    Raises:
        None
    """
    if not ops:
        return ["no operations"]
    width = max(len(names.get(o.device_id, "?")) for o in ops)
    width = max(width, len("DEVICE"))
    lines = [f"{'REQUESTED (UTC)':<19}  {'DEVICE':<{width}}  {'BY':<20}  OUTCOME"]
    for op in ops:
        name = names.get(op.device_id, str(op.device_id))
        lines.append(f"{_utc(op.requested_at):<19}  {name:<{width}}  "
                     f"{op.requested_by[:20]:<20}  {outcome_words(op)}  ({op.id})")  # fmt: skip
    return lines
