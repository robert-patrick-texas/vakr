"""Gaps and quality markers for graph series (E.19, STORE-012, UI-010, 12.6).

Two jobs, both about not drawing a line that the data does not support:

- **Gap points (E.19).** A native series has one point per stored
  observation, so an outage is simply a long step between two points. Where
  consecutive native points are more than ``GAP_FACTOR`` (1.5) times the
  effective poll interval apart, an explicit ``None`` point is inserted one
  interval after the earlier point, so a graph breaks the line instead of
  interpolating across the outage. Bucketed series already carry a ``None``
  point for every empty bucket.
- **Markers (12.6).** Quality flags become stretches a graph can shade:
  agent restart, counter discontinuity, invalid baseline (first sample,
  missing prior, baseline gap), partial collection, collector outage, and
  rates above capacity. A point without a value and without flags is a
  plain gap. Consecutive stretches of one kind merge, and the stretch
  before the first point and after the last are gaps when longer than the
  gap threshold, so an empty window says so.

Dependencies:
    vakr.domain: Points, spans, and quality flags.
    vakr.query.history_views, history_limits, labels: Views, E.19 factor, words.

Safety:
    Pure; never invents a value, only ``None`` gaps.

Example:
    >>> flag_names(int(Quality.BASELINE_UPTIME_RESET | Quality.RETRIED))
    ('baseline_uptime_reset', 'retried')
"""

import itertools
from collections.abc import Sequence
from datetime import timedelta
from functools import lru_cache

from vakr.domain.quality import Quality
from vakr.domain.series import SeriesPoint, TimeWindow
from vakr.query.history_limits import GAP_FACTOR
from vakr.query.history_views import MarkerKind, MarkerView, PointView
from vakr.query.labels import QUALITY_WORDS
from vakr.query.text import age_text

_FLAGS = tuple((f, (f.name or "").lower()) for f in Quality if f.value)

_KINDS: tuple[tuple[MarkerKind, Quality], ...] = (
    (MarkerKind.RESTART, Quality.BASELINE_UPTIME_RESET),
    (
        MarkerKind.DISCONTINUITY,
        Quality.BASELINE_DISCONTINUITY
        | Quality.BASELINE_WIDTH_CHANGE
        | Quality.BASELINE_DECREASE
        | Quality.BASELINE_IDENTITY_UNRESOLVED,
    ),
    (
        MarkerKind.BASELINE,
        Quality.BASELINE_FIRST_SAMPLE
        | Quality.BASELINE_MISSING_PRIOR
        | Quality.BASELINE_GAP
        | Quality.BASELINE_ELAPSED_INVALID,
    ),
    (MarkerKind.PARTIAL, Quality.PARTIAL_RESPONSE | Quality.MISSING_FIELDS),
    (MarkerKind.COLLECTOR, Quality.COLLECTOR_DEGRADED | Quality.NOT_ATTEMPTED),
    (MarkerKind.OVER_CAPACITY, Quality.RATE_EXCEEDS_CAPACITY),
)
"""Marker kinds and the flags that raise them, most specific first."""

GAP_TEXT = "no data: nothing was stored for this stretch"


@lru_cache(maxsize=4096)
def flag_names(quality: int) -> tuple[str, ...]:
    """Return the names of the flags set in a quality value, in bit order.

    Args:
        quality: Quality flags as an integer.

    Returns:
        tuple[str, ...]: Lower-case flag names; empty for no flags.

    Raises:
        None
    """
    return tuple(name for flag, name in _FLAGS if quality & flag)


def insert_gaps(points: Sequence[SeriesPoint], interval_s: float) -> tuple[list[SeriesPoint], int]:
    """Insert a ``None`` point inside every step longer than the gap threshold (E.19).

    Args:
        points: Native points, one per observation, in time order.
        interval_s: Effective poll interval in seconds.

    Returns:
        tuple: The points with gap points added, and how many were added.

    Raises:
        None
    """
    limit, step = timedelta(seconds=GAP_FACTOR * interval_s), timedelta(seconds=interval_s)
    out: list[SeriesPoint] = []
    added = 0
    for prev, cur in itertools.pairwise(points):
        out.append(prev)
        if cur.timestamp - prev.timestamp > limit:
            out.append(SeriesPoint(prev.timestamp + step, None))
            added += 1
    out.extend(points[-1:])
    return out, added


