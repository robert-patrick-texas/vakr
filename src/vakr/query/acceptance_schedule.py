"""MVP-ACC-01 from poll records: on-time starts, same-device overlap, drift, and workload (E.35).

Every scheduled slot leaves a ``DevicePoll`` record (SCHED-012, SCHED-017),
so the scheduler's punctuality is measured from the records themselves:
start offset = ``started_at - scheduled_at``. A slot counts toward the
on-time share when its device was healthy (``unhealthy_reason`` is
``None``); it is on time when it started within ±5 s, and a healthy slot
that never started (not attempted, or skipped because the previous poll
overran) counts against the share. Collector capacity is exactly what the
criterion tests, so collector-side misses are never excluded.

Dependencies:
    vakr.domain: Poll execution states and quality flags.
    vakr.ports.telemetry.acceptance: ``PollMark``.
    vakr.query.acceptance_drift, acceptance_limits, acceptance_views.

Safety:
    Pure computation over marks the caller read.

Example:
    >>> from vakr.domain.quality import Quality
    >>> m = PollMark(0, 60, PollExecution.COMPLETE, Quality.MAINTENANCE, 0, 1)
    >>> unhealthy_reason(m, None)
    'maintenance'
"""

from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from itertools import pairwise

from vakr.domain.enums import PollExecution
from vakr.domain.quality import Quality
from vakr.ports.telemetry.acceptance import PollMark
from vakr.query.acceptance_drift import DayTally, phase_error
from vakr.query.acceptance_limits import (
    GAP_FACTOR,
    ON_TIME_TOLERANCE_US,
    US,
)
from vakr.query.acceptance_views import Overlap

_DEVICE_TROUBLE = (PollExecution.PARTIAL, PollExecution.FAILED)
_DEGRADED, _MAINTENANCE = int(Quality.COLLECTOR_DEGRADED), int(Quality.MAINTENANCE)
_BITS: dict[Quality, int] = {}


def _has(quality: Quality, bit: int) -> bool:
    """Tell whether flags hold a bit, without ``IntFlag`` arithmetic per poll.

    A week holds a million polls with a handful of distinct flag values, and
    ``Flag.__and__`` costs more than the rest of the tally.

    Args:
        quality: The flags.
        bit: One flag as an integer.

    Returns:
        bool: Whether the bit is set.

    Raises:
        None
    """
    value = _BITS.get(quality)
    if value is None:
        value = _BITS.setdefault(quality, int(quality))
    return bool(value & bit)


MAX_OVERLAP_ROWS = 100
"""Overlaps listed per collector; every one is counted."""


def device_trouble(mark: PollMark) -> bool:
    """Tell whether a poll that ran shows the device, not the collector, in trouble.

    Args:
        mark: A poll.

    Returns:
        bool: Partial or failed without ``COLLECTOR_DEGRADED``, or longer than its interval.

    Raises:
        None
    """
    if mark.execution in _DEVICE_TROUBLE and not _has(mark.quality, _DEGRADED):
        return True
    if mark.started_us is None or mark.completed_us is None:
        return False
    return mark.completed_us - mark.started_us > mark.interval_s * US


def unhealthy_reason(mark: PollMark, previous: PollMark | None) -> str | None:
    """Return why a slot's device was not healthy (E.35), or ``None`` when it was.

    Args:
        mark: The slot's record.
        previous: The device's latest earlier poll that started, if any.

    Returns:
        str | None: ``maintenance``, ``device_trouble``, ``previous_poll_trouble``, or ``None``.

    Raises:
        None
    """
    if _has(mark.quality, _MAINTENANCE):
        return "maintenance"
    if mark.started_us is not None and device_trouble(mark):
        return "device_trouble"
    recent = previous is not None and (
        mark.scheduled_us - previous.scheduled_us <= 2 * mark.interval_s * US
    )
    if recent and previous is not None and device_trouble(previous):
        return "previous_poll_trouble"
    return None


