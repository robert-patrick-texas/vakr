"""Judge one collector's schedule tally against MVP-ACC-01 (E.35 thresholds).

Four checks, each ``pass``, ``fail``, or ``incomplete``: the on-time share
of healthy-device polls (at least 99% within ±5 s), same-device overlap
(none, and no slot recorded twice), drift (the daily median start offset
and slot phase each change by at most 100 ms a day, over at least 7 UTC
days), and the workload (every healthy-device poll at the 60 s reference
interval; device and interface counts are shown against the reference).

Dependencies:
    math: Nearest-rank percentiles.
    vakr.query.acceptance_drift, acceptance_limits, acceptance_schedule, acceptance_views.

Safety:
    Pure computation.

Example:
    >>> nearest_rank([3.0, 1.0, 2.0], 0.5)
    2.0
"""

import math
from datetime import UTC, datetime

from vakr.query.acceptance_drift import slope
from vakr.query.acceptance_limits import (
    DAY_US,
    DRIFT_MAX_US_PER_DAY,
    DRIFT_MIN_DAYS,
    DRIFT_MIN_POLLS_PER_DAY,
    ON_TIME_MIN_SHARE,
    ON_TIME_TOLERANCE_US,
    REFERENCE_DEVICES,
    REFERENCE_INTERFACES,
    REFERENCE_INTERVAL_S,
)
from vakr.query.acceptance_schedule import ScheduleTally
from vakr.query.acceptance_views import Check, CollectorSchedule, DayDrift, Verdict, combine


def nearest_rank(values: list[float], q: float) -> float:
    """Return the nearest-rank ``q`` quantile (E.17 #13's rule) of non-empty values.

    Args:
        values: The values.
        q: Quantile in ``(0, 1]``.

    Returns:
        float: The value at rank ``ceil(q * n)``.

    Raises:
        None
    """
    ordered = sorted(values)
    return ordered[max(0, math.ceil(q * len(ordered)) - 1)]


def _ms(us: float | None) -> float | None:
    """Convert microseconds to milliseconds, rounded to 0.001 ms.

    Args:
        us: Microseconds, or ``None``.

    Returns:
        float | None: Milliseconds.

    Raises:
        None
    """
    return None if us is None else round(us / 1_000, 3)


def _on_time(t: ScheduleTally) -> Check:
    """Judge the on-time share.

    Args:
        t: The tally.

    Returns:
        Check: ``on_time``.

    Raises:
        None
    """
    if not t.healthy:
        return Check("on_time", Verdict.INCOMPLETE, "no healthy-device polls in the window")
    share = t.on_time / t.healthy
    verdict = Verdict.PASS if share >= ON_TIME_MIN_SHARE else Verdict.FAIL
    detail = (f"{share:.4%} of {t.healthy} healthy-device polls started within "
              f"±{ON_TIME_TOLERANCE_US // 1_000_000} s ({t.late} late, {t.missed} never "
              f"started); at least {ON_TIME_MIN_SHARE:.0%} required")  # fmt: skip
    return Check("on_time", verdict, detail)


def _overlap(t: ScheduleTally) -> Check:
    """Judge same-device overlap.

    Args:
        t: The tally.

    Returns:
        Check: ``overlap``.

    Raises:
        None
    """
    if not t.records:
        return Check("overlap", Verdict.INCOMPLETE, "no poll records in the window")
    verdict = Verdict.PASS if t.overlaps == 0 and t.duplicates == 0 else Verdict.FAIL
    detail = f"{t.overlaps} overlapping polls and {t.duplicates} slots recorded twice; 0 allowed"
    return Check("overlap", verdict, detail)


def _drift(t: ScheduleTally) -> tuple[Check, tuple[DayDrift, ...], float | None, float | None]:
    """Judge drift from the daily medians of days with enough polls.

    Args:
        t: The tally.

    Returns:
        tuple: The ``drift`` check, the day rows, and both slopes in µs per day.

    Raises:
        None
    """
    rows, offsets, phases = [], [], []
    for day, polls, offset, phase in t.days.days():
        label = datetime.fromtimestamp(day * DAY_US / 1_000_000, UTC).date().isoformat()
        rows.append(DayDrift(label, polls, _ms(offset), _ms(phase)))
        if polls >= DRIFT_MIN_POLLS_PER_DAY:
            offsets.append((day, offset))
            phases.append((day, phase))
    off, pha = slope(offsets), slope(phases)
    limit_ms = DRIFT_MAX_US_PER_DAY // 1_000
    if len(offsets) < DRIFT_MIN_DAYS or off is None or pha is None:
        detail = f"{len(offsets)} UTC days with at least {DRIFT_MIN_POLLS_PER_DAY} polls; " \
                 f"{DRIFT_MIN_DAYS} needed"  # fmt: skip
        return Check("drift", Verdict.INCOMPLETE, detail), tuple(rows), off, pha
    worst = max(abs(off), abs(pha))
    verdict = Verdict.PASS if worst <= DRIFT_MAX_US_PER_DAY else Verdict.FAIL
    detail = (f"start offset {off / 1_000:+.3f} ms/day, slot phase {pha / 1_000:+.3f} ms/day "
              f"over {len(offsets)} days; at most ±{limit_ms} ms/day")  # fmt: skip
    return Check("drift", verdict, detail), tuple(rows), off, pha


def _workload(t: ScheduleTally, interfaces: int | None) -> Check:
    """Judge the reference interval and show the size against the reference workload.

    Args:
        t: The tally.
        interfaces: Interfaces sampled, if known.

    Returns:
        Check: ``workload``.

    Raises:
        None
    """
    size = (f"{t.devices} devices, {'unknown' if interfaces is None else interfaces} "
            f"interfaces (reference {REFERENCE_DEVICES} and {REFERENCE_INTERFACES})")  # fmt: skip
    if not t.healthy:
        return Check("workload", Verdict.INCOMPLETE, f"no healthy-device polls; {size}")
    other = t.offsets_ok[False]
    verdict = Verdict.PASS if other == 0 else Verdict.FAIL
    detail = (f"{other} healthy-device polls ran at an interval other than "
              f"{REFERENCE_INTERVAL_S} s; {size}")  # fmt: skip
    return Check("workload", verdict, detail)


def judge(t: ScheduleTally, collector: str, interfaces: int | None) -> CollectorSchedule:
    """Build one collector's MVP-ACC-01 section.

    Args:
        t: The collector's tally.
        collector: Collector name.
        interfaces: Interfaces sampled, from the newest health report, if any.

    Returns:
        CollectorSchedule: Figures, checks, and verdict.

    Raises:
        None
    """
    drift, days, off, pha = _drift(t)
    checks = (_on_time(t), _overlap(t), drift, _workload(t, interfaces))
    offsets = list(t.days.all_offsets())
    absolute = [abs(v) for v in offsets]
    return CollectorSchedule(
        collector, t.devices, interfaces, t.records, t.healthy, t.on_time, t.late, t.missed,
        dict(sorted(t.excluded.items())), (t.on_time / t.healthy) if t.healthy else None,
        _ms(nearest_rank(offsets, 0.5)) if offsets else None,
        _ms(nearest_rank(absolute, 0.99)) if absolute else None,
        _ms(max(absolute)) if absolute else None,
        t.overlaps, t.duplicates, tuple(t.overlap_rows), t.unrecorded,
        dict(sorted(t.intervals.items())), days,
        None if off is None else round(off / 1_000, 3),
        None if pha is None else round(pha / 1_000, 3),
        checks, combine(c.verdict for c in checks),
    )  # fmt: skip
