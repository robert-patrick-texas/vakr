"""The ranking projector: keeps the exact ranking projection current (E.28, DASH-003, DASH-004).

A 24-hour ranking over every native sample takes about 4.7 s at MVP scale,
over the 2 s dashboard budget of 12.9. The projection
(``vakr.ports.telemetry.ranking_projection``) holds each complete
15-minute bucket's exact ranking partials, so a ranking reads whole buckets
from it and scans only the rest of its window natively. This job keeps it
current. It runs every 15 minutes from ``TelemetryUpkeep``, beside the
staging move, in a worker thread, and uses the rollup job's completeness
rule (E.10: a bucket is complete once its end is more than 10 minutes in
the past and before the oldest observation still waiting in the spool).
Each pass:

1. on the first pass after the collector starts, forgets the whole
   projection, so it is always rebuilt by the release that reads it;
2. lists the complete buckets of the last ``PROJECTION_KEEP`` (25 h) that
   are not projected, newest first, because short windows need the newest;
3. projects them, one write transaction each, and stops after
   ``max_buckets`` buckets or ``budget_s`` seconds, so a rebuild after a
   start finishes in one pass at MVP scale and never starves collection.

Values never depend on this job: a bucket it has not projected yet is read
natively, with the same result, only slower.

Dependencies:
    vakr.app.collector.rollup_settings: The completeness rule and spool backlog.
    vakr.ports.telemetry.ranking_projection: The port and its bounds.

Safety:
    Blocking: call it from a worker thread, never the event loop (5.3).
    Every pass is bounded in buckets and time.

Example:
    >>> from datetime import UTC
    >>> from vakr.adapters.memory import MemoryTelemetryStore
    >>> from vakr.adapters.memory.telemetry import MemoryTelemetry
    >>> job = RankingProjector(MemoryTelemetry(MemoryTelemetryStore()).ranking_projection)
    >>> job.run_pass(datetime(2026, 10, 1, tzinfo=UTC)).projected  # 24.5 h of empty buckets
    98
"""

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta

from vakr.app.collector.build import CollectorParts
from vakr.app.collector.rollup_settings import SETTLE, complete_end, spool_pending
from vakr.domain.rollups import ROLLUP_BUCKET_SECONDS, bucket_start
from vakr.domain.series import TimeWindow
from vakr.domain.timestamps import require_utc
from vakr.ingest import Ingester
from vakr.ports.clock import Clock
from vakr.ports.telemetry.ranking_projection import (
    MAX_PROJECTION_BUCKETS,
    PROJECTION_KEEP,
    RankingProjection,
)

_LOG = logging.getLogger(__name__)

PROJECTION_BUDGET_S = 60.0
"""Seconds of work one projector pass starts buckets within (E.28)."""

_BUCKET = timedelta(seconds=ROLLUP_BUCKET_SECONDS)


@dataclass(frozen=True, slots=True)
class ProjectionPass:
    """What one projector pass did. Immutable.

    Attributes:
        forgot: Buckets forgotten first (the rebuild after a start).
        projected: Buckets projected.
        partials: Interface partials stored.
        remaining: Complete buckets of the last 25 hours still not projected.
        stopped: ``caught_up``, ``bucket_limit``, or ``time_budget``.
    """

    forgot: int
    projected: int
    partials: int
    remaining: int
    stopped: str


class RankingProjector:
    """Projects complete buckets for rankings; the collector's ``ProjectionHook``.

    Attributes:
        last: The most recent pass, for diagnostics.
    """

    def __init__(
        self, projection: RankingProjection, *, monotonic: Callable[[], float] = time.monotonic,
        pending: Callable[[], datetime | None] = lambda: None,
        max_buckets: int = MAX_PROJECTION_BUCKETS, budget_s: float = PROJECTION_BUDGET_S,
    ) -> None:  # fmt: skip
        """Bind the job.

        Args:
            projection: The projection port.
            monotonic: Clock for the time budget.
            pending: Append time of the oldest observation still waiting in
                the spool, or ``None`` (``rollup_settings.spool_pending``).
            max_buckets: Buckets per pass, 1 to ``MAX_PROJECTION_BUCKETS``.
            budget_s: Seconds per pass; the bucket in progress finishes.

        Returns:
            None

        Raises:
            None
        """
        self._projection, self._monotonic, self._pending = projection, monotonic, pending
        self._max, self._budget = max(1, min(max_buckets, MAX_PROJECTION_BUCKETS)), budget_s
        self._fresh = True
        self.last: ProjectionPass | None = None

    def run(self, now: datetime) -> int:
        """Run one bounded pass and log it (``ProjectionHook``).

        Args:
            now: Reference time (UTC).

        Returns:
            int: Buckets projected.

        Raises:
            ValidationError: If ``now`` is not UTC.
            StorageError: If the database is unavailable; buckets already
                projected stay, and the next pass continues.
        """
        report = self.run_pass(now)
        self.last = report
        _log(report)
        return report.projected

    def run_pass(self, now: datetime) -> ProjectionPass:
        """Project complete, unprojected buckets of the last 25 hours, newest first.

        Args:
            now: Reference time (UTC).

        Returns:
            ProjectionPass: What the pass did.

        Raises:
            ValidationError: If ``now`` is not UTC.
            StorageError: If the database is unavailable.
        """
        require_utc(now, "now")
        forgot = self._projection.forget() if self._fresh else 0
        self._fresh = False
        limit = complete_end(now, self._pending(), SETTLE)
        start = max(limit - PROJECTION_KEEP, bucket_start(now - PROJECTION_KEEP) + _BUCKET)
        if start >= limit:
            return ProjectionPass(forgot, 0, 0, 0, "caught_up")
        window = TimeWindow(start, limit)
        todo = self._projection.unprojected(window, limit=self._max)
        deadline, projected, partials = self._monotonic() + self._budget, 0, 0
        stopped = "caught_up"
        for bucket in todo:
            if self._monotonic() >= deadline:
                stopped = "time_budget"
                break
            partials += self._projection.project(bucket)
            projected += 1
        remaining = len(self._projection.unprojected(window))
        if remaining and stopped == "caught_up":
            stopped = "bucket_limit"
        return ProjectionPass(forgot, projected, partials, remaining, stopped)


def _log(report: ProjectionPass) -> None:
    """Log a pass in operator language when it did or left work.

    Args:
        report: The pass.

    Returns:
        None

    Raises:
        None
    """
    if report.projected or report.forgot:
        _LOG.info(
            "projected %d bucket(s) of 15 minutes for rankings (%d interface partials); "
            "%d complete bucket(s) of the last 25 h are still read from native samples",
            report.projected,
            report.partials,
            report.remaining,
            extra={"event_id": "telemetry.ranking_projection"},
        )
    if report.stopped != "caught_up":
        _LOG.info(
            "the ranking projection is catching up (pass stopped at its %s); long rankings "
            "stay exact and read the rest natively until the next pass",
            report.stopped.replace("_", " "),
            extra={"event_id": "telemetry.ranking_projection_behind"},
        )


def collector_projector(
    parts: CollectorParts, clock: Clock, ingester: Ingester
) -> RankingProjector:
    """Build the collector's ranking projector over its telemetry store (E.28).

    Args:
        parts: The opened collector parts.
        clock: The service clock, for the time budget.
        ingester: Whose spool backlog holds buckets back (E.10).

    Returns:
        RankingProjector: The job ``TelemetryUpkeep`` runs every 15 minutes.

    Raises:
        None
    """
    return RankingProjector(parts.telemetry.ranking_projection, monotonic=clock.monotonic,
                            pending=spool_pending(ingester))  # fmt: skip
