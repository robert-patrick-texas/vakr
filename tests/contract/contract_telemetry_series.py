"""Series, rollup, and retention semantics: STORE-012, API-006, STATE-004/005, DB-006.

Subclass ``TelemetrySeriesContract`` in an adapter's test module; the
adapter's ``conftest.py`` supplies ``telemetry_factory``. Tests that need
rollups read ``capabilities.rollups`` and skip with "not applicable" when the
backend has none yet (5.2 rule 5).

Dependencies:
    pytest; tests.contract.contract_samples; vakr.ports.telemetry.

Example:
    $ uv run pytest tests/contract -k TelemetrySeries
"""

from datetime import datetime, timedelta

import pytest

from tests.contract import contract_samples as s
from vakr.domain.enums import BaselineState, IcmpState, SeriesSource
from vakr.domain.errors import ValidationError
from vakr.domain.ids import BatchId, CollectorId, InterfaceId, new_id
from vakr.domain.quality import Quality
from vakr.domain.rollups import ROLLUP_BUCKET_SECONDS
from vakr.domain.series import MAX_SERIES_POINTS, Metric, SeriesResolution, TimeWindow
from vakr.domain.state import RollupBucket
from vakr.ports.telemetry import TelemetryBackendFactory

HOUR = timedelta(hours=1)
BUCKET = timedelta(seconds=ROLLUP_BUCKET_SECONDS)


def _bucket(iface: InterfaceId, col: CollectorId, start: datetime, base: float) -> RollupBucket:
    """Build a full in_bps bucket whose aggregates derive from ``base``.

    Args:
        iface: Interface UUID.
        col: Collector UUID.
        start: Aligned bucket start.
        base: Minimum value; the others are offsets from it.

    Returns:
        RollupBucket: The bucket.

    Raises:
        None
    """
    return RollupBucket(iface, col, Metric.IN_BPS, start, ROLLUP_BUCKET_SECONDS, 15, 15, 15,
                        minimum=base, mean=base + 7, p95=base + 13, maximum=base + 14)  # fmt: skip


