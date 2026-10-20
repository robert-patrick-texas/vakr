"""When to fsync: the group-commit schedule of Appendix E.8.

One fsync per append would cost a disk flush for every poll result; one per
minute would put a minute of telemetry at risk. E.8 settles it: fsync once
``spool.fsync_records`` records are waiting, or once the oldest waiting
append was accepted ``spool.fsync_interval`` ago, whichever comes first.
Because an append completes only when its fsync *returns*, the timed fsync
starts early: by ``MARGIN_FRACTION`` of the interval (thread wake-up, GIL
hand-off, an fsync slower than usual) plus twice the recent fsync duration,
at most half the interval (E.24). An append's wait then stays within the
interval even on a busy disk.

Dependencies:
    None beyond the standard library.

Safety:
    Pure arithmetic over monotonic times; owned by the writer thread.

Example:
    >>> plan = GroupCommit(interval_s=1.0, records=1000)
    >>> plan.written(accepted=10.0)
    >>> plan.deadline(now=10.2), plan.due(unsynced=1000, now=10.2)
    (10.9, True)
"""

_EWMA = 0.2  # weight of the newest fsync duration in the running estimate
MARGIN_FRACTION = 0.1
"""Share of the interval a timed fsync starts early, besides the fsync estimate (E.24)."""


class GroupCommit:
    """Tracks the oldest unsynced append and the recent fsync duration. Mutable.

    Attributes:
        interval_s: Longest wait for a covering fsync.
        records: Unsynced records that trigger an early fsync.
        fsync_estimate: Smoothed recent fsync duration in seconds.
        fsyncs: Data fsyncs completed.
    """

    def __init__(self, interval_s: float, records: int) -> None:
        """Start with nothing unsynced.

        Args:
            interval_s: ``spool.fsync_interval`` in seconds.
            records: ``spool.fsync_records``.

        Returns:
            None

        Raises:
            None
        """
        self.interval_s, self.records = interval_s, records
        self.fsync_estimate = 0.0
        self.fsyncs = 0
        self._oldest: float | None = None

    def written(self, accepted: float) -> None:
        """Note an append written but not yet fsynced.

        Args:
            accepted: Monotonic time the spool accepted it.

        Returns:
            None

        Raises:
            None
        """
        if self._oldest is None:
            self._oldest = accepted

    def reset(self) -> None:
        """Note that nothing is waiting for an fsync any more.

        Args:
            None

        Returns:
            None

        Raises:
            None
        """
        self._oldest = None

    def synced(self, took_s: float) -> None:
        """Note a completed fsync and fold its duration into the estimate.

        Args:
            took_s: How long the fsync took.

        Returns:
            None

        Raises:
            None
        """
        blended = (1 - _EWMA) * self.fsync_estimate + _EWMA * took_s
        self.fsync_estimate = blended if self.fsyncs else took_s
        self.fsyncs += 1
        self._oldest = None

    def deadline(self, now: float) -> float:
        """Monotonic time by which the next fsync should start.

        Args:
            now: Current monotonic time, used when nothing is waiting.

        Returns:
            float: Oldest acceptance plus the interval, less the lead time.

        Raises:
            None
        """
        oldest = now if self._oldest is None else self._oldest
        lead = min(MARGIN_FRACTION * self.interval_s + 2 * self.fsync_estimate, self.interval_s / 2)
        return oldest + self.interval_s - lead

    def due(self, unsynced: int, now: float) -> bool:
        """Report whether an fsync should happen now.

        Args:
            unsynced: Records written since the last fsync.
            now: Current monotonic time.

        Returns:
            bool: ``True`` at the record threshold or past the deadline.

        Raises:
            None
        """
        if unsynced <= 0:
            return False
        return unsynced >= self.records or now >= self.deadline(now)
