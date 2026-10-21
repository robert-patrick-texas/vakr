"""``RankingProjection`` semantics: projecting never changes a ranking (E.28, DASH-003, DASH-004).

Subclass ``RankingProjectionContract`` in an adapter's test module; its
``conftest.py`` supplies ``telemetry_factory``. The memory reference keeps
only the bookkeeping and always scans natively; SQLite answers from stored
partials. Both must return the summaries an independent computation gives
(nearest-rank p95, valid error sums, exact packet sums, coverage counts,
one collector at a time: DASH-T01, DASH-T02, MPOS-008), before and after
projecting, after a late sample, after retention, and after ``forget``,
and both must list, unlist, and bound buckets the same way (SEC-019).

Dependencies:
    pytest, random; tests.contract.contract_samples; vakr.ports.telemetry.

Example:
    $ uv run pytest tests/contract -k RankingProjection
"""

import math
import random
from datetime import datetime, timedelta, timezone
from typing import cast

import pytest

from tests.contract import contract_samples as s
from vakr.domain.errors import ValidationError
from vakr.domain.ids import BatchId, CollectorId, InterfaceId, new_id
from vakr.domain.observations import InterfaceObservation
from vakr.domain.rollups import ROLLUP_BUCKET_SECONDS
from vakr.domain.series import Metric, TimeWindow
from vakr.domain.state import RollupBucket
from vakr.logic.stats import p95
from vakr.ports.telemetry import TelemetryBackend, TelemetryBackendFactory
from vakr.ports.telemetry.history import HistoryBackend
from vakr.ports.telemetry.ranking_projection import ProjectionBackend, RankingProjection
from vakr.ports.telemetry.rankings import RankingScan, WindowSummary

BUCKET = timedelta(seconds=ROLLUP_BUCKET_SECONDS)
HOURS = 3
_SKEW = timedelta(seconds=3)
WINDOWS = (
    TimeWindow(s.T0, s.T0 + HOURS * 4 * BUCKET),
    TimeWindow(s.T0 + 7 * s.MINUTE, s.T0 + 2 * 4 * BUCKET + 53 * s.MINUTE),
    TimeWindow(s.T0 + 4 * BUCKET, s.T0 + 8 * BUCKET),
    TimeWindow(s.T0 + 20 * s.MINUTE, s.T0 + 35 * s.MINUTE),
)


def projection(backend: TelemetryBackend) -> RankingProjection:
    """Return a backend's projection port; every adapter here is a ``ProjectionBackend``.

    Args:
        backend: The backend.

    Returns:
        RankingProjection: Its ``ranking_projection`` port.

    Raises:
        None
    """
    return cast(ProjectionBackend, backend).ranking_projection


def fleet(seed: int = 7) -> tuple[list[InterfaceObservation], list[CollectorId]]:
    """Build three hours of awkward samples: two collectors, three interfaces.

    Rates and packet rates are arbitrary floats, some missing; error deltas
    are sometimes missing on one side; one instant holds two observations.

    Args:
        seed: Random seed.

    Returns:
        tuple: The samples and the collectors.

    Raises:
        None
    """
    rng, dev = random.Random(seed), s.device().id  # noqa: S311  # reproducible data
    cols = [new_id(CollectorId), new_id(CollectorId)]
    rows = []
    for col in cols:
        for iface in (new_id(InterfaceId) for _ in range(3)):
            for m in range(HOURS * 60):
                rows.append(s.sample(dev, iface, col, s.T0 + m * s.MINUTE + _SKEW,
                                     **_values(rng)))  # fmt: skip
            rows.append(s.sample(dev, iface, col, s.T0 + 61 * s.MINUTE + _SKEW,
                                 **_values(rng)))  # fmt: skip
    return rows, cols


def _values(rng: random.Random) -> dict[str, object]:
    """Return one sample's measured values.

    Args:
        rng: The random source.

    Returns:
        dict[str, object]: Field overrides.

    Raises:
        None
    """
    rate = lambda top: None if rng.random() < 0.05 else rng.uniform(0, top)  # noqa: E731
    delta = lambda: None if rng.random() < 0.05 else rng.choice((0, 0, 1, 7))  # noqa: E731
    return {"in_bps": rate(1e10), "out_bps": rate(1e9), "in_pps": rate(1e6), "out_pps": rate(1e5),
            "in_error_delta": delta(), "out_error_delta": delta(),
            "sample_elapsed_us": rng.randint(59_000_000, 61_000_000)}  # fmt: skip


