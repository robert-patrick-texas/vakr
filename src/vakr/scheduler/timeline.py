"""The plan timeline: monotonic cadence with UTC labels (SCHED-010, SCHED-011, SCHED-018, TIME-001).

The scheduler counts time in integer microseconds on one *plan timeline*.
It is anchored once, when the scheduler starts: that instant's UTC time
becomes the timeline's origin, and from then on the timeline advances only
with the monotonic clock. Due times, lateness, and durations are therefore
immune to NTP steps (SCHED-011), and cadence is anchored to intended slots,
not to completion times (invariant 10).

Persisted timestamps are UTC (TIME-001). A slot's ``scheduled_at`` is its
timeline time plus a *label shift*. When the wall clock is seen to differ
from the timeline by more than ``CLOCK_STEP_THRESHOLD_US`` (E.25), the shift
is updated, so later ``scheduled_at`` values follow the corrected clock while
the monotonic cadence is untouched: no burst, gap, or duplicate poll
(SCHED-018, SCHED-T04). The step shows once as an irregular spacing of
``scheduled_at``, and the measured offset is exposed for health views.

Dependencies:
    vakr.ports.clock: ``Clock`` (monotonic and UTC).
    vakr.domain.timestamps: Epoch-microsecond conversion.

Safety:
    Never sleeps or blocks; it only reads the clock.

Example:
    >>> from datetime import UTC, datetime, timedelta
    >>> from vakr.ports.clock import ManualClock
    >>> clock = ManualClock(datetime(2026, 10, 20, tzinfo=UTC))
    >>> line = Timeline(clock)
    >>> clock.advance(timedelta(seconds=90))
    >>> line.label(line.now_us()).isoformat()
    '2026-10-20T00:01:30+00:00'
"""

from datetime import datetime

from vakr.domain.timestamps import from_epoch_us, to_epoch_us
from vakr.ports.clock import Clock

US_PER_S = 1_000_000
CLOCK_STEP_THRESHOLD_US = 1_000_000  # E.25: below NTP's usual 1 s step threshold, labels slew


class Timeline:
    """Maps the integer-microsecond plan timeline onto the clock.

    Attributes:
        clock_steps: Number of wall-clock steps detected and relabelled.
    """

    def __init__(self, clock: Clock) -> None:
        """Anchor the timeline at the current instant.

        Args:
            clock: Monotonic and UTC time source.

        Returns:
            None

        Raises:
            None
        """
        self._clock = clock
        self._mono_origin = clock.monotonic()
        self._origin_us = to_epoch_us(clock.now_utc())
        self._label_shift_us = 0
        self.clock_steps = 0

    def now_us(self) -> int:
        """Return the current plan-timeline time, from the monotonic clock.

        Args:
            None

        Returns:
            int: Microseconds on the plan timeline.

        Raises:
            None
        """
        elapsed = self._clock.monotonic() - self._mono_origin
        return self._origin_us + round(elapsed * US_PER_S)

    def delay_s(self, t_us: int) -> float:
        """Return how long to wait until a timeline time.

        Args:
            t_us: Target time on the plan timeline.

        Returns:
            float: Seconds, never negative.

        Raises:
            None
        """
        return max(0.0, (t_us - self.now_us()) / US_PER_S)

    def label(self, t_us: int) -> datetime:
        """Return the UTC label of a timeline time, such as a slot's ``scheduled_at``.

        Args:
            t_us: Time on the plan timeline.

        Returns:
            datetime: Aware UTC datetime.

        Raises:
            None
        """
        return from_epoch_us(t_us + self._label_shift_us)

    def wall_offset_us(self) -> int:
        """Return how far the wall clock is from the unshifted plan timeline.

        Args:
            None

        Returns:
            int: Signed microseconds; positive when the wall clock is ahead.

        Raises:
            None
        """
        return to_epoch_us(self._clock.now_utc()) - self.now_us()

    def check_step(self) -> bool:
        """Detect a wall-clock step and move future labels with it (SCHED-018).

        Args:
            None

        Returns:
            bool: True when a step beyond the threshold was detected.

        Raises:
            None
        """
        offset = self.wall_offset_us()
        if abs(offset - self._label_shift_us) <= CLOCK_STEP_THRESHOLD_US:
            return False
        self._label_shift_us = offset
        self.clock_steps += 1
        return True
