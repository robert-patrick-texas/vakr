"""Bounded CSV and JSON export of graph views (API-012, API-T09, 12.6).

An export is the view the reader already sees, so it is authorized and
bounded exactly as the graph is (at most 1,200 points per series, E.19),
and it carries what makes the numbers trustworthy: UTC timestamps, the
display timezone, the unit, the source and bucket width of every point, and
its quality flags. A missing value is an empty CSV cell or JSON ``null``,
never ``0``. Exports larger than ``EXPORT_MAX_ROWS`` rows or
``EXPORT_MAX_BYTES`` bytes are refused; asynchronous bulk export is
deferred past the MVP (27.4).

Dependencies:
    csv, io, json, re: Encoding.
    vakr.query.history_views, latency_views, jsonable, history_limits.

Safety:
    Views hold no secrets. The filename is built from safe characters only.

Example:
    >>> safe_name("core1 Gi0/0/1")
    'core1_Gi0_0_1'
"""

import csv
import io
import json
import re
from dataclasses import dataclass
from enum import StrEnum

from vakr.domain.errors import ValidationError
from vakr.query.history_limits import EXPORT_MAX_BYTES, EXPORT_MAX_ROWS
from vakr.query.history_views import GraphView, ResolutionView, SeriesView
from vakr.query.jsonable import iso_utc, to_jsonable
from vakr.query.latency_views import LatencyView

COLUMNS = (
    "time_utc",
    "metric",
    "position",
    "collector_id",
    "value",
    "low",
    "high",
    "unit",
    "source",
    "bucket_seconds",
    "timezone",
    "flags",
)
"""CSV header, in order."""

_UNSAFE = re.compile(r"[^A-Za-z0-9._-]+")


class ExportFormat(StrEnum):
    """Export encodings."""

    CSV = "csv"
    JSON = "json"


@dataclass(frozen=True, slots=True)
class ExportFile:
    """One export, ready to send.

    Attributes:
        filename: Suggested file name.
        media_type: ``text/csv`` or ``application/json`` with ``charset=utf-8``.
        body: The encoded content.
        rows: Data rows (CSV) or points (JSON) it holds.
    """

    filename: str
    media_type: str
    body: bytes
    rows: int


def safe_name(text: str) -> str:
    """Reduce text to safe filename characters.

    Args:
        text: Any text, such as a device and interface name.

    Returns:
        str: Letters, digits, ``.``, ``_``, and ``-`` only, at most 80 characters.

    Raises:
        None
    """
    return _UNSAFE.sub("_", text).strip("_")[:80] or "export"


def _span(spans: tuple[ResolutionView, ...], series: SeriesView, index: int) -> ResolutionView:
    """Return the resolution span that holds a point.

    Args:
        spans: The series' spans.
        series: The series.
        index: The point's index.

    Returns:
        ResolutionView: The span containing the point (the last one otherwise).

    Raises:
        None
    """
    t = series.points[index].t
    return next((s for s in spans if s.start <= t < s.end), spans[-1])


def _cell(value: float | None) -> str:
    """Format a CSV number cell; a missing value is an empty cell.

    Args:
        value: The value.

    Returns:
        str: ``repr`` of the float, or ``""``.

    Raises:
        None
    """
    return "" if value is None else repr(value)


def _rows(series: tuple[SeriesView, ...], unit: str, zone: str) -> list[list[str]]:
    """Return one CSV row per point of every series.

    Args:
        series: The series.
        unit: Base unit of the values.
        zone: Display timezone name.

    Returns:
        list[list[str]]: Rows in series order, then time order.

    Raises:
        ValidationError: If there are more than ``EXPORT_MAX_ROWS`` rows.
    """
    if sum(len(s.points) for s in series) > EXPORT_MAX_ROWS:
        raise ValidationError("the export is too large; narrow the range", field="range")
    out = []
    for s in series:
        for i, p in enumerate(s.points):
            span = _span(s.resolutions, s, i) if s.resolutions else None
            out.append(
                [
                    iso_utc(p.t),
                    s.metric,
                    s.position,
                    str(s.collector_id),
                    _cell(p.value),
                    _cell(p.low),
                    _cell(p.high),
                    unit,
                    span.source if span else "",
                    str(span.bucket_seconds or "") if span else "",
                    zone,
                    "|".join(p.flags),
                ]
            )
    return out


def _series_of(view: GraphView | LatencyView) -> tuple[tuple[SeriesView, ...], str]:
    """Return a view's series and their base unit.

    Args:
        view: A graph or latency view.

    Returns:
        tuple: The series, and the unit (latency loss rows say ``%`` in ``metric``).

    Raises:
        None
    """
    if isinstance(view, GraphView):
        return view.series, view.unit.base
    found = tuple(s for p in view.positions for s in (p.median, p.loss, p.jitter))
    return found, f"{view.rtt_unit.base} (loss: {view.loss_unit.base})"


def export_view(view: GraphView | LatencyView, fmt: ExportFormat) -> ExportFile:
    """Encode a graph or latency view as CSV or JSON, within the E.28 bounds.

    Args:
        view: The view, as returned to the reader.
        fmt: The encoding.

    Returns:
        ExportFile: Name, media type, and content.

    Raises:
        ValidationError: If the export would exceed ``EXPORT_MAX_ROWS`` or
            ``EXPORT_MAX_BYTES``.
    """
    series, unit = _series_of(view)
    rows = _rows(series, unit, view.window.timezone)
    kind = view.kind if isinstance(view, GraphView) else "latency"
    name = view.subject if isinstance(view, GraphView) else view.device
    stem = f"vakr-{kind}-{safe_name(name)}-{iso_utc(view.window.start)}-{iso_utc(view.window.end)}"
    stem = stem.replace(":", "")
    if fmt is ExportFormat.JSON:
        body = json.dumps(to_jsonable(view), separators=(",", ":")).encode()
        media, suffix = "application/json; charset=utf-8", "json"
    else:
        buffer = io.StringIO()
        writer = csv.writer(buffer, lineterminator="\n")
        writer.writerow(COLUMNS)
        writer.writerows(rows)
        body, media, suffix = buffer.getvalue().encode(), "text/csv; charset=utf-8", "csv"
    if len(body) > EXPORT_MAX_BYTES:
        raise ValidationError("the export is too large; narrow the range", field="range")
    return ExportFile(f"{stem}.{suffix}", media, body, len(rows))
