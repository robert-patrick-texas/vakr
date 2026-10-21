"""Build an interface graph view: series per position, unit, and reference lines (12.5, 12.6).

Traffic and utilization graphs read ``in_bps``/``out_bps``, packet graphs
``in_pps``/``out_pps``, and error graphs the per-second error rates, each
from every chosen position separately (MPOS-008). Utilization is the same
read scaled by ``100 / capacity``, and only exists when the interface has a
utilization denominator by E.17 #15 (a physical port, a port-channel, an
interface of unknown type, or a capacity override); otherwise the view is
unavailable with the reason, while its traffic graph still works. The
current capacity is used for the whole window; vakr keeps no capacity
history yet, which the view notes. Traffic graphs carry the capacity and
contracted-rate reference lines when known (12.6).

Dependencies:
    vakr.query.history_*: Context, requests, reads, and views.
    vakr.query.interface_capacity, units: Denominator and SI units.

Safety:
    Reads are bounded by the window and the point cap.

Example:
    >>> [m.value for m, _ in METRICS[GraphKind.PACKETS]]
    ['in_pps', 'out_pps']
"""

from dataclasses import replace
from datetime import datetime

from vakr.domain.series import Metric, TimeWindow
from vakr.ports.telemetry import TelemetryBackend
from vakr.query.history_context import Subject, choose_collectors, interval_of
from vakr.query.history_fetch import SeriesAsk, read_series
from vakr.query.history_requests import GraphKind, GraphRequest, plan
from vakr.query.history_views import GraphView, ReferenceView, SeriesView, UnitView, WindowView
from vakr.query.interface_capacity import capacity_of, no_capacity_reason
from vakr.query.settings import QuerySettings
from vakr.query.units import BITS, ERRORS, PACKETS, PERCENT, fixed, scaled, si_text

METRICS: dict[GraphKind, tuple[tuple[Metric, str], ...]] = {
    GraphKind.TRAFFIC: ((Metric.IN_BPS, "Inbound"), (Metric.OUT_BPS, "Outbound")),
    GraphKind.UTILIZATION: ((Metric.IN_BPS, "Inbound"), (Metric.OUT_BPS, "Outbound")),
    GraphKind.PACKETS: ((Metric.IN_PPS, "Inbound"), (Metric.OUT_PPS, "Outbound")),
    GraphKind.ERRORS: ((Metric.IN_ERRORS, "Input errors"), (Metric.OUT_ERRORS, "Output errors")),
}
"""Metrics and legend words per graph kind, inbound first."""

_BASE = {
    GraphKind.TRAFFIC: BITS,
    GraphKind.PACKETS: PACKETS,
    GraphKind.ERRORS: ERRORS,
    GraphKind.UTILIZATION: PERCENT,
}

CAPACITY_NOTE = (
    "Utilization uses today's capacity for the whole window; capacity history is not kept."
)
ERRORS_NOTE = "Error values are per-second rates over each sample; rankings use the raw deltas."


def unavailable_graph(
    subject: Subject,
    kind: GraphKind,
    request: GraphRequest,
    shown: WindowView,
    now: datetime,
    reason: str | None,
) -> GraphView:
    """Return an empty graph view; unavailable when ``reason`` is given.

    Args:
        subject: The interface's inventory.
        kind: Which graph.
        request: The request.
        shown: Window and zone.
        now: When the view was computed (UTC).
        reason: Why the graph cannot be drawn, or ``None`` for an empty, available view.

    Returns:
        GraphView: No series.

    Raises:
        None
    """
    unit = (
        fixed(PERCENT, PERCENT, 1.0) if kind is GraphKind.UTILIZATION else scaled(_BASE[kind], [])
    )
    subject_id = subject.interface.id if subject.interface else subject.device.id
    return GraphView(
        kind.value,
        subject_id,
        subject.name,
        shown,
        request.source.value,
        unit,
        (),
        (),
        False,
        reason is None,
        reason,
        now,
    )


