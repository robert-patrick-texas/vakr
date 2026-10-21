"""In-memory ``HistoryQuery``: the reference downsampling and ranking summaries (M4).

Selection is a scan over the store; bucketing follows
``vakr.domain.downsampling`` and percentiles use ``vakr.logic.stats.p95``,
so this is the behaviour every other backend's ``HistoryQuery`` must match
(``tests/contract/contract_telemetry_history.py``).

Dependencies:
    vakr.adapters.memory.telemetry_store: Storage.
    vakr.domain, vakr.logic.stats: Bucketing rules and the shared p95.
    vakr.ports.telemetry.rankings: Result types and bounds.

Safety:
    Every read is bounded by a window and a point cap or row limit.

Example:
    >>> from vakr.adapters.memory.telemetry_store import MemoryTelemetryStore
    >>> MemoryHistoryQuery(MemoryTelemetryStore()).__class__.__name__
    'MemoryHistoryQuery'
"""

from collections import defaultdict
from uuid import UUID

from vakr.adapters.memory.telemetry_store import MemoryTelemetryStore
from vakr.domain.checks import require_int
from vakr.domain.downsampling import (
    Cell,
    bucket_points,
    downsample_spans,
    ladder_width,
    span_width,
)
from vakr.domain.enums import ObservationType, SeriesSource
from vakr.domain.ids import CollectorId, InterfaceId
from vakr.domain.observations import IcmpObservation, InterfaceObservation, observation_subject
from vakr.domain.rollups import ROLLUP_BUCKET_SECONDS
from vakr.domain.series import (
    MAX_SERIES_POINTS,
    Metric,
    Series,
    SeriesPoint,
    SeriesResolution,
    TimeWindow,
    metric_value,
)
from vakr.domain.timestamps import to_epoch_us
from vakr.logic.stats import p95
from vakr.ports.telemetry.rankings import (
    MAX_RANKED_INTERFACES,
    RankingScan,
    WindowSummary,
    check_ranking_bounds,
)

_US = 1_000_000