def expected(rows: list[InterfaceObservation], col: CollectorId, window: TimeWindow) -> RankingScan:
    """Compute the ranking summaries independently, by the port's rules.

    Args:
        rows: Every sample written.
        col: The collector.
        window: The window.

    Returns:
        RankingScan: Rows in interface UUID text order.

    Raises:
        None
    """
    found: dict[InterfaceId, list[InterfaceObservation]] = {}
    for o in rows:
        if o.collector_id == col and window.contains(o.timestamp):
            found.setdefault(o.interface_id, []).append(o)
    items = []
    for iface in sorted(found, key=str):
        got = found[iface]
        ins = [o.in_bps for o in got if o.in_bps is not None]
        outs = [o.out_bps for o in got if o.out_bps is not None]
        counted = [o for o in got if o.in_error_delta is not None and o.out_error_delta is not None]
        known = [o for o in counted if o.in_pps is not None and o.out_pps is not None]
        packets = None
        if counted and len(known) == len(counted):
            sums = [(o.in_pps + o.out_pps) * o.sample_elapsed_us for o in known]  # ty: ignore[unsupported-operator]
            packets = round(math.fsum(sums) / 1_000_000)
        items.append(WindowSummary(
            iface, len(got), len(ins), len(outs), p95(ins) if ins else None,
            p95(outs) if outs else None, len(counted),
            sum(o.in_error_delta or 0 for o in counted),
            sum(o.out_error_delta or 0 for o in counted), packets,
        ))  # fmt: skip
    return RankingScan(tuple(items), False)


def scans(backend: TelemetryBackend, cols: list[CollectorId]) -> list[RankingScan]:
    """Read every collector's summaries over every test window.

    Args:
        backend: The backend.
        cols: The collectors.

    Returns:
        list[RankingScan]: One scan per collector and window.

    Raises:
        None
    """
    history = cast(HistoryBackend, backend).history
    return [history.window_summaries(c, w) for c in cols for w in WINDOWS]


def write(backend: TelemetryBackend, rows: list[InterfaceObservation]) -> None:
    """Write samples in batches the backend accepts.

    Args:
        backend: The backend.
        rows: The samples.

    Returns:
        None

    Raises:
        None
    """
    size = backend.capabilities.max_batch_rows
    for n in range(0, len(rows), size):
        backend.writer.write_batch(new_id(BatchId), rows[n : n + size])


