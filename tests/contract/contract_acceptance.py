"""The acceptance reports' lean reads behave the same on every backend (E.35, DB-004).

Subclass ``AcceptanceContract`` in an adapter's test module; the adapter's
``conftest.py`` supplies ``telemetry_factory``. The reports (MVP-ACC-01,
MVP-ACC-02, MVP-ACC-10) judge a node from these marks, so every backend
must order, reduce, bound, and refuse requests identically.

Dependencies:
    pytest; tests.contract.contract_samples; vakr.ports.telemetry.acceptance.

Example:
    $ uv run pytest tests/contract -k Acceptance
"""

from datetime import timedelta
from typing import cast

import pytest

from tests.contract import contract_samples as s
from vakr.domain.enums import BaselineState, IcmpState, ObservationType, PollExecution
from vakr.domain.errors import ValidationError
from vakr.domain.ids import BatchId, CollectorId, DeviceId, InterfaceId, new_id
from vakr.domain.quality import Quality
from vakr.domain.rollups import ROLLUP_BUCKET_SECONDS, RollupBucket
from vakr.domain.series import Metric, TimeWindow
from vakr.domain.timestamps import to_epoch_us
from vakr.ports.telemetry import TelemetryBackendFactory
from vakr.ports.telemetry.acceptance import MAX_MARKS, AcceptanceBackend

DAY = TimeWindow(s.T0, s.T0 + timedelta(days=1))
IFACE = ObservationType.INTERFACE


def backend_of(factory: TelemetryBackendFactory) -> AcceptanceBackend:
    """Open a view and confirm it offers the acceptance port.

    Args:
        factory: Opens views of one telemetry store.

    Returns:
        AcceptanceBackend: The view.

    Raises:
        None
    """
    backend = factory()
    assert hasattr(backend, "acceptance")
    return cast(AcceptanceBackend, backend)


def bucket(subject, col, metric: Metric, start, samples: int, valid: int, quality=Quality.NONE):
    """Build a 15-minute rollup bucket.

    Args:
        subject: Interface or device.
        col: Collector.
        metric: The metric.
        start: Aligned bucket start.
        samples: Sample count.
        valid: Valid count.
        quality: Flags.

    Returns:
        RollupBucket: The bucket.

    Raises:
        None
    """
    value = 5.0 if valid else None
    return RollupBucket(subject, col, metric, start, ROLLUP_BUCKET_SECONDS, 15, samples, valid,
                        value, value, value, value, quality)  # fmt: skip


