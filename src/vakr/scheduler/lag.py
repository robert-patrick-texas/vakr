"""Event-loop lag: how late the loop runs ready work (OBS-004, OBS-007).

Every poll, timeout, and hand-off shares one asyncio event loop. If
something blocks it (a synchronous call that should have gone to a thread,
CPU starvation, a long garbage-collection pause), every timer fires late
and scheduling lateness rises for reasons that have nothing to do with the
targets. The monitor sleeps for a fixed probe interval and measures how much
later than requested it woke, on the monotonic clock. The largest lag of the
period goes into ``CollectorHealthObservation.event_loop_lag_us``.

Dependencies:
    asyncio: The sleep being measured.
    vakr.ports.clock: Monotonic time.

Safety:
    One sleeping task; it never blocks the loop itself.

Example:
    >>> from datetime import UTC, datetime
    >>> from vakr.ports.clock import ManualClock
    >>> monitor = LoopLagMonitor(ManualClock(datetime(2026, 10, 20, tzinfo=UTC)))
    >>> monitor.last_us, monitor.take_period_max()
    (None, None)
"""

import asyncio

from vakr.ports.clock import Clock

PROBE_INTERVAL_S = 1.0  # E.25: fine enough to catch a 1 s stall, cheap at one wake-up a second
US_PER_S = 1_000_000


class LoopLagMonitor:
    """Measures event-loop lag with a periodic sleep.

    Attributes:
        last_us: Lag of the latest probe, or ``None`` before the first.
    """

    def __init__(self, clock: Clock, interval_s: float = PROBE_INTERVAL_S) -> None:
        """Create the monitor.

        Args:
            clock: Monotonic time source; the loop's own time in production.
            interval_s: Probe interval in seconds.

        Returns:
            None

        Raises:
            None
        """
        self._clock = clock
        self._interval_s = interval_s
        self._period_max_us: int | None = None
        self.last_us: int | None = None

    def observe(self, lag_us: int) -> None:
        """Record one probe's lag.

        Args:
            lag_us: How late the probe woke, in microseconds (negative is
                clamped to zero).

        Returns:
            None

        Raises:
            None
        """
        lag_us = max(0, lag_us)
        self.last_us = lag_us
        self._period_max_us = max(lag_us, self._period_max_us or 0)

    def take_period_max(self) -> int | None:
        """Return the largest lag since the last call and start a new period.

        Args:
            None

        Returns:
            int | None: Microseconds, or ``None`` when no probe ran.

        Raises:
            None
        """
        peak, self._period_max_us = self._period_max_us, None
        return peak

    def peek_period_max(self) -> int | None:
        """Return the largest lag of the current period without resetting it.

        Args:
            None

        Returns:
            int | None: Microseconds, or ``None`` when no probe ran.

        Raises:
            None
        """
        return self._period_max_us

    async def run(self) -> None:
        """Probe forever; cancel the task to stop.

        Args:
            None

        Returns:
            None

        Raises:
            asyncio.CancelledError: When the task is cancelled.
        """
        while True:
            began = self._clock.monotonic()
            await asyncio.sleep(self._interval_s)
            late_s = self._clock.monotonic() - began - self._interval_s
            self.observe(round(late_s * US_PER_S))
