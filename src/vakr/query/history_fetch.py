"""Read one graph series through the telemetry ports and dress it for display (10.6, E.19).

``read_series`` follows a ``Plan``: a raw read (``ObservationQuery``, one
point per observation, E.19 gap points inserted inside native spans) or a
bucketed read (``HistoryQuery.downsampled_series``, every bucket present,
min/max envelope). Either way the result is one collector's evidence for
one metric (MPOS-008), with its resolution spans, truncation, gap count,
and markers. ``scale`` turns bits per second into percent of capacity for
utilization graphs; ``None`` stays ``None``.

Dependencies:
    vakr.ports.telemetry, history: The read ports.
    vakr.query.history_markers, history_requests, history_views: Gaps, plans, views.

Safety:
    Every read is bounded by the window and ``max_points``. Storage trouble
    raises ``StorageError`` for the caller to report; it is never turned
    into an empty or zero series.

Example:
    >>> from vakr.adapters.memory import memory_telemetry_factory
    >>> history_of(memory_telemetry_factory()()).__class__.__name__
    'MemoryHistoryQuery'
"""

from dataclasses import dataclass
from typing import cast
from uuid import UUID

from vakr.domain.catalog import Collector
from vakr.domain.enums import ObservationType, SeriesSource
from vakr.domain.errors import StorageError
from vakr.domain.ids import DeviceId, InterfaceId
from vakr.domain.series import Metric, Series, SeriesPoint, TimeWindow
from vakr.ports.telemetry import TelemetryBackend
from vakr.ports.telemetry.history import HistoryQuery
from vakr.query.history_markers import insert_gaps, markers, point_view
from vakr.query.history_requests import Plan
from vakr.query.history_views import ResolutionView, SeriesView


def history_of(backend: TelemetryBackend) -> HistoryQuery:
    """Return a backend's ``HistoryQuery`` (every adapter here is a ``HistoryBackend``).

    Args:
        backend: An open telemetry view.

    Returns:
        HistoryQuery: Its history port.

    Raises:
        StorageError: If the backend serves no history reads.
    """
    found = getattr(backend, "history", None)
    if found is None:
        raise StorageError("this telemetry store serves no history graphs", retryable=False)
    return cast(HistoryQuery, found)


@dataclass(frozen=True, slots=True)
class SeriesAsk:
    """One series to read.

    Attributes:
        subject: Interface (interface metrics) or device (ICMP metrics).
        collector: The one collector whose evidence to read.
        metric: The metric.
        label: Legend text without the position.
        interval_s: Effective poll interval, for E.19 gaps.
        scale: Factor applied to values, such as ``100 / capacity``.
    """

    subject: UUID
    collector: Collector
    metric: Metric
    label: str
    interval_s: float
    scale: float = 1.0


def _raw(backend: TelemetryBackend, ask: SeriesAsk, window: TimeWindow, cap: int) -> Series:
    """Read one point per observation through ``ObservationQuery``.

    Args:
        backend: The telemetry view.
        ask: What to read.
        window: The window.
        cap: Point cap.

    Returns:
        Series: The port's series.

    Raises:
        StorageError: If the store cannot be read.
    """
    q, c = backend.query, ask.collector.id
    if ask.metric.observation_type is ObservationType.ICMP:
        return q.icmp_series(DeviceId(ask.subject), c, ask.metric, window, max_points=cap)
    return q.interface_series(InterfaceId(ask.subject), c, ask.metric, window, max_points=cap)


def _with_gaps(series: Series, interval_s: float) -> list[SeriesPoint]:
    """Insert E.19 gap points between points of the native spans only.

    Rollup spans already hold a point for every bucket, gaps included.

    Args:
        series: A raw series.
        interval_s: Effective poll interval.

    Returns:
        list[SeriesPoint]: Points with gap points added, in time order.

    Raises:
        None
    """
    out: list[SeriesPoint] = []
    for span in series.resolutions:
        inside = [p for p in series.points if span.start <= p.timestamp < span.end]
        if span.source is SeriesSource.NATIVE and span.bucket_seconds is None:
            inside, _ = insert_gaps(inside, interval_s)
        out.extend(inside)
    return out


def read_series(
    backend: TelemetryBackend, ask: SeriesAsk, window: TimeWindow, how: Plan, cap: int
) -> SeriesView:
    """Read one series by plan and build its view.

    Args:
        backend: The telemetry view.
        ask: What to read.
        window: The window.
        how: Raw or bucketed, and the forced source.
        cap: Point cap, 1 to 1,200.

    Returns:
        SeriesView: Points, spans, truncation, gaps, and markers.

    Raises:
        StorageError: If the store cannot be read.
        ValidationError: If the store refuses the request (bad cap, no rollups).
    """
    if how.raw:
        series = _raw(backend, ask, window, cap)
        points = _with_gaps(series, ask.interval_s)
        truncated = series.truncated or len(points) > cap
        points = points[:cap]
    else:
        series = history_of(backend).downsampled_series(
            ask.subject, ask.collector.id, ask.metric, window, max_points=cap, source=how.source
        )
        points, truncated = list(series.points), series.truncated
    spans = tuple(
        ResolutionView(s.source.value, s.start, s.end, s.bucket_seconds) for s in series.resolutions
    )
    step = max([ask.interval_s, *(s.bucket_seconds or 0 for s in series.resolutions)])
    return SeriesView(
        metric=ask.metric.value,
        label=f"{ask.label} ({ask.collector.position})",
        collector_id=ask.collector.id,
        position=ask.collector.position,
        points=tuple(point_view(p, ask.scale) for p in points),
        resolutions=spans,
        truncated=truncated,
        gaps=sum(1 for p in points if p.value is None),
        markers=markers(points, window, step),
    )
