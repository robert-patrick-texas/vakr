"""Time sources: UTC wall-clock time and monotonic time (TIME-001, invariant 10).

Two clocks answer two questions. Wall-clock UTC says *when* something
happened and is what vakr stores. Monotonic time says *how long* something
took and never jumps when NTP steps the system clock, so timeouts and poll
durations stay correct across a clock correction. Code takes a ``Clock``
instead of calling ``datetime.now`` so tests can control time exactly.

Dependencies:
    datetime, time: The system clocks.
    vakr.domain.timestamps: The UTC rule.

Safety:
    ``ManualClock`` refuses to move monotonic time backwards, matching the
    guarantee real code relies on.

Example:
    >>> from datetime import UTC, datetime, timedelta
    >>> clock = ManualClock(datetime(2026, 10, 20, tzinfo=UTC))
    >>> clock.advance(timedelta(seconds=90))
    >>> clock.now_utc().minute, clock.monotonic()
    (1, 90.0)
"""

import time
from datetime import UTC, datetime, timedelta
from typing import Protocol

from vakr.domain.errors import ValidationError
from vakr.domain.timestamps import require_utc


class Clock(Protocol):
    """A source of UTC wall-clock time and monotonic time."""

    def now_utc(self) -> datetime:
        """Return the current time.

        Args:
            None

        Returns:
            datetime: Aware UTC datetime.

        Raises:
            None
        """
        ...

    def monotonic(self) -> float:
        """Return monotonic seconds from an arbitrary origin.

        Args:
            None

        Returns:
            float: Seconds; only differences are meaningful.

        Raises:
            None
        """
        ...


class SystemClock:
    """The real clocks of the host."""

    def now_utc(self) -> datetime:
        """Return the current UTC time from the system clock.

        Args:
            None

        Returns:
            datetime: Aware UTC datetime.

        Raises:
            None
        """
        return datetime.now(UTC)

    def monotonic(self) -> float:
        """Return ``time.monotonic()``.

        Args:
            None

        Returns:
            float: Monotonic seconds.

        Raises:
            None
        """
        return time.monotonic()


class ManualClock:
    """A clock that moves only when a test tells it to.

    Attributes:
        None beyond the private state behind ``now_utc`` and ``monotonic``.
    """

    def __init__(self, start: datetime, monotonic_start: float = 0.0) -> None:
        """Create the clock.

        Args:
            start: Initial wall-clock time (aware UTC).
            monotonic_start: Initial monotonic reading.

        Returns:
            None

        Raises:
            ValidationError: If ``start`` is not an aware UTC datetime.
        """
        require_utc(start, "start")
        self._now = start
        self._mono = monotonic_start

    def now_utc(self) -> datetime:
        """Return the current manual time.

        Args:
            None

        Returns:
            datetime: Aware UTC datetime.

        Raises:
            None
        """
        return self._now

    def monotonic(self) -> float:
        """Return the current manual monotonic reading.

        Args:
            None

        Returns:
            float: Monotonic seconds.

        Raises:
            None
        """
        return self._mono

    def advance(self, delta: timedelta) -> None:
        """Move both clocks forward by ``delta``.

        Args:
            delta: Non-negative amount of time.

        Returns:
            None

        Raises:
            ValidationError: If ``delta`` is negative.
        """
        if delta < timedelta(0):
            raise ValidationError("a clock cannot move backwards", field="delta")
        self._now += delta
        self._mono += delta.total_seconds()

    def set_utc(self, moment: datetime) -> None:
        """Set wall-clock time, as an NTP step would; monotonic time is unchanged.

        Args:
            moment: New wall-clock time (aware UTC); may be earlier.

        Returns:
            None

        Raises:
            ValidationError: If ``moment`` is not an aware UTC datetime.
        """
        require_utc(moment, "moment")
        self._now = moment