class TelemetrySeriesContract:
    """Series and retention behaviour for any ``TelemetryBackend``."""

    def test_store012_native_series_keeps_gaps_and_quality(
        self, telemetry_factory: TelemetryBackendFactory
    ) -> None:
        """STORE-012, invariants 1 and 5: ``None`` stays a gap, ``0.0`` stays a value, flags stay.

        Args:
            telemetry_factory: Opens views of one telemetry store.

        Returns:
            None

        Raises:
            None
        """
        dev, iface, col = s.device().id, new_id(InterfaceId), new_id(CollectorId)
        first = Quality.BASELINE_FIRST_SAMPLE
        gap = Quality.BASELINE_GAP
        rows = [
            s.sample(dev, iface, col, s.T0, baseline_state=BaselineState.NO_BASELINE, quality=first,
                     in_bps=None, out_bps=None),
            s.sample(dev, iface, col, s.T0 + s.MINUTE, in_bps=1000.0),
            s.sample(dev, iface, col, s.T0 + 3 * s.MINUTE, baseline_state=BaselineState.INVALIDATED,
                     quality=gap, in_bps=None, out_bps=None),
            s.sample(dev, iface, col, s.T0 + 10 * s.MINUTE, in_bps=0.0,
                     quality=Quality.RATE_EXCEEDS_CAPACITY),
        ]  # fmt: skip
        backend = telemetry_factory()
        backend.writer.write_batch(new_id(BatchId), rows[::-1])
        window = TimeWindow(s.T0, s.T0 + HOUR)
        series = telemetry_factory().query.interface_series(iface, col, Metric.IN_BPS, window)
        assert [(p.timestamp, p.value, p.quality) for p in series.points] == [
            (s.T0, None, first),
            (s.T0 + s.MINUTE, 1000.0, Quality.NONE),
            (s.T0 + 3 * s.MINUTE, None, gap),
            (s.T0 + 10 * s.MINUTE, 0.0, Quality.RATE_EXCEEDS_CAPACITY),
        ]
        assert series.resolutions == (SeriesResolution(SeriesSource.NATIVE, s.T0, s.T0 + HOUR),)
        assert (series.subject_id, series.collector_id, series.window) == (iface, col, window)
        assert not series.truncated

    def test_api006_point_cap_and_metric_family(
        self, telemetry_factory: TelemetryBackendFactory
    ) -> None:
        """API-006, 10.6: ``max_points`` truncates oldest first, disclosed; families are checked.

        Args:
            telemetry_factory: Opens views of one telemetry store.

        Returns:
            None

        Raises:
            None
        """
        dev, iface, col = s.device().id, new_id(InterfaceId), new_id(CollectorId)
        rows = [s.sample(dev, iface, col, s.T0 + m * s.MINUTE, in_bps=float(m)) for m in range(10)]
        backend = telemetry_factory()
        backend.writer.write_batch(new_id(BatchId), rows)
        window = TimeWindow(s.T0, s.T0 + HOUR)
        capped = backend.query.interface_series(iface, col, Metric.IN_BPS, window, max_points=4)
        exact = backend.query.interface_series(iface, col, Metric.IN_BPS, window, max_points=10)
        assert [p.value for p in capped.points] == [0.0, 1.0, 2.0, 3.0]
        assert capped.truncated
        assert len(exact.points) == 10
        assert not exact.truncated
        for bad in (0, MAX_SERIES_POINTS + 1):
            with pytest.raises(ValidationError):
                backend.query.interface_series(iface, col, Metric.IN_BPS, window, max_points=bad)
        with pytest.raises(ValidationError):
            backend.query.interface_series(iface, col, Metric.LOSS_PCT, window)
        with pytest.raises(ValidationError):
            backend.query.icmp_series(dev, col, Metric.IN_BPS, window)

    def test_state004_series_are_per_collector_and_unique(
        self, telemetry_factory: TelemetryBackendFactory
    ) -> None:
        """STATE-004, DB-004: collectors never mix; equal timestamps keep the larger UUID.

        Args:
            telemetry_factory: Opens views of one telemetry store.

        Returns:
            None

        Raises:
            None
        """
        dev, iface = s.device().id, new_id(InterfaceId)
        east, west = new_id(CollectorId), new_id(CollectorId)
        twins = [s.sample(dev, iface, east, s.T0, in_bps=float(v)) for v in (1, 2)]
        winner = max(twins, key=lambda o: o.observation_id)
        rows = [*twins, s.sample(dev, iface, west, s.T0 + s.MINUTE, in_bps=9.0)]
        backend = telemetry_factory()
        backend.writer.write_batch(new_id(BatchId), rows)
        window = TimeWindow(s.T0, s.T0 + HOUR)
        east_series = backend.query.interface_series(iface, east, Metric.IN_BPS, window)
        west_series = backend.query.interface_series(iface, west, Metric.IN_BPS, window)
        assert [p.value for p in east_series.points] == [winner.in_bps]
        assert [p.value for p in west_series.points] == [9.0]

    def test_icmp_series_metrics(self, telemetry_factory: TelemetryBackendFactory) -> None:
        """ICMP-005, RET-008: RTT is ``None`` when nothing answered; loss comes from counts.

        Args:
            telemetry_factory: Opens views of one telemetry store.

        Returns:
            None

        Raises:
            None
        """
        dev, col = s.device().id, new_id(CollectorId)
        lost = s.icmp(dev, col, s.T0 + s.MINUTE, rtts_us=(None,) * 5, state=IcmpState.UNREACHABLE,
                      min_rtt_us=None, avg_rtt_us=None, median_rtt_us=None, max_rtt_us=None,
                      jitter_us=None)  # fmt: skip
        backend = telemetry_factory()
        backend.writer.write_batch(new_id(BatchId), [s.icmp(dev, col, s.T0), lost])
        window = TimeWindow(s.T0, s.T0 + HOUR)

        def values(metric: Metric) -> list[float | None]:
            """Return the point values of one ICMP metric.

            Args:
                metric: The metric.

            Returns:
                list[float | None]: Values in time order.

            Raises:
                None
            """
            return [p.value for p in backend.query.icmp_series(dev, col, metric, window).points]

        assert values(Metric.RTT_MEDIAN_US) == [1000.0, None]
        assert values(Metric.LOSS_PCT) == [20.0, 100.0]
        assert values(Metric.JITTER_US) == [100.0, None]

    def test_db006_native_rollup_boundary(self, telemetry_factory: TelemetryBackendFactory) -> None:
        """DB-006, STATE-005, STORE-012: ordered, unique points with disclosed resolution spans.

        Args:
            telemetry_factory: Opens views of one telemetry store.

        Returns:
            None

        Raises:
            None
        """
        backend = telemetry_factory()
        if not backend.capabilities.rollups:
            pytest.skip("DB-006 not applicable: this backend declares rollups=False")
        dev, iface, col = s.device().id, new_id(InterfaceId), new_id(CollectorId)
        native = [
            s.sample(dev, iface, col, s.T0 + m * s.MINUTE, in_bps=float(m)) for m in range(120)
        ]
        backend.writer.write_batch(new_id(BatchId), native)
        # Bucket 2 is never written: it must appear as a gap, not vanish.
        backend.rollups.write(
            [_bucket(iface, col, s.T0 + k * BUCKET, 15.0 * k) for k in range(8) if k != 2]
        )
        report = backend.retention.apply(s.T0 + HOUR + backend.retention.policy.native)
        assert (report.native_cutoff, report.native_removed) == (s.T0 + HOUR, 60)
        assert backend.retention.native_horizon() == s.T0 + HOUR
        series = telemetry_factory().query.interface_series(
            iface, col, Metric.IN_BPS, TimeWindow(s.T0, s.T0 + 2 * HOUR)
        )
        times = [p.timestamp for p in series.points]
        assert times == sorted(set(times))
        head, tail = series.points[:4], series.points[4:]
        assert [p.timestamp for p in head] == [s.T0 + k * BUCKET for k in range(4)]
        assert (head[0].value, head[0].minimum, head[0].maximum) == (7.0, 0.0, 14.0)
        assert head[2].value is None
        assert head[2].minimum is None
        assert [p.timestamp for p in tail] == [s.T0 + m * s.MINUTE for m in range(60, 120)]
        assert series.resolutions == (
            SeriesResolution(SeriesSource.ROLLUP, s.T0, s.T0 + HOUR, ROLLUP_BUCKET_SECONDS),
            SeriesResolution(SeriesSource.NATIVE, s.T0 + HOUR, s.T0 + 2 * HOUR),
        )
        old = backend.query.interface_series(
            iface, col, Metric.IN_BPS, TimeWindow(s.T0, s.T0 + 2 * BUCKET)
        )
        assert [p.value for p in old.points] == [7.0, 22.0]
        assert [r.source for r in old.resolutions] == [SeriesSource.ROLLUP]

    def test_rollup_repository_or_capability_marker(
        self, telemetry_factory: TelemetryBackendFactory
    ) -> None:
        """RET-009, 5.2 rule 5: buckets replace by identity, or the backend says it has none.

        Args:
            telemetry_factory: Opens views of one telemetry store.

        Returns:
            None

        Raises:
            None
        """
        backend = telemetry_factory()
        iface, col = new_id(InterfaceId), new_id(CollectorId)
        window = TimeWindow(s.T0, s.T0 + HOUR)
        if not backend.capabilities.rollups:
            with pytest.raises(NotImplementedError):
                backend.rollups.write([_bucket(iface, col, s.T0, 1.0)])
            with pytest.raises(NotImplementedError):
                backend.rollups.read(iface, col, Metric.IN_BPS, window, limit=10)
            with pytest.raises(NotImplementedError):
                backend.rollups.coverage_end()
            return
        assert backend.rollups.coverage_end() is None
        assert (
            backend.rollups.write(
                [_bucket(iface, col, s.T0 + BUCKET, 5.0), _bucket(iface, col, s.T0, 1.0)]
            )
            == 2
        )
        rerun = _bucket(iface, col, s.T0, 2.0)
        backend.rollups.write([rerun])
        stored = telemetry_factory().rollups.read(iface, col, Metric.IN_BPS, window, limit=10)
        assert [b.minimum for b in stored] == [2.0, 5.0]
        assert stored[0] == rerun
        assert backend.rollups.read(iface, col, Metric.IN_BPS, window, limit=1) == (rerun,)
        assert backend.rollups.read(iface, col, Metric.OUT_BPS, window, limit=10) == ()
        assert backend.rollups.coverage_end() == s.T0 + 2 * BUCKET
        with pytest.raises(ValidationError):
            backend.rollups.read(iface, col, Metric.IN_BPS, window, limit=0)

    def test_retention_follows_policy(self, telemetry_factory: TelemetryBackendFactory) -> None:
        """RET-007, E.10: native expires by policy, and never ahead of rollup coverage.

        Args:
            telemetry_factory: Opens views of one telemetry store.

        Returns:
            None

        Raises:
            None
        """
        backend = telemetry_factory()
        policy = backend.retention.policy
        dev, col = s.device().id, new_id(CollectorId)
        backend.writer.write_batch(
            new_id(BatchId), [s.icmp(dev, col, s.T0), s.icmp(dev, col, s.T0 + 2 * HOUR)]
        )
        now = s.T0 + HOUR + policy.native
        if backend.capabilities.rollups:
            waiting = backend.retention.apply(now)
            assert (waiting.native_cutoff, waiting.native_removed) == (None, 0)
            backend.rollups.write([_bucket(new_id(InterfaceId), col, s.T0, 1.0)])
        report = backend.retention.apply(now)
        expected_cutoff = s.T0 + BUCKET if backend.capabilities.rollups else s.T0 + HOUR
        assert (report.applied_at, report.native_cutoff, report.native_removed) == (
            now,
            expected_cutoff,
            1,
        )
        assert backend.retention.apply(now).native_removed == 0
        assert backend.retention.native_horizon() == expected_cutoff
        if backend.capabilities.rollups:
            aged = backend.retention.apply(s.T0 + BUCKET + policy.rollup)
            assert aged.rollups_removed == 1
            assert backend.rollups.coverage_end() is None
        else:
            assert report.rollup_cutoff is None
            assert report.rollups_removed == 0
        with pytest.raises(ValidationError):
            backend.retention.apply(now.replace(tzinfo=None))
