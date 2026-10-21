"""The leaves of ``from_jsonable``: enums, text, numbers, booleans, times, and UUIDs (E.29 #10).

``vakr.query.from_json`` walks containers and dataclasses; every value at
the bottom of a view comes here. Numbers are kept exactly as sent, so an
``int`` stays an ``int``, and neither ``int`` nor ``float`` accepts a
boolean. A time needs an offset (the API always writes ``Z``).

Dependencies:
    datetime, enum, uuid: Standard library only.

Safety:
    Messages name the path and the expected type, never the value received.

Example:
    >>> decode_scalar(float, 2, "$.value")
    2
"""

from datetime import datetime
from enum import Enum
from typing import Any
from uuid import UUID


class ContractError(ValueError):
    """A JSON value does not have the shape of the view it should be."""


def decode_enum(kind: type[Enum], value: object, path: str) -> Enum:
    """Return the enum member with this value.

    Args:
        kind: The enum.
        value: Parsed JSON.
        path: Where it sits.

    Returns:
        Enum: The member.

    Raises:
        ContractError: If no member has the value.
    """
    if isinstance(value, bool) or not isinstance(value, str | int):
        raise ContractError(f"{path}: expected a {kind.__name__} value, got {type(value).__name__}")
    try:
        return kind(value)
    except ValueError:
        raise ContractError(f"{path}: not a {kind.__name__} value") from None


def decode_scalar(kind: Any, value: object, path: str) -> object:
    """Decode text, a number, a boolean, ``null``, a time, or a UUID.

    Args:
        kind: The scalar type.
        value: Parsed JSON.
        path: Where it sits.

    Returns:
        object: The value; numbers exactly as sent.

    Raises:
        ContractError: If the value does not fit.
        TypeError: If ``kind`` is not a supported scalar.
    """
    number = isinstance(value, int | float) and not isinstance(value, bool)
    checks: dict[Any, bool] = {
        type(None): value is None,
        bool: isinstance(value, bool),
        int: isinstance(value, int) and not isinstance(value, bool),
        float: number,
        str: isinstance(value, str),
    }
    if kind in checks:
        if not checks[kind]:
            name = getattr(kind, "__name__", str(kind))
            raise ContractError(f"{path}: expected {name}, got {type(value).__name__}")
        return value
    if kind is datetime or kind is UUID:
        return _parsed(kind, value, path)
    raise TypeError(f"{path}: {kind!r} is not a type from_jsonable decodes")


def _parsed(kind: type[datetime] | type[UUID], value: object, path: str) -> datetime | UUID:
    """Parse an aware ISO 8601 time or a UUID from text.

    Args:
        kind: ``datetime`` or ``UUID``.
        value: Parsed JSON.
        path: Where it sits.

    Returns:
        datetime | UUID: The value.

    Raises:
        ContractError: If the value is not such text.
    """
    if not isinstance(value, str):
        raise ContractError(f"{path}: expected {kind.__name__} text, got {type(value).__name__}")
    try:
        parsed = datetime.fromisoformat(value) if kind is datetime else UUID(value)
    except ValueError:
        raise ContractError(f"{path}: not a {kind.__name__}") from None
    if isinstance(parsed, datetime) and parsed.tzinfo is None:
        raise ContractError(f"{path}: a time without an offset")
    return parsed
