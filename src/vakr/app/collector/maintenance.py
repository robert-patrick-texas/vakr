"""Telemetry upkeep inside the collector: staging moves, rollups, retention (E.10, E.21).

The Compact collector is the only telemetry writer (6.2), so it also keeps
the store in shape:

* every 15 minutes, ``maintain(now)`` moves staged rows older than two
  minutes into their day partitions and truncates the WAL (E.21), and the
  ranking projector (``ProjectionHook``) projects the newly complete
  15-minute buckets that long rankings read (E.28);
* every hour, the rollup job (``RollupHook``) rolls up every complete
  15-minute bucket, within its per-pass bounds, and then the retention
  pass expires native rows and rollups exactly by policy (E.10, E.21).
  Retention never expires native rows that no rollup covers (ROLL-T04),
  so it runs even when the rollup pass fails.

``CollectorService`` builds the real jobs (``rollup_job.collector_job``,
``ranking_projector.collector_projector``); ``NoRollups``, which computes
nothing and so keeps every native row, is the default only for callers
that pass no job, and without a projector rankings scan native data.
Each pass runs in a worker thread, one at a time, after the first pass at
startup. A failed pass is logged and retried at the next interval; it
never stops collection, because the spool keeps every observation until
it is stored (invariant 8).

Dependencies:
    asyncio: The timer loop and worker threads.
    vakr.ports.telemetry: The retention port.

Safety:
    Writes go through the adapter's single-writer gate (5.3), shared with ingestion.

Example:
    >>> NoRollups().run(None)
    0
"""

import asyncio
import contextlib
import logging
from datetime import datetime
from typing import Any, Protocol

from vakr.app.collector.timings import ServiceTimings
from vakr.ports.clock import Clock
from vakr.ports.telemetry import RetentionManager

_LOG = logging.getLogger(__name__)
_KINDS = ("move", "projection", "retention")


class MaintainedTelemetry(Protocol):
    """The telemetry operations upkeep needs (``SqliteTelemetry`` provides them)."""

    @property
    def retention(self) -> RetentionManager:
        """Return the retention port.

        Args:
            None

        Returns:
            RetentionManager: Expiry by policy.

        Raises:
            None
        """
        ...

    def maintain(self, now: datetime) -> Any:
        """Move staged rows and checkpoint the WAL.

        Args:
            now: Reference time (UTC).

        Returns:
            Any: A report.

        Raises:
            StorageError: If the database is unavailable.
        """
        ...


class RollupHook(Protocol):
    """The rollup job: complete 15-minute buckets before retention runs (E.10, RET-004)."""

    def run(self, now: datetime) -> int:
        """Roll up every complete bucket not yet rolled up.

        Args:
            now: Reference time (UTC).

        Returns:
            int: Buckets written.

        Raises:
            StorageError: If the database is unavailable.
        """
        ...


class ProjectionHook(Protocol):
    """The ranking projector: complete buckets for long rankings (E.28, 12.9)."""

    def run(self, now: datetime) -> int:
        """Project every complete bucket of the last 25 hours not yet projected.

        Args:
            now: Reference time (UTC).

        Returns:
            int: Buckets projected.

        Raises:
            StorageError: If the database is unavailable.
        """
        ...


class NoRollups:
    """A rollup or projection hook that computes nothing (every native row stays, rankings scan)."""

    def run(self, now: datetime | None) -> int:  # noqa: ARG002 - hook shape
        """Do nothing.

        Args:
            now: Reference time (UTC).

        Returns:
            int: 0 buckets.

        Raises:
            None
        """
        return 0


class TelemetryUpkeep:
    """Runs the staging move and the hourly rollup-and-retention passes.

    Attributes:
        passes: Completed passes by kind (``move``, ``projection``, ``retention``).
        failures: Failed passes by kind.
        last_success: When each kind last succeeded (UTC), for the self-metrics (E.33).
        last_duration_s: How long each kind's last successful pass took.
    """

    def __init__(
        self,
        telemetry: MaintainedTelemetry,
        clock: Clock,
        timings: ServiceTimings,
        rollups: RollupHook | None = None,
        projection: ProjectionHook | None = None,
    ) -> None:
        """Bind the store.

        Args:
            telemetry: The telemetry backend.
            clock: Monotonic and UTC time.
            timings: Cadences (E.21, E.27).
            rollups: The rollup job; ``NoRollups`` (keep all native data) by default.
            projection: The ranking projector, run with the staging move;
                none by default (rankings then scan native data).

        Returns:
            None

        Raises:
            None
        """
        self._telemetry, self._clock, self._timings = telemetry, clock, timings
        self._rollups = NoRollups() if rollups is None else rollups
        self._projection = NoRollups() if projection is None else projection
        self._stop = asyncio.Event()
        self.passes = dict.fromkeys(_KINDS, 0)
        self.failures = dict.fromkeys(_KINDS, 0)
        self.last_success: dict[str, datetime] = {}
        self.last_duration_s: dict[str, float] = {}

    async def run(self) -> None:
        """Run passes on their cadences until ``stop``; a pass in progress finishes.

        Args:
            None

        Returns:
            None

        Raises:
            None
        """
        clock, timings = self._clock, self._timings
        due = dict.fromkeys(_KINDS, clock.monotonic())
        every = {"move": timings.maintenance_interval_s,
                 "projection": timings.maintenance_interval_s,
                 "retention": timings.retention_interval_s}  # fmt: skip
        while not self._stop.is_set():
            for kind in _KINDS:
                if clock.monotonic() >= due[kind]:
                    await self._pass(kind)
                    due[kind] = clock.monotonic() + every[kind]
            delay = max(0.0, min(due.values()) - clock.monotonic())
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._stop.wait(), delay)

    def stop(self) -> None:
        """Ask ``run`` to return after any pass in progress.

        Args:
            None

        Returns:
            None

        Raises:
            None
        """
        self._stop.set()

    async def _pass(self, kind: str) -> None:
        """Run one pass in a worker thread, logging rather than raising.

        Args:
            kind: ``move``, ``projection``, or ``retention``.

        Returns:
            None

        Raises:
            None
        """
        now, began = self._clock.now_utc(), self._clock.monotonic()
        try:
            await asyncio.to_thread(self._work, kind, now)
        except Exception as exc:  # noqa: BLE001 - upkeep trouble must never stop collection
            self.failures[kind] += 1
            _LOG.warning(
                "telemetry %s pass failed (%s); observations stay safe in the spool "
                "and the pass runs again later",
                kind,
                type(exc).__name__,
                extra={"event_id": f"telemetry.{kind}_failed"},
            )
            return
        self.passes[kind] += 1
        self.last_success[kind] = now
        self.last_duration_s[kind] = self._clock.monotonic() - began

    def _work(self, kind: str, now: datetime) -> None:
        """Do one pass (blocking; worker thread only).

        Args:
            kind: ``move``, ``projection``, or ``retention``.
            now: Reference time (UTC).

        Returns:
            None

        Raises:
            StorageError: If the database is unavailable.
        """
        if kind == "move":
            self._telemetry.maintain(now)
            return
        if kind == "projection":
            self._projection.run(now)
            return
        try:
            self._rollups.run(now)
        finally:
            # Retention only ever expires what rollups already cover, so a failed
            # rollup pass must not also stop rollups past day 400 from expiring.
            report = self._telemetry.retention.apply(now)
        _LOG.debug("retention pass: %s", report, extra={"event_id": "telemetry.retention"})
