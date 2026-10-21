"""``HistoryQuery`` semantics: downsampled series and ranking summaries (M4; 10.6, E.17 #13).

Subclass ``TelemetryHistoryContract`` in an adapter's test module; its
``conftest.py`` supplies ``telemetry_factory``. Every backend must return
equal answers, so the memory reference and SQLite agree on bucket widths,
envelopes, gaps, forced sources, nearest-rank p95, error and packet sums,
collector separation, and bounds (DB-004, DB-006, STORE-012, MPOS-008,
API-006, SEC-019). Values are chosen so float sums are exact.

Dependencies:
    pytest; tests.contract.contract_samples; vakr.ports.telemetry.history.

Example:
    $ uv run pytest tests/contract -k TelemetryHistory
"""

from datetime import timedelta
from typing import cast

import pytest

from tests.contract import contract_samples as s
from vakr.domain.enums import BaselineState, SeriesSource
from vakr.domain.errors import ValidationError
from vakr.domain.ids import BatchId, CollectorId, InterfaceId, new_id
from vakr.domain.quality import Quality
from vakr.domain.rollups import ROLLUP_BUCKET_SECONDS
from vakr.domain.series import Metric, SeriesResolution, TimeWindow
from vakr.domain.state import RollupBucket
from vakr.logic.stats import p95
from vakr.ports.telemetry import TelemetryBackend, TelemetryBackendFactory
from vakr.ports.telemetry.history import HistoryBackend, HistoryQuery

HOUR = timedelta(hours=1)
DAY = timedelta(days=1)
BUCKET = timedelta(seconds=ROLLUP_BUCKET_SECONDS)


def history(backend: TelemetryBackend) -> HistoryQuery:
    """Return a backend's history port; every adapter here is a ``HistoryBackend``.

    Args:
        backend: The backend.

    Returns:
        HistoryQuery: Its ``history`` port.

    Raises:
        None
    """
    return cast(HistoryBackend, backend).history


def _bucket(iface: InterfaceId, col: CollectorId, k: int, base: float) -> RollupBucket:
    """Build a full in_bps bucket ``k`` buckets after ``T0``.

    Args:
        iface: Interface.
        col: Collector.
        k: Bucket number.
        base: Minimum; the other aggregates are offsets from it.

    Returns:
        RollupBucket: The bucket.

    Raises:
        None
    """
    return RollupBucket(
        iface,
        col,
        Metric.IN_BPS,
        s.T0 + k * BUCKET,
        ROLLUP_BUCKET_SECONDS,
        15,
        15,
        15,
        minimum=base,
        mean=base + 7,
        p95=base + 13,
        maximum=base + 14,
    )


