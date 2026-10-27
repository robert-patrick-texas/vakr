"""Start-offset and slot-phase drift over UTC days (MVP-ACC-01 "no systematic drift", E.35).

A scheduler that anchors each slot to the previous poll's completion, or
whose timeline slowly parts from the wall clock, shows up as a trend: the
daily median start offset (start minus schedule) or the daily median slot
phase (how far a device's slot moved from its first slot in the window,
modulo the interval) climbs day after day. ``DayTally`` collects both per
UTC day; ``slope`` fits a least-squares line through the daily medians, in
microseconds per day. Medians keep a few late polls in a busy minute from
reading as drift.

Dependencies:
    array, statistics: Compact per-day samples and medians.

Safety:
    Pure computation; memory is one float per healthy poll in the window.

Example:
    >>> slope([(0, 10.0), (1, 12.0), (2, 14.0)])
    2.0
    >>> phase_error(60_500_000, 500_000, 60_000_000)
    0
"""

from array import array
from dataclasses import dataclass, field
from statistics import median

from vakr.query.acceptance_limits import DAY_US


def phase_error(scheduled_us: int, reference_us: int, period_us: int) -> int:
    """Return how far a slot sits from its device's reference phase, wrapped to ±half a period.

    Args:
        scheduled_us: The slot.
        reference_us: The device's first slot in the window.
        period_us: The interval in microseconds.

    Returns:
        int: Signed microseconds; 0 on the device's grid.

    Raises:
        None
    """
    error = (scheduled_us - reference_us) % period_us
    return error - period_us if error > period_us // 2 else error


def slope(points: list[tuple[int, float]]) -> float | None:
    """Fit ``value = a + b * day`` by least squares and return ``b``.

    Args:
        points: ``(day index, value)`` pairs.

    Returns:
        float | None: Units of ``value`` per day; ``None`` with fewer than two days.

    Raises:
        None
    """
    if len({d for d, _ in points}) < 2:
        return None
    n = len(points)
    mean_x = sum(d for d, _ in points) / n
    mean_y = sum(v for _, v in points) / n
    sxx = sum((d - mean_x) ** 2 for d, _ in points)
    sxy = sum((d - mean_x) * (v - mean_y) for d, v in points)
    return sxy / sxx


@dataclass(slots=True)
class DayTally:
    """Start offsets and slot phases of healthy-device polls, per UTC day.

    Attributes:
        offsets: Day index to start offsets in microseconds.
        phases: Day index to slot phase errors in microseconds.
    """

    offsets: dict[int, array] = field(default_factory=dict)
    phases: dict[int, array] = field(default_factory=dict)

    def add(self, scheduled_us: int, offset_us: int, phase_us: int) -> None:
        """Record one started poll.

        Args:
            scheduled_us: Its slot, which picks the UTC day.
            offset_us: Start minus schedule.
            phase_us: Its slot phase error.

        Returns:
            None

        Raises:
            None
        """
        day = scheduled_us // DAY_US
        self.offsets.setdefault(day, array("d")).append(offset_us)
        self.phases.setdefault(day, array("d")).append(phase_us)

    def days(self) -> list[tuple[int, int, float, float]]:
        """Return each day's index, poll count, median offset, and median phase, oldest first.

        Args:
            None

        Returns:
            list[tuple[int, int, float, float]]: Microsecond medians.

        Raises:
            None
        """
        return [
            (day, len(self.offsets[day]), median(self.offsets[day]), median(self.phases[day]))
            for day in sorted(self.offsets)
        ]

    def all_offsets(self) -> array:
        """Return every recorded start offset.

        Args:
            None

        Returns:
            array: Microseconds, in no particular order.

        Raises:
            None
        """
        out = array("d")
        for values in self.offsets.values():
            out.extend(values)
        return out