class RankingProjectionContract:
    """Behaviour every ``RankingProjection`` must show."""

    def test_e28_projected_rankings_equal_native_ones(
        self, telemetry_factory: TelemetryBackendFactory
    ) -> None:
        """E.28, DASH-T01, DASH-T02, MPOS-008: projecting and forgetting change no summary.

        Args:
            telemetry_factory: Opens views of one telemetry store.

        Returns:
            None

        Raises:
            None
        """
        rows, cols = fleet()
        backend = telemetry_factory()
        write(backend, rows)
        want = [expected(rows, c, w) for c in cols for w in WINDOWS]
        assert scans(backend, cols) == want
        proj, whole = projection(backend), WINDOWS[0]
        todo = proj.unprojected(whole)
        assert todo == tuple(s.T0 + k * BUCKET for k in reversed(range(HOURS * 4)))
        assert [proj.project(b) for b in todo] == [6] * len(todo)
        assert proj.unprojected(whole) == ()
        assert scans(telemetry_factory(), cols) == want
        assert proj.forget() == len(todo)
        assert scans(backend, cols) == want

    def test_e28_late_sample_unlists_its_bucket(
        self, telemetry_factory: TelemetryBackendFactory
    ) -> None:
        """E.28: a sample stored into a projected bucket unlists it; a replay does not.

        Args:
            telemetry_factory: Opens views of one telemetry store.

        Returns:
            None

        Raises:
            None
        """
        rows, cols = fleet()
        backend = telemetry_factory()
        write(backend, rows)
        proj = projection(backend)
        for bucket in proj.unprojected(WINDOWS[0]):
            proj.project(bucket)
        write(backend, rows[:5])  # a replay stores nothing
        assert proj.unprojected(WINDOWS[0]) == ()
        late = s.sample(rows[0].device_id, rows[0].interface_id, cols[1],
                        s.T0 + 5 * BUCKET + 2 * s.MINUTE, in_bps=9e12)  # fmt: skip
        write(backend, [late])
        assert proj.unprojected(WINDOWS[0]) == (s.T0 + 5 * BUCKET,)
        rows.append(late)
        assert scans(backend, cols) == [expected(rows, c, w) for c in cols for w in WINDOWS]

    def test_sec019_projection_bounds(self, telemetry_factory: TelemetryBackendFactory) -> None:
        """SEC-019, E.28: listings cover at most 25 hours and 100 buckets; buckets are aligned.

        Args:
            telemetry_factory: Opens views of one telemetry store.

        Returns:
            None

        Raises:
            None
        """
        proj = projection(telemetry_factory())
        with pytest.raises(ValidationError):
            proj.unprojected(TimeWindow(s.T0, s.T0 + timedelta(hours=25, minutes=1)))
        for limit in (0, 101, True):
            with pytest.raises(ValidationError):
                proj.unprojected(WINDOWS[0], limit=limit)
        assert proj.unprojected(WINDOWS[0], limit=2) == (s.T0 + 11 * BUCKET, s.T0 + 10 * BUCKET)
        inside = TimeWindow(s.T0 + 10 * s.MINUTE, s.T0 + 35 * s.MINUTE)
        assert proj.unprojected(inside) == (s.T0 + BUCKET,)
        assert proj.unprojected(WINDOWS[3]) == ()
        for bad in (s.T0 + s.MINUTE, datetime(2026, 10, 1, 1, tzinfo=timezone(timedelta(hours=1)))):
            with pytest.raises(ValidationError):
                proj.project(bad)
        assert proj.project(s.T0) == 0
        assert proj.unprojected(TimeWindow(s.T0, s.T0 + BUCKET)) == ()

    def test_e28_retention_unlists_expired_and_old_buckets(
        self, telemetry_factory: TelemetryBackendFactory
    ) -> None:
        """E.28, RET-001: buckets before the horizon or older than 25 h are unlisted.

        Args:
            telemetry_factory: Opens views of one telemetry store.

        Returns:
            None

        Raises:
            None
        """
        rows, cols = fleet()
        backend = telemetry_factory()
        write(backend, rows)
        proj = projection(backend)
        for bucket in proj.unprojected(WINDOWS[0]):
            proj.project(bucket)
        backend.retention.apply(s.T0 + timedelta(hours=26))
        assert proj.unprojected(WINDOWS[0]) == tuple(s.T0 + k * BUCKET for k in (3, 2, 1, 0))
        assert scans(backend, cols) == [expected(rows, c, w) for c in cols for w in WINDOWS]
        if backend.capabilities.rollups:
            backend.rollups.write([_rollup(rows[0], s.T0 + 4 * BUCKET)])
        backend.retention.apply(s.T0 + 4 * BUCKET + backend.retention.policy.native)
        assert proj.project(s.T0) == 0
        assert proj.unprojected(WINDOWS[0]) == tuple(
            s.T0 + k * BUCKET for k in reversed(range(4, HOURS * 4))
        )
        kept = [o for o in rows if o.timestamp >= s.T0 + 4 * BUCKET]
        assert scans(backend, cols) == [expected(kept, c, w) for c in cols for w in WINDOWS]


def _rollup(sample: InterfaceObservation, end: datetime) -> RollupBucket:
    """Build a rollup bucket ending at ``end``, so retention may expire native data before it.

    Args:
        sample: Any stored sample, for its interface and collector.
        end: The bucket end.

    Returns:
        RollupBucket: A full bucket.

    Raises:
        None
    """
    return RollupBucket(sample.interface_id, sample.collector_id, Metric.IN_BPS, end - BUCKET,
                        ROLLUP_BUCKET_SECONDS, 15, 15, 15, minimum=1.0, mean=2.0, p95=3.0,
                        maximum=4.0)  # fmt: skip
