"""Pinned integer codes for stored enums, and the unsigned 64-bit mapping (ADR-021, E.21).

Telemetry rows store enum values as small integers, not text, because a
code of 1 costs no bytes at all in a SQLite record and every other small
code costs one. The codes are pinned here, explicitly, and recorded in the
``vocab_code`` table by migration ``0001``, so the file explains itself.
They are append-only like ``Quality`` bit positions: a code is never
renumbered or reused, and a new enum member gets the next free code in a
new migration. Codes are deliberately *not* derived from enum definition
order, which a refactor could change. The most common value of each
vocabulary has code 1.

SQLite integers are signed 64-bit, so 64-bit SNMP counters at or above
2^63 are stored as ``value - 2**64`` and mapped back on read (E.21).

Dependencies:
    vakr.domain: The enums, the rollup metric, and errors.

Safety:
    Decoding an unknown code is damaged or foreign storage: ``StorageError``,
    never the caller's ``ValidationError``.

Example:
    >>> from vakr.domain.enums import IcmpState
    >>> code_of(IcmpState.REACHABLE), member_of(IcmpState, 3)
    (1, <IcmpState.UNREACHABLE: 'unreachable'>)
    >>> to_signed64(2**64 - 1), from_signed64(-1) == 2**64 - 1
    (-1, True)
"""

from enum import Enum
from typing import cast

from vakr.domain.enums import (
    AddressFamily,
    BaselineState,
    CollectorState,
    CounterFamily,
    IcmpState,
    PollExecution,
    ReachabilityStrategy,
    SnmpState,
    SnmpVersion,
    UptimeContinuity,
)
from vakr.domain.errors import StorageError
from vakr.domain.series import Metric

CODES: dict[type[Enum], dict[str, int]] = {
    AddressFamily: {"ipv4": 1, "ipv6": 2},
    BaselineState: {"valid": 1, "no_baseline": 2, "invalidated": 3},
    CollectorState: {"healthy": 1, "degraded": 2, "overloaded": 3, "unavailable": 4},
    CounterFamily: {"hc64": 1, "legacy32": 2},
    IcmpState: {"reachable": 1, "impaired": 2, "unreachable": 3, "unknown": 4},
    PollExecution: {
        "complete": 1,
        "partial": 2,
        "failed": 3,
        "not_attempted": 4,
        "missed_overrun": 5,
        "running": 6,
        "scheduled": 7,
        "cancelled": 8,
    },
    ReachabilityStrategy: {"icmp_gated": 1, "snmp_fallback": 2, "icmp_only": 3, "snmp_only": 4},
    SnmpState: {
        "responding": 1,
        "timeout": 2,
        "error": 3,
        "skipped": 4,
        "partial": 5,
        "unknown": 6,
    },
    SnmpVersion: {"2c": 1, "3": 2},
    UptimeContinuity: {"continuous": 1, "reset_detected": 2, "unknown": 3},
}
"""Enum class to ``{value: code}``; mirrored row for row in ``vocab_code``."""

ROLLUP_SLOTS: dict[str, dict[Metric, int]] = {
    "roll_iface": {
        Metric.IN_BPS: 1,
        Metric.OUT_BPS: 2,
        Metric.IN_PPS: 3,
        Metric.OUT_PPS: 4,
        Metric.IN_ERRORS: 5,
        Metric.OUT_ERRORS: 6,
    },
    "roll_icmp": {Metric.RTT_MEDIAN_US: 1, Metric.LOSS_PCT: 2, Metric.JITTER_US: 3},
}
"""Rollup family to ``{metric: slot}``; bit ``slot - 1`` of a payload marks the metric."""

_DECODE: dict[type[Enum], dict[int, Enum]] = {
    kind: {code: kind(value) for value, code in codes.items()} for kind, codes in CODES.items()
}
_U64 = 1 << 64
_I64_MAX = (1 << 63) - 1


def vocab_rows() -> list[tuple[str, str, int]]:
    """Return every pinned code as ``vocab_code`` rows.

    Args:
        None

    Returns:
        list[tuple[str, str, int]]: ``(vocab, value, code)``, rollup slots
        included as vocabularies ``RollupSlot.<family>``.

    Raises:
        None
    """
    rows = [(k.__name__, v, c) for k, codes in CODES.items() for v, c in codes.items()]
    for family, slots in ROLLUP_SLOTS.items():
        rows.extend((f"RollupSlot.{family}", m.value, s) for m, s in slots.items())
    return sorted(rows)


def code_of(member: Enum) -> int:
    """Return the pinned code of an enum member.

    Args:
        member: A member of an enum listed in ``CODES``.

    Returns:
        int: Its code, 1 or more.

    Raises:
        KeyError: If the enum or member has no pinned code (a missing migration).
    """
    return CODES[type(member)][member.value]


def optional_code(member: Enum | None) -> int | None:
    """Return the pinned code of an optional member.

    Args:
        member: A member, or ``None``.

    Returns:
        int | None: The code, or ``None``.

    Raises:
        KeyError: If the member has no pinned code.
    """
    return None if member is None else code_of(member)


def member_of[E: Enum](kind: type[E], code: object) -> E:
    """Decode a stored code into its enum member.

    Args:
        kind: The enum class.
        code: Column value.

    Returns:
        E: The member.

    Raises:
        StorageError: If the code is not one this release pinned.
    """
    member = _DECODE[kind].get(code) if isinstance(code, int) else None
    if member is None:
        raise StorageError(f"a stored {kind.__name__} code is not known", retryable=False)
    return cast(E, member)


def optional_member[E: Enum](kind: type[E], code: object) -> E | None:
    """Decode an optional stored code.

    Args:
        kind: The enum class.
        code: Column value, or ``None``.

    Returns:
        E | None: The member, or ``None``.

    Raises:
        StorageError: If a non-null code is not known.
    """
    return None if code is None else member_of(kind, code)


def to_signed64(value: int | None) -> int | None:
    """Map an unsigned 64-bit value onto SQLite's signed 64-bit integers.

    Args:
        value: A counter from 0 to 2^64 - 1, or ``None``.

    Returns:
        int | None: ``value`` below 2^63, else ``value - 2**64``.

    Raises:
        None
    """
    return value - _U64 if value is not None and value > _I64_MAX else value


def from_signed64(value: object) -> int | None:
    """Map a stored signed 64-bit integer back to the unsigned counter.

    Args:
        value: Column value, or ``None``.

    Returns:
        int | None: ``value`` if non-negative, else ``value + 2**64``.

    Raises:
        StorageError: If the stored value is not an integer.
    """
    if value is None:
        return None
    if not isinstance(value, int):
        raise StorageError("a stored counter is not an integer", retryable=False)
    return value + _U64 if value < 0 else value
