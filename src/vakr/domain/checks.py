"""Small invariant checks shared by every domain dataclass.

Domain records validate themselves in ``__post_init__`` so an invalid device,
interface, or observation cannot exist in memory, in a queue, or in storage.
These helpers keep those checks to one line each and give every failure the
same shape: a ``ValidationError`` naming the field and the rule.

The checks are deliberately strict about types: ``True`` is not a counter,
``0`` is not a missing value, and ``float("nan")`` is not a rate (invariant 1).

Dependencies:
    math, uuid, enum, datetime: Finite-number, UUID, enum, and ordering checks.
    vakr.domain.errors: ``ValidationError``.

Safety:
    Error messages quote field names and simple values only; callers must
    never route credentials through these helpers.

Example:
    >>> require_int(5, "probe_count", minimum=1)
    >>> require_float(None, "in_bps", optional=True)
    >>> require_name(" core1", "name")
    Traceback (most recent call last):
    ...
    vakr.domain.errors.ValidationError: name must not start or end with whitespace
"""

import math
import re
from collections.abc import Iterable
from datetime import datetime
from enum import Enum
from uuid import UUID

from vakr.domain.errors import ValidationError

_CONTROL = re.compile(r"[\x00-\x1f\x7f]")


def require_uuid(value: object, field: str, *, optional: bool = False) -> None:
    """Reject anything that is not a ``uuid.UUID``.

    Args:
        value: Candidate identifier.
        field: Field name for the error message.
        optional: Whether ``None`` is allowed.

    Returns:
        None

    Raises:
        ValidationError: If ``value`` is not a UUID (or ``None`` when optional).
    """
    if value is None and optional:
        return
    if not isinstance(value, UUID):
        raise ValidationError(f"{field} must be a UUID", field=field)


def require_uuid_set(values: object, field: str) -> None:
    """Reject anything that is not a frozenset of UUIDs.

    Args:
        values: Candidate set of identifiers.
        field: Field name for the error message.

    Returns:
        None

    Raises:
        ValidationError: If ``values`` is not a frozenset or holds a non-UUID.
    """
    if not isinstance(values, frozenset) or not all(isinstance(v, UUID) for v in values):
        raise ValidationError(f"{field} must be a frozenset of UUIDs", field=field)


def require_name(value: object, field: str, *, optional: bool = False) -> None:
    """Require a non-empty single-line name without surrounding whitespace.

    Configuration loaders normalise whitespace before building records
    (blueprint 17.6), so a stray space here is a bug, not operator input.

    Args:
        value: Candidate name.
        field: Field name for the error message.
        optional: Whether ``None`` is allowed.

    Returns:
        None

    Raises:
        ValidationError: If ``value`` is not a string, is empty, has leading
            or trailing whitespace, or contains control characters.
    """
    if value is None and optional:
        return
    if not isinstance(value, str) or not value:
        raise ValidationError(f"{field} must be a non-empty string", field=field)
    if value != value.strip():
        raise ValidationError(f"{field} must not start or end with whitespace", field=field)
    if _CONTROL.search(value):
        raise ValidationError(f"{field} must not contain control characters", field=field)


def require_text(value: object, field: str) -> None:
    """Require a string, which may be empty or span several lines.

    Args:
        value: Candidate free-form text, such as an operator comment.
        field: Field name for the error message.

    Returns:
        None

    Raises:
        ValidationError: If ``value`` is not a string.
    """
    if not isinstance(value, str):
        raise ValidationError(f"{field} must be a string", field=field)


def require_int(
    value: object,
    field: str,
    *,
    minimum: int | None = None,
    maximum: int | None = None,
    optional: bool = False,
) -> None:
    """Require an integer within optional bounds; ``bool`` is not an integer here.

    Args:
        value: Candidate integer.
        field: Field name for the error message.
        minimum: Smallest allowed value, inclusive, if any.
        maximum: Largest allowed value, inclusive, if any.
        optional: Whether ``None`` is allowed.

    Returns:
        None

    Raises:
        ValidationError: If ``value`` is not an int or is out of range.
    """
    if value is None and optional:
        return
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValidationError(f"{field} must be an integer", field=field)
    if minimum is not None and value < minimum:
        raise ValidationError(f"{field} must be at least {minimum}, got {value}", field=field)
    if maximum is not None and value > maximum:
        raise ValidationError(f"{field} must be at most {maximum}, got {value}", field=field)