class MemoryHistoryQuery:
    """``HistoryQuery`` over a ``MemoryTelemetryStore``."""

    def __init__(self, store: MemoryTelemetryStore) -> None:
        """Bind the query.

        Args:
            store: The store.

        Returns:
            None

        Raises:
            None
        """
        self._store = store

    def downsampled_series(
        self, subject_id: UUID, collector_id: CollectorId, metric: Metric, window: TimeWindow,
        *, max_points: int = MAX_SERIES_POINTS, source: SeriesSource | None = None,
    ) -> Series:  # fmt: skip
        """Return a series bucketed on the fixed width ladder; see the port.

        Args:
            subject_id: Interface or device.
            collector_id: The collector.
            metric: The metric.
            window: The window.
            max_points: Point cap, 1 to ``MAX_SERIES_POINTS``.
            source: ``None`` to split at the native horizon, else one source throughout.

        Returns:
            Series: One point per bucket, gaps as ``None``.

        Raises:
            ValidationError: If ``max_points`` is out of range or rollups are
                forced on a store without them.
        """
        require_int(max_points, "max_points", minimum=1, maximum=MAX_SERIES_POINTS)
        store = self._store
        width = ladder_width(window.duration.total_seconds(), max_points)
        spans = downsample_spans(window, store.horizon, rollups=store.capabilities.rollups,
                                 source=source)  # fmt: skip
        points: list[SeriesPoint] = []
        shown: list[SeriesResolution] = []
        for span in spans:
            bounds = (to_epoch_us(span.start), to_epoch_us(span.end))
            w = span_width(span.source, width)
            if span.source is SeriesSource.ROLLUP:
                cells = self._rollup_cells(subject_id, collector_id, metric, bounds, w)
            else:
                cells = self._native_cells(subject_id, collector_id, metric, bounds, w)
            points.extend(bucket_points(cells, bounds, w, max_points + 1 - len(points)))
            shown.append(SeriesResolution(span.source, span.start, span.end, w))
        return Series(subject_id, collector_id, metric, window, tuple(points[:max_points]),
                      tuple(shown), len(points) > max_points)  # fmt: skip

    def _native_cells(
        self, subject: UUID, collector: UUID, metric: Metric, bounds: tuple[int, int], width: int
    ) -> dict[int, Cell]:
        """Fold native samples of one span into buckets.

        Args:
            subject: Interface or device.
            collector: The collector.
            metric: The metric.
            bounds: ``(start_us, end_us)`` of the span.
            width: Bucket width in seconds.

        Returns:
            dict[int, Cell]: Bucket index to its folded aggregates.

        Raises:
            None
        """
        icmp = metric.observation_type is ObservationType.ICMP
        expected = IcmpObservation if icmp else InterfaceObservation
        cells: dict[int, Cell] = defaultdict(Cell)
        for o in list(self._store.observations.values()):
            if not isinstance(o, expected) or o.collector_id != collector:
                continue
            ts = to_epoch_us(o.timestamp)
            if observation_subject(o) != subject or not bounds[0] <= ts < bounds[1]:
                continue
            v = metric_value(o, metric)
            cells[ts // (width * _US)].add(1, v is not None, v, v, v or 0.0, int(o.quality))
        return cells

    def _rollup_cells(
        self, subject: UUID, collector: UUID, metric: Metric, bounds: tuple[int, int], width: int
    ) -> dict[int, Cell]:
        """Re-bucket stored 15-minute rollups of one span to ``width``.

        Args:
            subject: Interface or device.
            collector: The collector.
            metric: The metric.
            bounds: ``(start_us, end_us)`` on ``bucket_start``.
            width: Bucket width in seconds, at least 15 minutes.

        Returns:
            dict[int, Cell]: Bucket index to its folded aggregates.

        Raises:
            None
        """
        cells: dict[int, Cell] = defaultdict(Cell)
        for b in list(self._store.buckets.values()):
            start = to_epoch_us(b.bucket_start)
            if (b.subject_id, b.collector_id, b.metric) != (subject, collector, metric):
                continue
            if b.bucket_seconds != ROLLUP_BUCKET_SECONDS or not bounds[0] <= start < bounds[1]:
                continue
            cells[start // (width * _US)].add(b.sample_count, b.valid_count, b.minimum,
                                              b.maximum, (b.mean or 0.0) * b.valid_count,
                                              int(b.quality))  # fmt: skip
        return cells

    def window_summaries(
        self, collector_id: CollectorId, window: TimeWindow, *, limit: int = MAX_RANKED_INTERFACES
    ) -> RankingScan:
        """Return every interface's p95 rates and error totals; see the port.

        Args:
            collector_id: The collector.
            window: The window.
            limit: Most rows.

        Returns:
            RankingScan: Rows ordered by interface UUID text.

        Raises:
            ValidationError: If the window or limit is out of bounds.
        """
        check_ranking_bounds(window.duration.total_seconds(), limit)
        found: dict[InterfaceId, list[InterfaceObservation]] = defaultdict(list)
        for o in list(self._store.observations.values()):
            mine = isinstance(o, InterfaceObservation) and o.collector_id == collector_id
            if mine and window.contains(o.timestamp):
                found[o.interface_id].append(o)
        rows = [_summary(iface, found[iface]) for iface in sorted(found, key=str)[:limit]]
        return RankingScan(tuple(rows), len(found) > limit)


def _summary(iface: InterfaceId, samples: list[InterfaceObservation]) -> WindowSummary:
    """Summarise one interface's samples by the port's rules.

    Args:
        iface: The interface.
        samples: Its samples in the window.

    Returns:
        WindowSummary: Counts, p95 rates, and error totals.

    Raises:
        None
    """
    ins = [o.in_bps for o in samples if o.in_bps is not None]
    outs = [o.out_bps for o in samples if o.out_bps is not None]
    both = (o for o in samples if o.in_error_delta is not None)
    counted = [o for o in both if o.out_error_delta is not None]
    return WindowSummary(
        iface, len(samples), len(ins), len(outs),
        p95(ins) if ins else None, p95(outs) if outs else None, len(counted),
        sum(o.in_error_delta or 0 for o in counted), sum(o.out_error_delta or 0 for o in counted),
        _packets(counted),
    )  # fmt: skip


def _packets(counted: list[InterfaceObservation]) -> int | None:
    """Sum the packet deltas of counted samples, when every one has them (E.17 #13).

    Args:
        counted: Samples with both error deltas.

    Returns:
        int | None: ``round(sum((in_pps + out_pps) x elapsed))``; ``None`` when
        a sample lacks a packet rate or its elapsed time, or none counted.

    Raises:
        None
    """
    total = 0.0
    for o in counted:
        if o.in_pps is None or o.out_pps is None or o.sample_elapsed_us is None:
            return None
        total += (o.in_pps + o.out_pps) * o.sample_elapsed_us
    return round(total / _US) if counted else None