@dataclass(slots=True)
class ScheduleTally:
    """Running counts of one collector's slots, device by device.

    Attributes:
        records: Poll records seen.
        healthy: Healthy-device slots.
        on_time: Of those, started within the tolerance.
        late: Started outside it.
        missed: Never started.
        excluded: Unhealthy slots by reason.
        intervals: Records by effective interval.
        offsets_ok: Healthy slots at the reference interval, or not.
        overlaps: Overlapping polls.
        overlap_rows: The first overlaps.
        duplicates: Slots recorded twice.
        unrecorded: Slots with no record between or around records.
        devices: Devices with records.
        days: Per-day offsets and phases.
    """

    records: int = 0
    healthy: int = 0
    on_time: int = 0
    late: int = 0
    missed: int = 0
    excluded: Counter[str] = field(default_factory=Counter)
    intervals: Counter[int] = field(default_factory=Counter)
    offsets_ok: Counter[bool] = field(default_factory=Counter)
    overlaps: int = 0
    overlap_rows: list[Overlap] = field(default_factory=list)
    duplicates: int = 0
    unrecorded: int = 0
    devices: int = 0
    days: DayTally = field(default_factory=DayTally)

    def add_device(
        self, name: str, marks: Sequence[PollMark], window: tuple[int, int], reference_s: int
    ) -> None:
        """Count one device's records, oldest first.

        Args:
            name: Device name, for overlap rows.
            marks: The device's poll marks in the window, by scheduled time.
            window: ``(start_us, end_us)`` of the report.
            reference_s: The reference interval in seconds.

        Returns:
            None

        Raises:
            None
        """
        if not marks:
            return
        self.devices += 1
        self.unrecorded += unrecorded_slots(marks, window)
        previous: PollMark | None = None
        last_slot: int | None = None
        anchor = (marks[0].scheduled_us, marks[0].interval_s)
        for mark in marks:
            self.records += 1
            self.intervals[mark.interval_s] += 1
            if last_slot == mark.scheduled_us:
                self.duplicates += 1
            last_slot = mark.scheduled_us
            if mark.interval_s != anchor[1]:
                anchor = (mark.scheduled_us, mark.interval_s)
            self._overlap(name, previous, mark)
            self._slot(mark, previous, anchor, reference_s)
            if mark.started_us is not None:
                previous = mark

    def _overlap(self, name: str, previous: PollMark | None, mark: PollMark) -> None:
        """Count a poll that started before the device's previous poll finished.

        Args:
            name: Device name.
            previous: The latest earlier poll that started.
            mark: This poll.

        Returns:
            None

        Raises:
            None
        """
        if previous is None or previous.completed_us is None or mark.started_us is None:
            return
        if mark.started_us < previous.completed_us:
            self.overlaps += 1
            if len(self.overlap_rows) < MAX_OVERLAP_ROWS:
                at = datetime.fromtimestamp(mark.scheduled_us / US, UTC)
                ms = (previous.completed_us - mark.started_us) / 1_000
                self.overlap_rows.append(Overlap(name, at, ms))

    def _slot(
        self, mark: PollMark, previous: PollMark | None, anchor: tuple[int, int], reference_s: int
    ) -> None:
        """Count one slot toward the on-time share, or as excluded.

        Args:
            mark: The slot's record.
            previous: The device's latest earlier poll that started.
            anchor: The device's reference slot and interval for phase.
            reference_s: The reference interval.

        Returns:
            None

        Raises:
            None
        """
        reason = unhealthy_reason(mark, previous)
        if reason is not None:
            self.excluded[reason] += 1
            return
        self.healthy += 1
        self.offsets_ok[mark.interval_s == reference_s] += 1
        if mark.started_us is None:
            self.missed += 1
            return
        offset = mark.started_us - mark.scheduled_us
        if abs(offset) <= ON_TIME_TOLERANCE_US:
            self.on_time += 1
        else:
            self.late += 1
        phase = phase_error(mark.scheduled_us, anchor[0], anchor[1] * US)
        self.days.add(mark.scheduled_us, offset, phase)


def unrecorded_slots(marks: Sequence[PollMark], window: tuple[int, int]) -> int:
    """Count slots with no record before, between, and after a device's records.

    Args:
        marks: The device's marks, by scheduled time.
        window: ``(start_us, end_us)``.

    Returns:
        int: Slots inferred from the interval in force.

    Raises:
        None
    """
    count = 0
    edges = [(window[0], marks[0].scheduled_us, marks[0].interval_s)]
    edges += [(a.scheduled_us, b.scheduled_us, a.interval_s) for a, b in pairwise(marks)]
    edges.append((marks[-1].scheduled_us, window[1], marks[-1].interval_s))
    for low, high, interval_s in edges:
        period = interval_s * US
        if high - low > GAP_FACTOR * period:
            count += round((high - low) / period) - 1
    return count