def require_float(value: object, field: str, *, optional: bool = False) -> None:
    """Require a finite, non-negative float; negative zero is refused too.

    Negative zero would encode as ``-0.0`` and break the one-canonical-form
    rule of the envelope, so it is treated as a bug in the producer.

    Args:
        value: Candidate measurement, such as a rate or a percentage.
        field: Field name for the error message.
        optional: Whether ``None`` is allowed.

    Returns:
        None

    Raises:
        ValidationError: If ``value`` is not a float, or is NaN, infinite, or
            negative.
    """
    if value is None and optional:
        return
    if not isinstance(value, float):
        raise ValidationError(f"{field} must be a float", field=field)
    if not math.isfinite(value) or math.copysign(1.0, value) < 0:
        raise ValidationError(f"{field} must be finite and non-negative", field=field)


def require_bool(value: object, field: str) -> None:
    """Require exactly ``True`` or ``False``.

    Args:
        value: Candidate flag.
        field: Field name for the error message.

    Returns:
        None

    Raises:
        ValidationError: If ``value`` is not a bool.
    """
    if not isinstance(value, bool):
        raise ValidationError(f"{field} must be true or false", field=field)


def require_instance(value: object, kind: type, field: str, *, optional: bool = False) -> None:
    """Require an instance of ``kind``, such as a nested domain record.

    Args:
        value: Candidate value.
        kind: Required class.
        field: Field name for the error message.
        optional: Whether ``None`` is allowed.

    Returns:
        None

    Raises:
        ValidationError: If ``value`` is not an instance of ``kind``.
    """
    if value is None and optional:
        return
    if not isinstance(value, kind):
        raise ValidationError(f"{field} must be a {kind.__name__}", field=field)


def require_enum(value: object, kind: type[Enum], field: str, *, optional: bool = False) -> None:
    """Require a member of ``kind``, not merely its string or integer value.

    Args:
        value: Candidate enum member.
        kind: The enum class.
        field: Field name for the error message.
        optional: Whether ``None`` is allowed.

    Returns:
        None

    Raises:
        ValidationError: If ``value`` is not a member of ``kind``.
    """
    if value is None and optional:
        return
    if not isinstance(value, kind):
        raise ValidationError(f"{field} must be a {kind.__name__}", field=field)


def require_order(earlier: datetime | None, later: datetime | None, field: str) -> None:
    """Require ``earlier <= later`` when both times are present.

    Args:
        earlier: The time that must not be later, or ``None``.
        later: The time that must not be earlier, or ``None``.
        field: Field name for the error message (normally the later one).

    Returns:
        None

    Raises:
        ValidationError: If both are present and ``earlier > later``.
    """
    if earlier is not None and later is not None and earlier > later:
        raise ValidationError(
            f"{field} must not be earlier than {earlier.isoformat()}", field=field
        )


def require_unchanged(stored: object, incoming: object, fields: Iterable[str]) -> None:
    """Reject an update that changes fields the writer does not own.

    Adapters call this before persisting, so protected fields (IDs, the
    owning source, discovered attributes) cannot drift through an edit.

    Args:
        stored: The record as currently persisted.
        incoming: The record the caller wants to store.
        fields: Names of fields that must be equal on both.

    Returns:
        None

    Raises:
        ValidationError: Naming every protected field that differs.
    """
    changed = [f for f in fields if getattr(stored, f) != getattr(incoming, f)]
    if changed:
        names = ", ".join(changed)
        raise ValidationError(f"this update may not change {names}", field=changed[0])
