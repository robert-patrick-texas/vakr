"""The 15-minute rollup job: complete buckets, restart-safe and bounded (RET-004..RET-009, E.10).

Retention expires native rows only behind rollup coverage (ROLL-T04), so
this job is what lets ``telemetry.db`` stop growing after 30 days. It runs
hourly from ``TelemetryUpkeep``, in a worker thread, right before the
retention pass. Each pass:

1. finds the newest **complete** bucket: one whose end is more than 10
   minutes in the past, and more than 10 minutes before the append time of
   the oldest observation still waiting in the spool, so a database outage
   cannot roll up a bucket whose data has not arrived yet (E.10);
2. resumes at the stored rollup frontier (``RollupRepository.coverage_end``)
   or, with no rollups yet, at the oldest native observation. A bucket is
   written for every subject in one transaction, so the frontier is always
   a bucket every subject has finished; an interrupted pass resumes there,
   and re-running a bucket replaces it (RET-009, ROLL-T03);
3. rolls buckets forward in time, a page of subjects at a time, stepping
   over stretches with no native data at all;
4. stops after ``max_buckets`` buckets or ``budget_s`` seconds, whichever
   comes first, so weeks of backlog after an upgrade drain over several
   passes without starving the collector (E.10).

Buckets holding fewer samples than the schedule intended are counted and
logged: they are the incomplete buckets RET-009 asks to report, and the
bucket itself keeps the gap visible (ROLL-T05).

Dependencies:
    vakr.app.collector.rollup_build, rollup_settings: Buckets, bounds, reports.
    vakr.ports.telemetry: The rollup source and repository.

Safety:
    Blocking: call it from a worker thread, never the event loop (5.3).
    Every pass is bounded in buckets, time, and page size.

Example:
    >>> from datetime import UTC
    >>> from vakr.adapters.memory import MemoryTelemetryStore
    >>> from vakr.adapters.memory.telemetry import MemoryTelemetry
    >>> backend = MemoryTelemetry(MemoryTelemetryStore())
    >>> job = RollupJob(backend.rollup_source, backend.rollups)
    >>> job.run_pass(datetime(2026, 10, 1, tzinfo=UTC)).stopped
    'caught_up'
"""

import time
from collections.abc import Callable, Iterator
from datetime import datetime, timedelta
from uuid import UUID

from vakr.app.collector.build import CollectorParts
from vakr.app.collector.rollup_build import (
    expected_count,
    icmp_buckets,
    interface_buckets,
    is_thin,
)
from vakr.app.collector.rollup_settings import (
    RollupPass,
    RollupSettings,
    complete_end,
    log_pass,
    spool_pending,
)
from vakr.domain.paging import Page, PageRequest
from vakr.domain.rollups import ROLLUP_BUCKET_SECONDS, RollupBucket, bucket_start
from vakr.domain.series import TimeWindow
from vakr.domain.timestamps import EPOCH, require_utc
from vakr.ingest import Ingester
from vakr.ports.clock import Clock
from vakr.ports.telemetry import RollupRepository, RollupSource
from vakr.ports.telemetry.rollup_source import PollSlot


