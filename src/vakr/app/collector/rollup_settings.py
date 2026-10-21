"""Bounds, reports, and logging of the rollup job (Appendix E.10, RET-009).

The job's bounds are constants, not options: like the collector's other
cadences (E.27) none of them is a site decision. A pass rolls up at most
seven days of buckets and starts no bucket after two minutes of work, so a
backlog of weeks after an upgrade drains over a few hourly passes while
the collector keeps polling. ``complete_end`` is E.10's completeness rule,
tightened by the spool: a bucket is complete once its end is more than 10
minutes in the past *and* more than 10 minutes before the oldest
observation still waiting to be stored.

Dependencies:
    logging: Operator-language pass summaries.
    vakr.domain: Bucket alignment and paging bounds.
    vakr.ingest: The ingester whose backlog holds rollups back.

Safety:
    Pure values and log lines; nothing here touches storage.

Example:
    >>> RollupSettings().max_buckets, RollupSettings().budget_s
    (672, 120.0)
"""

import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta

from vakr.domain.paging import MAX_PAGE_LIMIT
from vakr.domain.rollups import bucket_start
from vakr.domain.timestamps import EPOCH, from_epoch_us
from vakr.ingest import Ingester

_LOG = logging.getLogger("vakr.app.collector.rollup_job")

SETTLE = timedelta(minutes=10)
"""A bucket is complete once its end is more than this far in the past (E.10)."""

MAX_BUCKETS_PER_PASS = 672
"""Buckets one pass rolls up at most: 7 days of backlog (E.10)."""

PASS_BUDGET_S = 120.0
"""Seconds of work one pass starts buckets within (E.10)."""

_ONE_US = timedelta(microseconds=1)


@dataclass(frozen=True, slots=True)
class RollupSettings:
    """The job's bounds (Appendix E.10). Immutable.

    Attributes:
        fallback_interval_s: ``polling.default_interval``; sets the expected
            count of a device with no poll record in the schedule lookback.
        max_buckets: Buckets per pass.
        budget_s: Seconds per pass; the bucket in progress finishes.
        page_limit: Subjects per read.
        settle: How long after its end a bucket is complete.
    """

    fallback_interval_s: int = 60
    max_buckets: int = MAX_BUCKETS_PER_PASS
    budget_s: float = PASS_BUDGET_S
    page_limit: int = MAX_PAGE_LIMIT
    settle: timedelta = SETTLE


@dataclass(frozen=True, slots=True)
class RollupPass:
    """What one pass did. Immutable.

    Attributes:
        buckets: 15-minute buckets rolled up (empty stretches count once each).
        written: Metric buckets written.
        thin: Subject buckets with fewer samples than intended (RET-009).
        frontier: Rollup coverage after the pass, or ``None``.
        complete_end: End of the newest complete bucket.
        stopped: ``caught_up``, ``bucket_limit``, or ``time_budget``.
    """

    buckets: int
    written: int
    thin: int
    frontier: datetime | None
    complete_end: datetime
    stopped: str


def complete_end(now: datetime, pending: datetime | None, settle: timedelta) -> datetime:
    """Return the end of the newest complete bucket (E.10).

    Args:
        now: Reference time (UTC).
        pending: Append time of the oldest observation still in the spool, if any.
        settle: How far in the past a complete bucket must end.

    Returns:
        datetime: A bucket boundary; every bucket ending at or before it is complete.

    Raises:
        None
    """
    edge = now if pending is None else min(now, pending)
    return bucket_start(edge - settle - _ONE_US)


def log_pass(report: RollupPass) -> None:
    """Log a pass in operator language: progress, thin buckets, and backlog.

    Args:
        report: The pass.

    Returns:
        None

    Raises:
        None
    """
    if report.buckets:
        _LOG.info(
            "rolled up %d bucket(s) of 15 minutes, up to %s; %d subject bucket(s) held "
            "fewer samples than scheduled",
            report.buckets,
            "nothing" if report.frontier is None else report.frontier.isoformat(),
            report.thin,
            extra={"event_id": "telemetry.rollups"},
        )
    if report.stopped != "caught_up" and report.frontier is not None:
        behind = (report.complete_end - report.frontier).total_seconds() / 3600
        _LOG.info(
            "rollups are catching up: %.1f h of complete buckets remain (pass stopped at "
            "its %s); the next hourly pass continues, and native data is kept until then",
            behind,
            report.stopped.replace("_", " "),
            extra={"event_id": "telemetry.rollups_behind"},
        )


def spool_pending(ingester: Ingester) -> Callable[[], datetime | None]:
    """Return the rollup job's view of what the spool still holds (E.10).

    The ingester records the append time of the oldest record it could not
    store yet. Until its first write after a start it does not know that
    time, so records left from before the start hold every bucket back for
    that pass rather than risk rolling up a bucket whose data is still
    waiting.

    Args:
        ingester: The collector's ingester.

    Returns:
        Callable[[], datetime | None]: Reads the oldest waiting append time,
        ``EPOCH`` while it is unknown, or ``None`` when nothing old waits.

    Raises:
        None
    """

    def pending() -> datetime | None:
        """Read the ingester's backlog (worker thread; plain attribute reads).

        Args:
            None

        Returns:
            datetime | None: As described above.

        Raises:
            None
        """
        status = ingester.status
        if status.oldest_unacked_us is not None:
            return from_epoch_us(status.oldest_unacked_us)
        if status.last_write_us is None and ingester.spool.durable_seq > status.acked_seq:
            return EPOCH
        return None

    return pending
