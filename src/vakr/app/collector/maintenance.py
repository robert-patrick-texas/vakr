"""Telemetry upkeep inside the collector: staging moves, rollups hook, retention (E.10, E.21).

The Compact collector is the only telemetry writer (6.2), so it also keeps
the store in shape:

* every 15 minutes, ``maintain(now)`` moves staged rows older than two
  minutes into their day partitions and truncates the WAL (E.21);
* every hour, the rollup hook runs and then the retention pass expires
  native rows and rollups exactly by policy (E.10, E.21). Retention never
  expires native rows that no rollup covers, so until the M4 rollup job
  exists the hourly pass keeps all native data.

``ROLLUP_HOOK`` is the clearly named place for the M4 rollup job; today it
is ``NoRollups``, which computes nothing. Each pass runs in a worker thread,
one at a time, after the first pass at startup. A failed pass is logged and
retried at the next interval; it never stops collection, because the spool
keeps every observation until it is stored (invariant 8).

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
    """The M4 rollup job: complete 15-minute buckets before retention runs (E.10)."""

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


class NoRollups:
    """Placeholder until the M4 rollup job exists: computes nothing."""

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


ROLLUP_HOOK: RollupHook = NoRollups()
"""Where the M4 rollup job plugs in (RET-004); it runs hourly before retention."""


class TelemetryUpkeep:
    """Runs the staging move and the hourly rollup-and-retention passes.

    Attributes:
        passes: Completed passes by kind (``move``, ``retention``).
        failures: Failed passes by kind.
    """

    def __init__(
        self,
        telemetry: MaintainedTelemetry,
        clock: Clock,
        timings: ServiceTimings,
        rollups: RollupHook | None = None,
    ) -> None:
        """Bind the store.

        Args:
            telemetry: The telemetry backend.
            clock: Monotonic and UTC time.
            timings: Cadences (E.21, E.27).
            rollups: The rollup job; ``ROLLUP_HOOK`` by default.

        Returns:
            None

        Raises:
            None
        """
        self._telemetry, self._clock, self._timings = telemetry, clock, timings
        self._rollups = ROLLUP_HOOK if rollups is None else rollups
        self._stop = asyncio.Event()
        self.passes = {"move": 0, "retention": 0}
        self.failures = {"move": 0, "retention": 0}

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
        due = {"move": clock.monotonic(), "retention": clock.monotonic()}
        every = {"move": timings.maintenance_interval_s, "retention": timings.retention_interval_s}
        while not self._stop.is_set():
            for kind in ("move", "retention"):
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
            kind: ``move`` or ``retention``.

        Returns:
            None

        Raises:
            None
        """
        now = self._clock.now_utc()
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

    def _work(self, kind: str, now: datetime) -> None:
        """Do one pass (blocking; worker thread only).

        Args:
            kind: ``move`` or ``retention``.
            now: Reference time (UTC).

        Returns:
            None

        Raises:
            StorageError: If the database is unavailable.
        """
        if kind == "move":
            self._telemetry.maintain(now)
            return
        self._rollups.run(now)
        report = self._telemetry.retention.apply(now)
        _LOG.debug("retention pass: %s", report, extra={"event_id": "telemetry.retention"})