class AcceptanceContract:
    """``AcceptanceQuery`` behaviour for any ``TelemetryBackend`` that offers it."""

    def test_e35_poll_marks_keep_times_order_and_duplicates(
        self, telemetry_factory: TelemetryBackendFactory
    ) -> None:
        """E.35, MVP-ACC-01: polls come back by scheduled time with exact microsecond times.

        Args:
            telemetry_factory: Opens views of one telemetry store.

        Returns:
            None

        Raises:
            None
        """
        backend = backend_of(telemetry_factory)
        dev, col, other = s.device().id, new_id(CollectorId), new_id(CollectorId)
        late = s.T0 + timedelta(minutes=1)
        rows = [
            s.poll(dev, col, late, started_at=late + timedelta(microseconds=6_250_001),
                   completed_at=late + timedelta(seconds=9)),
            s.poll(dev, col, s.T0), s.poll(dev, col, s.T0),
            s.poll(dev, col, s.T0 + timedelta(minutes=2), execution=PollExecution.NOT_ATTEMPTED,
                   quality=Quality.NOT_ATTEMPTED, started_at=None, completed_at=None),
            s.poll(dev, other, s.T0),
        ]  # fmt: skip
        backend.writer.write_batch(new_id(BatchId), rows)
        marks = backend_of(telemetry_factory).acceptance.poll_marks(dev, col, DAY)
        assert not marks.truncated
        t0 = to_epoch_us(s.T0)
        assert [m.scheduled_us - t0 for m in marks.items] == [0, 0, 60_000_000, 120_000_000]
        third = marks.items[2]
        assert third.started_us is not None
        assert (third.started_us - third.scheduled_us, third.duration_us) == (6_250_001, 2_749_999)
        missed = marks.items[3]
        assert (missed.execution, missed.started_us, missed.completed_us) == (
            PollExecution.NOT_ATTEMPTED, None, None)  # fmt: skip
        assert missed.quality == Quality.NOT_ATTEMPTED
        assert missed.interval_s == 60
        capped = backend.acceptance.poll_marks(dev, col, DAY, limit=3)
        assert capped.truncated
        assert len(capped.items) == 3
        assert backend.acceptance.poll_marks(new_id(DeviceId), col, DAY).items == ()

    def test_e35_sample_marks_report_presence_never_zero(
        self, telemetry_factory: TelemetryBackendFactory
    ) -> None:
        """Invariant 1: a missing rate is a cleared presence bit, not a value.

        Args:
            telemetry_factory: Opens views of one telemetry store.

        Returns:
            None

        Raises:
            None
        """
        backend = backend_of(telemetry_factory)
        dev, col, iface = s.device().id, new_id(CollectorId), new_id(InterfaceId)
        reset = Quality.BASELINE_UPTIME_RESET
        rows = [
            s.sample(dev, iface, col, s.T0, in_pps=3.0, out_pps=4.0, in_error_rate=0.0),
            s.sample(dev, iface, col, s.T0 + s.MINUTE, in_bps=None, out_bps=None, quality=reset,
                     baseline_state=BaselineState.INVALIDATED),
            s.icmp(dev, col, s.T0),
            s.icmp(dev, col, s.T0 + s.MINUTE, median_rtt_us=None, jitter_us=None,
                   min_rtt_us=None, avg_rtt_us=None, max_rtt_us=None,
                   rtts_us=(None,) * 5, state=IcmpState.UNREACHABLE, quality=Quality.NONE),
        ]  # fmt: skip
        backend.writer.write_batch(new_id(BatchId), rows)
        query = backend_of(telemetry_factory).acceptance
        marks = query.sample_marks(IFACE, iface, col, DAY)
        assert [(m.ts_us - to_epoch_us(s.T0), m.available) for m in marks.items] == [
            (0, 0b011111), (60_000_000, 0)]  # fmt: skip
        assert marks.items[1].quality == reset
        newest = query.sample_marks(IFACE, iface, col, DAY, newest_first=True, limit=1)
        assert newest.truncated
        assert newest.items[0].quality == reset
        icmp = query.sample_marks(ObservationType.ICMP, dev, col, DAY)
        assert [m.available for m in icmp.items] == [0b111, 0b010]

    def test_e35_bucket_marks_merge_metrics_of_one_bucket(
        self, telemetry_factory: TelemetryBackendFactory
    ) -> None:
        """E.35, RET-006: a bucket's marks hold the largest count, presence, and flag union.

        Args:
            telemetry_factory: Opens views of one telemetry store.

        Returns:
            None

        Raises:
            None
        """
        backend = backend_of(telemetry_factory)
        if not backend.capabilities.rollups:
            with pytest.raises(NotImplementedError):
                backend.acceptance.bucket_marks(IFACE, new_id(InterfaceId), new_id(CollectorId),
                                                DAY)  # fmt: skip
            return
        iface, col = new_id(InterfaceId), new_id(CollectorId)
        second = s.T0 + timedelta(minutes=15)
        flag = Quality.BASELINE_DISCONTINUITY
        backend.rollups.write([
            bucket(iface, col, Metric.IN_BPS, s.T0, 15, 15),
            bucket(iface, col, Metric.IN_PPS, s.T0, 15, 0),
            bucket(iface, col, Metric.IN_BPS, second, 14, 13, flag),
            bucket(iface, col, Metric.OUT_ERRORS, second, 14, 14, flag),
            bucket(iface, new_id(CollectorId), Metric.IN_BPS, second, 15, 15),
        ])  # fmt: skip
        query = backend_of(telemetry_factory).acceptance
        marks = query.bucket_marks(IFACE, iface, col, DAY)
        t0 = to_epoch_us(s.T0)
        assert [(m.start_us - t0, m.samples, m.available, m.quality) for m in marks.items] == [
            (0, 15, 0b000001, Quality.NONE), (900_000_000, 14, 0b100001, flag)]  # fmt: skip
        newest = query.bucket_marks(IFACE, iface, col, DAY, newest_first=True, limit=1)
        assert newest.truncated
        assert newest.items[0].start_us == t0 + 900_000_000
        later = TimeWindow(second, DAY.end)
        assert [m.start_us for m in query.bucket_marks(IFACE, iface, col, later).items] == [
            t0 + 900_000_000
        ]

    def test_e35_requests_are_bounded_and_typed(
        self, telemetry_factory: TelemetryBackendFactory
    ) -> None:
        """E.35 bounds: limits outside 1..MAX_MARKS and other types are refused.

        Args:
            telemetry_factory: Opens views of one telemetry store.

        Returns:
            None

        Raises:
            None
        """
        query = backend_of(telemetry_factory).acceptance
        dev, col = s.device().id, new_id(CollectorId)
        for limit in (0, MAX_MARKS + 1):
            with pytest.raises(ValidationError):
                query.poll_marks(dev, col, DAY, limit=limit)
            with pytest.raises(ValidationError):
                query.sample_marks(IFACE, dev, col, DAY, limit=limit)
        with pytest.raises(ValidationError):
            query.sample_marks(ObservationType.SNMP_DEVICE, dev, col, DAY)