def _references(subject: Subject, kind: GraphKind) -> tuple[ReferenceView, ...]:
    """Return the capacity and contracted-rate lines a graph shows (12.6).

    Args:
        subject: The interface's inventory.
        kind: Which graph.

    Returns:
        tuple[ReferenceView, ...]: Capacity, then contracted rate, when known.

    Raises:
        None
    """
    iface = subject.interface
    if iface is None or kind not in (GraphKind.TRAFFIC, GraphKind.UTILIZATION):
        return ()
    cap, contracted = capacity_of(iface), iface.annotation.contracted_bps
    found = []
    if kind is GraphKind.TRAFFIC:
        if cap is not None:
            found.append(
                ReferenceView(
                    "capacity", float(cap.bps), f"Capacity {si_text(cap.bps, BITS)} ({cap.label})"
                )
            )
        if contracted is not None:
            found.append(
                ReferenceView(
                    "contracted", float(contracted), f"Contracted {si_text(contracted, BITS)}"
                )
            )
    elif cap is not None and contracted is not None:
        pct = 100.0 * contracted / cap.bps
        found.append(ReferenceView("contracted", pct, f"Contracted {si_text(contracted, BITS)}"))
    return tuple(found)


def _unit(kind: GraphKind, series: list[SeriesView]) -> UnitView:
    """Choose the graph's display unit from every value and envelope bound.

    Args:
        kind: Which graph.
        series: The series.

    Returns:
        UnitView: Shared by every series of the graph.

    Raises:
        None
    """
    if kind is GraphKind.UTILIZATION:
        return fixed(PERCENT, PERCENT, 1.0)
    values = (v for s in series for p in s.points for v in (p.value, p.high))
    return scaled(_BASE[kind], values)


def interface_graph_view(
    backend: TelemetryBackend,
    subject: Subject,
    kind: GraphKind,
    request: GraphRequest,
    window: TimeWindow,
    shown: WindowView,
    settings: QuerySettings,
    now: datetime,
) -> GraphView:
    """Read and assemble an interface graph.

    Args:
        backend: The telemetry view.
        subject: The interface's inventory (``interface`` is set).
        kind: Which graph.
        request: The request.
        window: The resolved window.
        shown: Window and zone for the view.
        settings: Defaults (local collector, default interval).
        now: Reference time (UTC).

    Returns:
        GraphView: The graph, or an unavailable view with the reason.

    Raises:
        StorageError: If the store cannot be read.
        ValidationError: If the store refuses the request (``rollup`` without rollups).
    """
    iface = subject.interface
    base = unavailable_graph(subject, kind, request, shown, now, None)
    cap = capacity_of(iface) if iface is not None else None
    if iface is None or (kind is GraphKind.UTILIZATION and cap is None):
        return replace(
            base, available=False, reason=no_capacity_reason(iface) if iface else "not an interface"
        )
    chosen = choose_collectors(
        subject.collectors, request.collector_id, settings.local_collector, every=False
    )
    if not chosen:
        return replace(base, available=False, reason="no such collector")
    scale = 100.0 / cap.bps if kind is GraphKind.UTILIZATION and cap is not None else 1.0
    horizon, rollups = backend.retention.native_horizon(), backend.capabilities.rollups
    series: list[SeriesView] = []
    for c in chosen:
        interval = interval_of(backend, subject.device, c, settings, now)
        how = plan(
            window, request.source, interval, request.max_points, horizon=horizon, rollups=rollups
        )
        for metric, label in METRICS[kind]:
            ask = SeriesAsk(iface.id, c, metric, label, interval, scale)
            series.append(read_series(backend, ask, window, how, request.max_points))
    notes = {GraphKind.UTILIZATION: (CAPACITY_NOTE,), GraphKind.ERRORS: (ERRORS_NOTE,)}
    return replace(
        base,
        unit=_unit(kind, series),
        series=tuple(series),
        references=_references(subject, kind),
        truncated=any(s.truncated for s in series),
        notes=notes.get(kind, ()),
    )
