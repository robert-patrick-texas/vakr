"""The ranking projector job: rebuild after a start, bounds, hold-back, logs, upkeep (E.28).

Dependencies:
    pytest; vakr.app.collector.ranking_projector, maintenance; the memory backend.

Example:
    $ uv run pytest tests/unit/app/collector/test_ranking_projector.py
"""

import asyncio
import logging
from datetime import datetime, timedelta

import pytest

from tests.contract import contract_samples as s
from vakr.adapters.memory import MemoryTelemetryStore
from vakr.adapters.memory.telemetry import MemoryTelemetry
from vakr.app.collector.maintenance import TelemetryUpkeep
from vakr.app.collector.ranking_projector import RankingProjector
from vakr.app.collector.timings import ServiceTimings
from vakr.domain.ids import BatchId, CollectorId, InterfaceId, new_id
from vakr.domain.series import TimeWindow
from vakr.domain.timestamps import EPOCH
from vakr.ports.clock import ManualClock

NOW = s.T0 + timedelta(days=2)
BUCKET = timedelta(minutes=15)


class Ticks:
    """A monotonic clock that advances ``step`` seconds per read."""

    def __init__(self, step: float) -> None:
        """Start at zero.

        Args:
            step: Seconds per read.

        Returns:
            None

        Raises:
            None
        """
        self.now, self.step = 0.0, step

    def __call__(self) -> float:
        """Read and advance.

        Args:
            None

        Returns:
            float: Seconds.

        Raises:
            None
        """
        self.now += self.step
        return self.now


def backend() -> MemoryTelemetry:
    """Return a memory backend holding one sample a minute for the last 26 hours.

    Args:
        None

    Returns:
        MemoryTelemetry: The backend.

    Raises:
        None
    """
    telemetry = MemoryTelemetry(MemoryTelemetryStore())
    dev, iface, col = s.device().id, new_id(InterfaceId), new_id(CollectorId)
    first = NOW - timedelta(hours=26)
    rows = [s.sample(dev, iface, col, first + m * s.MINUTE) for m in range(26 * 60)]
    telemetry.writer.write_batch(new_id(BatchId), rows)
    return telemetry


def test_e28_first_pass_rebuilds_then_passes_follow_new_buckets() -> None:
    """E.28: a start forgets and rebuilds, newest first; later passes add complete buckets.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    telemetry = backend()
    proj = telemetry.ranking_projection
    proj.project(NOW - timedelta(hours=3))
    job = RankingProjector(proj)
    first = job.run_pass(NOW)
    assert (first.forgot, first.projected, first.partials, first.remaining) == (1, 98, 98, 0)
    assert first.stopped == "caught_up"
    window = TimeWindow(NOW - timedelta(hours=24, minutes=5), NOW - timedelta(minutes=15))
    assert proj.unprojected(window) == ()
    again = job.run_pass(NOW)
    assert (again.forgot, again.projected, again.stopped) == (0, 0, "caught_up")
    later = job.run_pass(NOW + BUCKET)
    assert (later.projected, later.remaining) == (1, 0)
    assert proj.unprojected(TimeWindow(NOW - 2 * BUCKET, NOW)) == ()


def test_e28_passes_stop_at_their_bounds_and_the_spool_holds_back() -> None:
    """E.28, E.10: bucket and time bounds stop a pass; unstored observations hold buckets back.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    proj = backend().ranking_projection
    limited = RankingProjector(proj, max_buckets=3).run_pass(NOW)
    assert (limited.projected, limited.remaining, limited.stopped) == (3, 95, "bucket_limit")
    assert proj.unprojected(TimeWindow(NOW - 5 * BUCKET, NOW - BUCKET)) == (NOW - 5 * BUCKET,)
    timed = RankingProjector(proj, monotonic=Ticks(10.0), budget_s=25.0).run_pass(NOW)
    assert (timed.projected, timed.stopped) == (2, "time_budget")
    held = RankingProjector(proj, pending=lambda: NOW - timedelta(hours=2)).run_pass(NOW)
    assert held.remaining == 0
    assert proj.unprojected(TimeWindow(NOW - timedelta(hours=2), NOW))
    blocked = RankingProjector(proj, pending=lambda: EPOCH).run_pass(NOW)
    assert (blocked.projected, blocked.stopped) == (0, "caught_up")


def test_e28_passes_are_logged_and_run_with_the_staging_move(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """E.28: passes log in operator words; upkeep runs the projector as its own pass kind.

    Args:
        caplog: Captures the log lines.

    Returns:
        None

    Raises:
        None
    """
    telemetry = backend()
    job = RankingProjector(telemetry.ranking_projection, max_buckets=2)
    with caplog.at_level(logging.INFO, logger="vakr.app.collector.ranking_projector"):
        assert job.run(NOW) == 2
    events = [getattr(r, "event_id", "") for r in caplog.records]
    assert events == ["telemetry.ranking_projection", "telemetry.ranking_projection_behind"]
    assert "still read from native samples" in caplog.records[0].getMessage()
    assert job.last is not None
    assert job.last.remaining == 96
    upkeep = TelemetryUpkeep(telemetry, ManualClock(NOW), ServiceTimings(), None, job)  # ty: ignore[invalid-argument-type]
    asyncio.run(upkeep._pass("projection"))
    assert (upkeep.passes["projection"], upkeep.failures["projection"]) == (1, 0)
    assert job.last.projected == 2


def test_e28_a_failing_projection_pass_is_counted_not_raised() -> None:
    """E.28: a projector failure never stops collection; upkeep counts it and moves on.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """

    class Broken:
        """A projector whose store is gone."""

        def run(self, now: datetime) -> int:
            """Fail.

            Args:
                now: Reference time.

            Returns:
                int: Never.

            Raises:
                OSError: Always.
            """
            raise OSError(now.isoformat())

    upkeep = TelemetryUpkeep(backend(), ManualClock(NOW), ServiceTimings(), None, Broken())  # ty: ignore[invalid-argument-type]
    asyncio.run(upkeep._pass("projection"))
    assert (upkeep.passes["projection"], upkeep.failures["projection"]) == (0, 1)
