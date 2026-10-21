"""The rollup job in the collector: spool hold-back, completeness, pass logs (E.10, RET-009).

Dependencies:
    pytest; vakr.app.collector.rollup_settings; vakr.ingest.

Example:
    $ uv run pytest tests/unit/app/collector/test_rollup_wiring.py
"""

import asyncio
import logging
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

from tests.contract import contract_samples as s
from vakr.app.collector.maintenance import TelemetryUpkeep
from vakr.app.collector.rollup_settings import (
    SETTLE,
    RollupPass,
    complete_end,
    log_pass,
    spool_pending,
)
from vakr.app.collector.timings import ServiceTimings
from vakr.domain.errors import StorageError
from vakr.domain.timestamps import EPOCH, to_epoch_us
from vakr.ingest.ingest_steps import IngestStatus
from vakr.ports.clock import ManualClock


def ingester(status: IngestStatus, durable: int) -> SimpleNamespace:
    """Build the two attributes ``spool_pending`` reads.

    Args:
        status: The ingester's counters.
        durable: The spool's highest durable sequence number.

    Returns:
        SimpleNamespace: A stand-in ingester.

    Raises:
        None
    """
    return SimpleNamespace(status=status, spool=SimpleNamespace(durable_seq=durable))


def test_e10_spool_backlog_holds_buckets_back() -> None:
    """E.10, RET-009: unstored observations keep their buckets from being rolled up.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    waiting = IngestStatus(acked_seq=5, oldest_unacked_us=to_epoch_us(s.T0))
    assert spool_pending(ingester(waiting, 9))() == s.T0  # ty: ignore[invalid-argument-type]
    unknown = IngestStatus(acked_seq=5)
    assert spool_pending(ingester(unknown, 9))() == EPOCH  # ty: ignore[invalid-argument-type]
    steady = IngestStatus(acked_seq=5, last_write_us=to_epoch_us(s.T0))
    assert spool_pending(ingester(steady, 9))() is None  # ty: ignore[invalid-argument-type]
    assert spool_pending(ingester(unknown, 5))() is None  # ty: ignore[invalid-argument-type]


def test_e10_a_bucket_is_complete_more_than_ten_minutes_after_its_end() -> None:
    """E.10: "more than 10 minutes in the past", and before the oldest waiting record.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    end = s.T0 + timedelta(minutes=15)
    assert complete_end(end + SETTLE, None, SETTLE) == s.T0
    assert complete_end(end + SETTLE + timedelta(microseconds=1), None, SETTLE) == end
    assert complete_end(end + timedelta(hours=5), end + SETTLE, SETTLE) == s.T0


def test_ret009_passes_report_thin_buckets_and_backlog(caplog: pytest.LogCaptureFixture) -> None:
    """RET-009: each pass logs progress and thin buckets; a bounded pass says it continues.

    Args:
        caplog: Captures log records.

    Returns:
        None

    Raises:
        None
    """
    frontier = s.T0 + timedelta(days=1)
    with caplog.at_level(logging.INFO, logger="vakr.app.collector.rollup_job"):
        log_pass(RollupPass(96, 2880, 3, frontier, frontier + timedelta(days=2), "bucket_limit"))
        log_pass(RollupPass(0, 0, 0, None, s.T0, "caught_up"))
    events = [getattr(r, "event_id", None) for r in caplog.records]
    assert events == ["telemetry.rollups", "telemetry.rollups_behind"]
    assert "3 subject bucket(s) held fewer samples" in caplog.records[0].getMessage()
    assert "48.0 h of complete buckets remain" in caplog.records[1].getMessage()
    assert "bucket limit" in caplog.records[1].getMessage()


class BrokenRollups:
    """A rollup hook that always fails, like a damaged rollup partition."""

    def run(self, now: datetime) -> int:
        """Fail.

        Args:
            now: Reference time.

        Returns:
            int: Never.

        Raises:
            StorageError: Always.
        """
        raise StorageError(f"rollup partition damaged at {now:%H:%M}", retryable=False)


class Retention:
    """Records retention passes; doubles as the telemetry and its retention port.

    Attributes:
        applied: Reference times of the retention passes.
    """

    def __init__(self) -> None:
        """Start with no passes.

        Args:
            None

        Returns:
            None

        Raises:
            None
        """
        self.applied: list[datetime] = []
        self.retention = self

    def apply(self, now: datetime) -> str:
        """Record a pass.

        Args:
            now: Reference time.

        Returns:
            str: A report stand-in.

        Raises:
            None
        """
        self.applied.append(now)
        return "ok"


def test_ret007_a_failing_rollup_pass_still_lets_retention_run() -> None:
    """RET-007, ROLL-T04: retention only expires covered data, so it runs even then.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    telemetry = Retention()
    upkeep = TelemetryUpkeep(telemetry, ManualClock(s.T0), ServiceTimings(), BrokenRollups())  # ty: ignore[invalid-argument-type]
    asyncio.run(upkeep._pass("retention"))
    assert telemetry.applied == [s.T0]
    assert (upkeep.passes["retention"], upkeep.failures["retention"]) == (0, 1)