def _kinds_of(point: SeriesPoint) -> list[tuple[MarkerKind, str]]:
    """Return the marker kinds a point raises, with operator words for each.

    Args:
        point: The point.

    Returns:
        list: ``(kind, text)`` pairs; a flagless gap is ``GAP``.

    Raises:
        None
    """
    found = []
    for kind, mask in _KINDS:
        hit = point.quality & mask
        if hit:
            first = next(f for f, _ in _FLAGS if hit & f)
            found.append((kind, QUALITY_WORDS.get(first, kind.value)))
    if point.value is None and not found:
        found.append((MarkerKind.GAP, GAP_TEXT))
    return found


def markers(
    points: Sequence[SeriesPoint], window: TimeWindow, interval_s: float
) -> tuple[MarkerView, ...]:
    """Build merged markers for a series, edge gaps included.

    Args:
        points: The series points, in time order.
        window: The series window.
        interval_s: Effective poll interval, for the edge-gap threshold.

    Returns:
        tuple[MarkerView, ...]: Markers ordered by start, then kind.

    Raises:
        None
    """
    limit, step = timedelta(seconds=GAP_FACTOR * interval_s), timedelta(seconds=interval_s)
    found: list[MarkerView] = []
    first = points[0].timestamp if points else window.end
    if first - window.start > limit:
        found.append(MarkerView(MarkerKind.GAP, window.start, first, GAP_TEXT))
    # The last point stands for one interval (or bucket), not for the rest of the window.
    tail = min(points[-1].timestamp + step, window.end) if points else window.end
    ends = [p.timestamp for p in points[1:]] + ([tail] if points else [])
    open_: dict[MarkerKind, MarkerView] = {}
    for point, end in zip(points, ends, strict=True):
        hits = dict(_kinds_of(point))
        for kind in list(open_):
            if kind not in hits:
                found.append(open_.pop(kind))
        for kind, text in hits.items():
            started = open_.get(kind)
            open_[kind] = MarkerView(
                kind,
                started.start if started else point.timestamp,
                end,
                started.text if started else text,
            )
    found.extend(open_.values())
    if points and window.end - points[-1].timestamp > limit:
        found.append(MarkerView(MarkerKind.GAP, tail, window.end, _quiet(window.end - tail)))
    return _merged(sorted(found, key=lambda m: (m.start, m.kind.value)))


def _merged(found: list[MarkerView]) -> tuple[MarkerView, ...]:
    """Join markers of one kind that touch, keeping the first one's words.

    Args:
        found: Markers ordered by start.

    Returns:
        tuple[MarkerView, ...]: The markers with touching same-kind stretches joined.

    Raises:
        None
    """
    out: list[MarkerView] = []
    last: dict[MarkerKind, int] = {}
    for m in found:
        at = last.get(m.kind)
        if at is not None and out[at].end >= m.start:
            out[at] = MarkerView(m.kind, out[at].start, max(out[at].end, m.end), out[at].text)
            continue
        last[m.kind] = len(out)
        out.append(m)
    return tuple(out)


def _quiet(span: timedelta) -> str:
    """Describe a trailing stretch without samples.

    Args:
        span: Its length.

    Returns:
        str: Such as ``"no samples in the last 12 min"``.

    Raises:
        None
    """
    return f"no samples in the last {age_text(span.total_seconds())}"


def point_view(point: SeriesPoint, scale: float = 1.0) -> PointView:
    """Turn a domain point into its view, optionally scaled (utilization percent).

    Args:
        point: The point.
        scale: Factor applied to the value and the envelope.

    Returns:
        PointView: The view; ``None`` stays ``None``.

    Raises:
        None
    """

    def times(v: float | None) -> float | None:
        """Scale one value, keeping ``None``.

        Args:
            v: The value.

        Returns:
            float | None: ``v * scale``, or ``None``.

        Raises:
            None
        """
        return None if v is None else v * scale

    return PointView(
        point.timestamp,
        times(point.value),
        times(point.minimum),
        times(point.maximum),
        flag_names(int(point.quality)),
    )
