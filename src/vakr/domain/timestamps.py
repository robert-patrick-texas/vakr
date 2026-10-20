"""UTC timestamp rules and the integer-microsecond wire encoding (invariant 11).

Every canonical timestamp in vakr is an aware ``datetime`` in UTC
(TIME-001). A naive time is ambiguous, and a local time with a zero offset
(``Europe/London`` in winter) silently becomes wrong in summer, so both are
rejected rather than guessed at. On the wire and in storage a timestamp is an
integer count of microseconds since 1970-01-01T00:00:00Z: integers sort,
compare, and round-trip exactly in every language, while ``datetime`` itself
has microsecond resolution, so nothing is lost (blueprint 7.4).

Dependencies:
    datetime: Aware UTC datetimes and exact ``timedelta`` arithmetic.
    vakr.domain.errors: ``ValidationError`` for rejected values.

Safety:
    Conversions use integer arithmetic only; no float rounding can move a
    sample by a microsecond and break counter-interval math.

Example:
    >>> from datetime import UTC, datetime
    >>> to_epoch_us(datetime(1970, 1, 1, 0, 0, 1, tzinfo=UTC))
    1000000
    >>> from_epoch_us(1_000_000).isoformat()
    '1970-01-01T00:00:01+00:00'
"""

from datetime import UTC, datetime, timedelta

from vakr.domain.errors import ValidationError

EPOCH = datetime(1970, 1, 1, tzinfo=UTC)
"""The zero point of the microsecond encoding."""

_ONE_US = timedelta(microseconds=1)
_MIN_US = (datetime(1, 1, 1, tzinfo=UTC) - EPOCH) // _ONE_US
_MAX_US = (datetime(9999, 12, 31, 23, 59, 59, 999999, tzinfo=UTC) - EPOCH) // _ONE_US


def is_utc(value: datetime) -> bool:
    """Tell whether ``value`` is an aware datetime in UTC.

    A zero offset alone is not enough: the zone must also call itself UTC,
    so a local zone that happens to sit at +00:00 today is refused.

    Args:
        value: The datetime to inspect.

    Returns:
        bool: ``True`` for ``datetime.UTC``, ``ZoneInfo("UTC")`` and similar.

    Raises:
        None
    """
    return (
        value.tzinfo is not None
        and value.utcoffset() == timedelta(0)
        and value.tzname() in {"UTC", "Etc/UTC"}
    )


def require_utc(value: object, field: str) -> None:
    """Reject anything that is not an aware UTC datetime.

    Args:
        value: Candidate timestamp.
        field: Field name for the error message.

    Returns:
        None

    Raises:
        ValidationError: If ``value`` is not a datetime, is naive, or is in a
            zone other than UTC.
    """
    if not isinstance(value, datetime):
        raise ValidationError(f"{field} must be a datetime", field=field)
    if not is_utc(value):
        raise ValidationError(
            f"{field} must be an aware UTC datetime (invariant 11), got {value.isoformat()}",
            field=field,
        )


def require_optional_utc(value: object, field: str) -> None:
    """Like ``require_utc`` but also accept ``None``.

    Args:
        value: Candidate timestamp or ``None``.
        field: Field name for the error message.

    Returns:
        None

    Raises:
        ValidationError: If ``value`` is neither ``None`` nor an aware UTC datetime.
    """
    if value is not None:
        require_utc(value, field)


def to_epoch_us(value: datetime) -> int:
    """Encode a UTC datetime as integer microseconds since the Unix epoch.

    Args:
        value: Aware UTC datetime.

    Returns:
        int: Microseconds since 1970-01-01T00:00:00Z; negative before it.

    Raises:
        ValidationError: If ``value`` is not an aware UTC datetime.
    """
    require_utc(value, "timestamp")
    return (value - EPOCH) // _ONE_US


def from_epoch_us(value: int) -> datetime:
    """Decode integer microseconds since the Unix epoch into a UTC datetime.

    Args:
        value: Microseconds since 1970-01-01T00:00:00Z.

    Returns:
        datetime: The instant, with ``tzinfo`` set to ``datetime.UTC``.

    Raises:
        ValidationError: If ``value`` is not an integer or lies outside the
            years 1-9999 that ``datetime`` can represent.
    """
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValidationError("timestamp must be integer microseconds since the epoch")
    if not _MIN_US <= value <= _MAX_US:
        raise ValidationError(f"timestamp {value} us is outside the representable range")
    return EPOCH + timedelta(microseconds=value)
