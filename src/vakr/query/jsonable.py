"""Turn query view models into plain JSON values (TIME-004, API-002, API-006).

The API returns the views of ``vakr.query`` as they are; this converts them
without a serialisation library: dataclasses become objects of their
fields, tuples become arrays, UUIDs become their canonical text, enums
their value, and times ISO 8601 in UTC with a ``Z`` and microseconds only
when present (TIME-004). ``None`` stays ``null``; a missing value never
becomes ``0`` (invariant 1). A non-finite float, which no view should
hold, becomes ``null`` too, so the output is always valid JSON.

Dependencies:
    dataclasses, datetime, enum, math, uuid: The types converted.

Safety:
    Views carry no secrets, so nothing is filtered here; the conversion
    is depth-limited by the views' own shapes (no cycles).

Example:
    >>> from datetime import UTC, datetime
    >>> from vakr.query.history_views import PointView
    >>> to_jsonable(PointView(datetime(2026, 10, 20, 12, 0, tzinfo=UTC), None, flags=("x",)))
    {'t': '2026-10-20T12:00:00Z', 'value': None, 'low': None, 'high': None, 'flags': ['x']}
"""

import dataclasses
import math
from collections.abc import Mapping
from datetime import UTC, datetime
from enum import Enum
from typing import Any
from uuid import UUID

type JsonValue = bool | int | float | str | list[JsonValue] | dict[str, JsonValue] | None


def iso_utc(moment: datetime) -> str:
    """Format an aware time as ISO 8601 UTC with ``Z`` (TIME-004).

    Args:
        moment: Aware time.

    Returns:
        str: Such as ``2026-10-20T12:00:00Z`` or ``...:00.250000Z``.

    Raises:
        None
    """
    spec = "microseconds" if moment.microsecond else "seconds"
    return moment.astimezone(UTC).isoformat(timespec=spec).replace("+00:00", "Z")


def _scalar(value: object) -> JsonValue:
    """Convert a scalar view value.

    Args:
        value: ``None``, a bool, number, string, enum, time, or UUID.

    Returns:
        JsonValue: The JSON value; a non-finite float becomes ``None``.

    Raises:
        TypeError: If ``value`` is not a supported scalar.
    """
    if isinstance(value, Enum):
        value = value.value
    if isinstance(value, datetime):
        value = iso_utc(value)
    elif isinstance(value, UUID):
        value = str(value)
    elif isinstance(value, float) and not math.isfinite(value):
        value = None
    if value is None or isinstance(value, bool | int | float | str):
        return value
    raise TypeError(f"cannot convert {type(value).__name__} to JSON")


def to_jsonable(value: Any) -> JsonValue:  # any view model goes in
    """Convert a view model (or any nesting of supported values) to JSON values.

    Args:
        value: A dataclass, mapping, sequence, scalar, UUID, enum, or time.

    Returns:
        JsonValue: Plain ``dict``/``list``/``str``/number/``None`` values.

    Raises:
        TypeError: If a value of an unsupported type is found.
    """
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return {f.name: to_jsonable(getattr(value, f.name)) for f in dataclasses.fields(value)}
    if isinstance(value, Mapping):
        return {str(k): to_jsonable(v) for k, v in value.items()}
    if isinstance(value, frozenset | set):
        return [to_jsonable(v) for v in sorted(value, key=str)]
    if isinstance(value, list | tuple):
        return [to_jsonable(v) for v in value]
    return _scalar(value)