class RollupJob:
    """Rolls up complete 15-minute buckets; the collector's ``RollupHook``.

    Attributes:
        last: The most recent pass, for diagnostics.
    """

    def __init__(
        self, source: RollupSource, rollups: RollupRepository,
        settings: RollupSettings | None = None, *, monotonic: Callable[[], float] = time.monotonic,
        pending: Callable[[], datetime | None] = lambda: None,
    ) -> None:  # fmt: skip
        """Bind the job.

        Args:
            source: Native reads.
            rollups: Where buckets go, and the frontier.
            settings: Bounds; the E.10 defaults when ``None``.
            monotonic: Clock for the time budget.
            pending: Append time of the oldest observation still waiting in
                the spool, or ``None`` (``rollup_settings.spool_pending``).

        Returns:
            None

        Raises:
            None
        """
        self._source, self._rollups = source, rollups
        self._settings = settings or RollupSettings()
        self._monotonic, self._pending = monotonic, pending
        self.last: RollupPass | None = None

    def run(self, now: datetime) -> int:
        """Run one bounded pass and log it (``RollupHook``).

        Args:
            now: Reference time (UTC).

        Returns:
            int: Metric buckets written.

        Raises:
            ValidationError: If ``now`` is not UTC.
            StorageError: If the database is unavailable; buckets already
                written stay, and the next pass resumes after them.
        """
        report = self.run_pass(now)
        self.last = report
        log_pass(report)
        return report.written

    def run_pass(self, now: datetime) -> RollupPass:
        """Roll up complete buckets from the frontier on, within the pass bounds.

        Args:
            now: Reference time (UTC).

        Returns:
            RollupPass: What the pass did.

        Raises:
            ValidationError: If ``now`` is not UTC.
            StorageError: If the database is unavailable.
        """
        require_utc(now, "now")
        s, width = self._settings, timedelta(seconds=ROLLUP_BUCKET_SECONDS)
        limit = complete_end(now, self._pending(), s.settle)
        start = self._resume(limit)
        deadline = self._monotonic() + s.budget_s
        rolled = written = thin = 0
        stopped = "caught_up"
        while start is not None and start + width <= limit:
            if rolled >= s.max_buckets or self._monotonic() >= deadline:
                stopped = "bucket_limit" if rolled >= s.max_buckets else "time_budget"
                break
            buckets, thin_here = self._roll(start)
            rolled += 1
            if buckets:
                written, thin = written + self._rollups.write(buckets), thin + thin_here
                start += width
            else:
                start = self._next(start + width, limit)
        return RollupPass(rolled, written, thin, self._rollups.coverage_end(), limit, stopped)

    def _resume(self, limit: datetime) -> datetime | None:
        """Return the first bucket to roll up: at the frontier, else at the oldest data.

        Args:
            limit: End of the newest complete bucket.

        Returns:
            datetime | None: A bucket start, or ``None`` when nothing is stored.

        Raises:
            StorageError: If the database is unavailable.
        """
        frontier = self._rollups.coverage_end()
        return self._next(EPOCH, limit) if frontier is None else bucket_start(frontier)

    def _next(self, after: datetime, limit: datetime) -> datetime | None:
        """Return the start of the first bucket at or after ``after`` holding native data.

        Args:
            after: Earliest time to look at.
            limit: End of the newest complete bucket.

        Returns:
            datetime | None: The bucket start, or ``None`` when none remains.

        Raises:
            StorageError: If the database is unavailable.
        """
        if after >= limit:
            return None
        found = self._source.first_native_time(TimeWindow(after, limit))
        return None if found is None else bucket_start(found)

    def _roll(self, start: datetime) -> tuple[list[RollupBucket], int]:
        """Build every subject's buckets for one bucket start.

        Args:
            start: The bucket start.

        Returns:
            tuple[list[RollupBucket], int]: The buckets and how many subjects were thin.

        Raises:
            StorageError: If the database is unavailable.
        """
        span, window = (start, ROLLUP_BUCKET_SECONDS), TimeWindow(start, start + _width())
        fallback = self._settings.fallback_interval_s
        schedules: dict[tuple[UUID, UUID], PollSlot | None] = {}
        buckets: list[RollupBucket] = []
        thin = 0
        for device in self._pages(self._source.device_evidence, window):
            schedules[device.device_id, device.collector_id] = device.schedule
            made = icmp_buckets(device, span, expected_count(device.schedule, *span, fallback))
            buckets.extend(made)
            thin += is_thin(made)
        for series in self._pages(self._source.interface_samples, window):
            schedule = schedules.get((series.device_id, series.collector_id))
            made = interface_buckets(series, span, expected_count(schedule, *span, fallback))
            buckets.extend(made)
            thin += is_thin(made)
        return buckets, thin

    def _pages[T](self, read: Callable[[TimeWindow, PageRequest], Page[T]],
                  window: TimeWindow) -> Iterator[T]:  # fmt: skip
        """Yield every item of a paged read.

        Args:
            read: A ``RollupSource`` page method.
            window: The bucket.

        Returns:
            Iterator[T]: The items, page after page.

        Raises:
            StorageError: If the database is unavailable.
        """
        cursor: str | None = None
        while True:
            page = read(window, PageRequest(self._settings.page_limit, cursor))
            yield from page.items
            if page.next_cursor is None:
                return
            cursor = page.next_cursor


def _width() -> timedelta:
    """Return the reference bucket width.

    Args:
        None

    Returns:
        timedelta: 15 minutes (E.10).

    Raises:
        None
    """
    return timedelta(seconds=ROLLUP_BUCKET_SECONDS)


def collector_job(parts: CollectorParts, clock: Clock, ingester: Ingester) -> RollupJob:
    """Build the collector's rollup job over its telemetry store (RET-004, E.10).

    Args:
        parts: The opened collector parts (telemetry store, ``[polling]`` defaults).
        clock: The service clock, for the time budget.
        ingester: Whose spool backlog holds buckets back.

    Returns:
        RollupJob: The job ``TelemetryUpkeep`` runs hourly before retention.

    Raises:
        None
    """
    settings = RollupSettings(fallback_interval_s=parts.scheduler.defaults.interval_s)
    return RollupJob(parts.telemetry.rollup_source, parts.telemetry.rollups, settings,
                     monotonic=clock.monotonic, pending=spool_pending(ingester))  # fmt: skip