class TelemetryHistoryContract:
    """Downsampling and ranking summaries for any ``HistoryBackend``."""

    def test_10_6_downsample_buckets_envelope_and_gaps(
        self, telemetry_factory: TelemetryBackendFactory
    ) -> None:
        """10.6, STORE-012: one point per 120 s bucket over a day, min/max kept, empty buckets None.

        Args:
            telemetry_factory: Opens views of one telemetry store.

        Returns:
            None

        Raises:
            None
        """
        dev, iface, col = s.device().id, new_id(InterfaceId), new_id(CollectorId)
        values = [10.0, 30.0, None, 50.0]
        rows = [
            s.sample(dev, iface, col, s.T0 + m * s.MINUTE, in_bps=v) for m, v in enumerate(values)
        ]
        rows[2] = s.sample(
            dev,
            iface,
            col,
            s.T0 + 2 * s.MINUTE,
            in_bps=None,
            out_bps=None,
            baseline_state=BaselineState.INVALIDATED,
            quality=Quality.BASELINE_UPTIME_RESET,
        )
        backend = telemetry_factory()
        backend.writer.write_batch(new_id(BatchId), rows)
        window = TimeWindow(s.T0, s.T0 + DAY)
        series = history(telemetry_factory()).downsampled_series(iface, col, Metric.IN_BPS, window)
        assert series.resolutions == (SeriesResolution(SeriesSource.NATIVE, s.T0, s.T0 + DAY, 120),)
        assert len(series.points) == 720
        first, second, third = series.points[:3]
        assert (first.value, first.minimum, first.maximum) == (20.0, 10.0, 30.0)
        assert (second.value, second.quality) == (50.0, Quality.BASELINE_UPTIME_RESET)
        assert (third.value, third.minimum, third.quality) == (None, None, Quality.NONE)
        assert not series.truncated
        month = TimeWindow(s.T0, s.T0 + 30 * DAY)  # no ladder rung fits 10 points: truncated
        capped = history(backend).downsampled_series(
            iface, col, Metric.IN_BPS, month, max_points=10
        )
        assert len(capped.points) == 10
        assert capped.truncated
        with pytest.raises(ValidationError):
            history(backend).downsampled_series(iface, col, Metric.IN_BPS, window, max_points=0)

    def test_10_6_forced_sources(self, telemetry_factory: TelemetryBackendFactory) -> None:
        """10.6 table: ``ROLLUP`` reads rollups for the whole window, ``NATIVE`` ignores them.

        Args:
            telemetry_factory: Opens views of one telemetry store.

        Returns:
            None

        Raises:
            None
        """
        backend = telemetry_factory()
        dev, iface, col = s.device().id, new_id(InterfaceId), new_id(CollectorId)
        window = TimeWindow(s.T0, s.T0 + HOUR)
        backend.writer.write_batch(new_id(BatchId), [s.sample(dev, iface, col, s.T0, in_bps=4.0)])
        if not backend.capabilities.rollups:
            with pytest.raises(ValidationError):
                history(backend).downsampled_series(
                    iface, col, Metric.IN_BPS, window, source=SeriesSource.ROLLUP
                )
            return
        backend.rollups.write([_bucket(iface, col, k, 10.0 * k) for k in (0, 1, 3)])
        rolled = history(backend).downsampled_series(
            iface, col, Metric.IN_BPS, window, source=SeriesSource.ROLLUP
        )
        assert rolled.resolutions == (
            SeriesResolution(SeriesSource.ROLLUP, s.T0, s.T0 + HOUR, ROLLUP_BUCKET_SECONDS),
        )
        assert [p.value for p in rolled.points] == [7.0, 17.0, None, 37.0]
        assert (rolled.points[3].minimum, rolled.points[3].maximum) == (30.0, 44.0)
        native = history(backend).downsampled_series(
            iface, col, Metric.IN_BPS, window, source=SeriesSource.NATIVE
        )
        assert [r.source for r in native.resolutions] == [SeriesSource.NATIVE]
        assert native.points[0].value == 4.0

    def test_mpos008_collectors_are_never_merged(
        self, telemetry_factory: TelemetryBackendFactory
    ) -> None:
        """MPOS-008, STATE-004: each collector's buckets and summaries hold only its own samples.

        Args:
            telemetry_factory: Opens views of one telemetry store.

        Returns:
            None

        Raises:
            None
        """
        dev, iface = s.device().id, new_id(InterfaceId)
        east, west = new_id(CollectorId), new_id(CollectorId)
        rows = [
            s.sample(dev, iface, east, s.T0, in_bps=100.0, out_bps=1.0),
            s.sample(dev, iface, west, s.T0, in_bps=900.0, out_bps=9.0),
        ]
        backend = telemetry_factory()
        backend.writer.write_batch(new_id(BatchId), rows)
        window = TimeWindow(s.T0, s.T0 + HOUR)
        h = history(backend)
        assert h.downsampled_series(iface, east, Metric.IN_BPS, window).points[0].value == 100.0
        assert h.downsampled_series(iface, west, Metric.IN_BPS, window).points[0].value == 900.0
        east_scan = h.window_summaries(east, window)
        assert [(x.in_p95_bps, x.out_p95_bps, x.samples) for x in east_scan.items] == [
            (100.0, 1.0, 1)
        ]

    def test_e17_13_nearest_rank_p95_and_error_sums(
        self, telemetry_factory: TelemetryBackendFactory
    ) -> None:
        """E.17 #13: directional nearest-rank p95; valid error deltas; packets only when all known.

        Args:
            telemetry_factory: Opens views of one telemetry store.

        Returns:
            None

        Raises:
            None
        """
        dev, col = s.device().id, new_id(CollectorId)
        busy, quiet = sorted((new_id(InterfaceId), new_id(InterfaceId)), key=str)
        ins = [
            float(v)
            for v in (5, 1, 20, 3, 17, 9, 11, 2, 8, 14, 6, 19, 4, 13, 7, 16, 12, 10, 15, 18)
        ]
        rows = [
            s.sample(
                dev,
                busy,
                col,
                s.T0 + m * s.MINUTE,
                in_bps=v,
                out_bps=2.0 * m,
                in_error_delta=m % 3,
                out_error_delta=1,
                in_pps=10.0,
                out_pps=5.0,
            )
            for m, v in enumerate(ins)
        ]
        rows.append(
            s.sample(
                dev,
                busy,
                col,
                s.T0 + 20 * s.MINUTE,
                in_bps=None,
                out_bps=None,
                baseline_state=BaselineState.NO_BASELINE,
                quality=Quality.BASELINE_FIRST_SAMPLE,
            )
        )
        rows += [
            s.sample(
                dev,
                quiet,
                col,
                s.T0 + m * s.MINUTE,
                in_bps=1.0,
                out_bps=1.0,
                in_error_delta=0,
                out_error_delta=0,
                in_pps=None if m == 1 else 1.0,
                out_pps=1.0,
            )
            for m in range(3)
        ]
        rows.append(s.sample(dev, quiet, col, s.T0 + 2 * HOUR, in_bps=1e9))  # outside the window
        backend = telemetry_factory()
        backend.writer.write_batch(new_id(BatchId), rows)
        scan = history(telemetry_factory()).window_summaries(col, TimeWindow(s.T0, s.T0 + HOUR))
        assert not scan.truncated
        first, second = scan.items
        assert (first.interface_id, second.interface_id) == (busy, quiet)
        assert (first.samples, first.in_valid, first.out_valid) == (21, 20, 20)
        assert (first.in_p95_bps, first.out_p95_bps) == (p95(ins), 36.0)
        assert first.in_p95_bps == 19.0
        assert (first.error_samples, first.in_errors, first.out_errors) == (20, 19, 20)
        assert first.packets == 20 * 15 * 60
        assert (second.error_samples, second.in_errors, second.packets) == (3, 0, None)
        assert second.in_p95_bps == 1.0

    def test_sec019_ranking_bounds(self, telemetry_factory: TelemetryBackendFactory) -> None:
        """SEC-019, E.28: windows over one day are refused; ``limit`` truncates in UUID order.

        Args:
            telemetry_factory: Opens views of one telemetry store.

        Returns:
            None

        Raises:
            None
        """
        dev, col = s.device().id, new_id(CollectorId)
        ifaces = sorted((new_id(InterfaceId) for _ in range(3)), key=str)
        backend = telemetry_factory()
        backend.writer.write_batch(new_id(BatchId), [s.sample(dev, i, col, s.T0) for i in ifaces])
        h = history(backend)
        window = TimeWindow(s.T0, s.T0 + HOUR)
        cut = h.window_summaries(col, window, limit=2)
        assert [x.interface_id for x in cut.items] == ifaces[:2]
        assert cut.truncated
        assert h.window_summaries(new_id(CollectorId), window).items == ()
        with pytest.raises(ValidationError):
            h.window_summaries(col, TimeWindow(s.T0, s.T0 + DAY + s.MINUTE))
        with pytest.raises(ValidationError):
            h.window_summaries(col, window, limit=0)
