"""MVP-ACC-02 graph coverage: does each monitored interface have data at every graph range?

The 1 h, 24 h, 7 d, and 30 d graphs draw native samples and the 400 d
graph draws 15-minute rollups (E.28 ``auto`` source). The ranges end now
and nest, so a family (traffic, packets, errors) has data at a native range
exactly when its newest native value is inside that range. ``range_states``
finds the newest value of each family from the last hour of samples, then
the newest rollup buckets (a rollup bucket summarises native samples still
held for 30 days), and judges each range and family:

- ``data``: the graph holds at least one value;
- ``gap``: the interface has the family, just not in this range, so the
  graph correctly shows a gap there (``vakr acceptance gaps`` explains it);
- ``pending``: 400 d only, the interface's data is newer than the rollup
  frontier, or the interface is less than an hour old;
- ``missing``: no value of the family anywhere: the graph is empty, a failure.

Dependencies:
    vakr.ports.telemetry.acceptance: Sample and bucket marks.
    vakr.query.acceptance_limits, acceptance_subjects.

Safety:
    Read-only: one hour of samples and the newest buckets per interface.

Example:
    >>> judge_range(10, 3_600, {"traffic": 9}, {}, None, "traffic")
    'data'
"""

from collections.abc import Mapping
from dataclasses import dataclass

from vakr.domain.enums import ObservationType
from vakr.domain.ids import CollectorId, InterfaceId
from vakr.domain.series import TimeWindow
from vakr.ports.telemetry.acceptance import MAX_MARKS, AcceptanceQuery
from vakr.query.acceptance_limits import (
    BUCKET_US,
    COVERAGE_RANGES,
    FAMILIES,
    NATIVE_RANGE_MAX,
    RECENT_BUCKETS,
    US,
)
from vakr.query.acceptance_subjects import as_time

_IFACE = ObservationType.INTERFACE
_HOUR_US = 3_600 * US
MID_SAMPLES = 2_000
"""Native samples read between the rollup frontier and the last hour (about 33 h at 60 s)."""


@dataclass(frozen=True, slots=True)
class Newest:
    """The newest value of each family an interface has, natively and in rollups.

    Attributes:
        native: Family to the newest native evidence time (µs).
        rollup: Family to the newest rollup bucket start (µs).
        truncated: Whether a read was cut short.
    """

    native: dict[str, int]
    rollup: dict[str, int]
    truncated: bool


def _note(found: dict[str, int], available: int, at_us: int) -> None:
    """Record the families present in one mark, keeping the newest time.

    Args:
        found: Family to newest time; updated in place.
        available: The mark's presence bits.
        at_us: The mark's time.

    Returns:
        None

    Raises:
        None
    """
    for family, bits in FAMILIES:
        if available & bits and found.get(family, -1) < at_us:
            found[family] = at_us


def newest(
    query: AcceptanceQuery, iface: InterfaceId, collector: CollectorId, now_us: int,
    frontier_us: int | None, *, rollups: bool,
) -> Newest:  # fmt: skip
    """Find the newest native and rollup value of each family of one interface.

    Args:
        query: The acceptance port.
        iface: The interface.
        collector: The collector.
        now_us: The report time.
        frontier_us: The rollup frontier, or ``None``.
        rollups: Whether the backend has rollups.

    Returns:
        Newest: Newest times per family.

    Raises:
        StorageError: If the store is unavailable.
    """
    native: dict[str, int] = {}
    rollup: dict[str, int] = {}
    hour = query.sample_marks(_IFACE, iface, collector,
                              TimeWindow(as_time(now_us - _HOUR_US), as_time(now_us)))  # fmt: skip
    for m in hour.items:
        _note(native, m.available, m.ts_us)
    truncated = hour.truncated
    behind = frontier_us is not None and frontier_us < now_us - _HOUR_US
    if behind and len(native) < len(FAMILIES):
        # Native samples the rollup job has not reached yet, older than the hour.
        mid = query.sample_marks(_IFACE, iface, collector,
                                 TimeWindow(as_time(frontier_us), as_time(now_us - _HOUR_US)),
                                 limit=MID_SAMPLES, newest_first=True)  # fmt: skip
        for m in mid.items:
            _note(native, m.available, m.ts_us)
    if not rollups:
        return Newest(native, rollup, truncated)
    oldest = now_us - int(COVERAGE_RANGES[-1][1].total_seconds()) * US
    window = TimeWindow(as_time(oldest), as_time(now_us))
    native_from = now_us - int(NATIVE_RANGE_MAX.total_seconds()) * US
    for limit in (RECENT_BUCKETS, MAX_MARKS):
        marks = query.bucket_marks(_IFACE, iface, collector, window, limit=limit,
                                   newest_first=True)  # fmt: skip
        for b in marks.items:
            _note(rollup, b.available, b.start_us)
            if b.start_us >= native_from:
                _note(native, b.available, b.start_us + BUCKET_US - 1)
        if len(rollup) == len(FAMILIES) or not marks.truncated:
            truncated = truncated or (limit == MAX_MARKS and marks.truncated)
            break
    return Newest(native, rollup, truncated)


def judge_range(
    now_us: int, range_s: int, native: Mapping[str, int], rollup: Mapping[str, int],
    frontier_us: int | None, family: str,
) -> str:  # fmt: skip
    """Judge one range and family of one interface (module docstring states).

    Args:
        now_us: The report time.
        range_s: The range in seconds.
        native: Family to newest native time.
        rollup: Family to newest rollup bucket start.
        frontier_us: The rollup frontier, or ``None``.
        family: The family.

    Returns:
        str: ``data``, ``gap``, ``pending``, or ``missing``.

    Raises:
        None
    """
    start = now_us - range_s * US
    if range_s <= NATIVE_RANGE_MAX.total_seconds():
        if native.get(family, -1) >= start:
            return "data"
        return "gap" if family in native or family in rollup else "missing"
    if rollup.get(family, -1) >= start:
        return "data"
    newer = family in native and (frontier_us is None or native[family] >= frontier_us)
    return "pending" if newer else "missing"


def range_states(
    found: Newest, now_us: int, frontier_us: int | None, *, too_new: bool
) -> dict[tuple[str, str], str]:
    """Judge every range and family of one interface.

    Args:
        found: The interface's newest values.
        now_us: The report time.
        frontier_us: The rollup frontier, or ``None``.
        too_new: The interface is less than an hour old: everything not ``data`` is pending.

    Returns:
        dict[tuple[str, str], str]: ``(range label, family)`` to state.

    Raises:
        None
    """
    out: dict[tuple[str, str], str] = {}
    for label, span in COVERAGE_RANGES:
        for family, _bits in FAMILIES:
            state = judge_range(now_us, int(span.total_seconds()), found.native, found.rollup,
                                frontier_us, family)  # fmt: skip
            out[label, family] = "pending" if too_new and state != "data" else state
    return out
