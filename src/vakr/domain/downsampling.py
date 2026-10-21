"""Downsampling on the fixed width ladder: widths, spans, and bucket points (10.6, E.19, E.21).

A graph of seven or thirty days has more samples than the 1,200-point cap
(10.6), so the server buckets them. Every backend buckets the same way, so
a memory store and a SQLite store return equal series (DB-004, DB-006):

1. ``ladder_width`` picks the narrowest width on ``LADDER_S`` (60 s to one
   day) that keeps the whole window within the point cap.
2. ``downsample_spans`` splits the window at the native horizon, as
   ``resolution_spans`` does, unless the caller forces one source for the
   whole window (the 10.6 table's "rollup" ranges, or an explicit
   "native" request). Rollup spans use at least the 15-minute width.
3. Each output bucket is UTC-aligned (``epoch_us // width``) and folds its
   inputs into a ``Cell``: sample and valid counts, minimum, maximum, sum,
   and the union of quality flags. Rollup buckets fold in with their own
   counts, ``mean x valid_count`` as the sum, and their minimum and maximum.
4. ``bucket_points`` emits one point per bucket of a span, ``None`` where no
   valid value exists (STORE-012), with the min/max envelope so peaks
   survive (10.6). The first point sits at the span start when its bucket
   begins earlier.

Dependencies:
    vakr.domain.series, series_assembly, rollups: Read models and spans.

Safety:
    Pure; a bucket without a valid value is a gap, never zero.

Example:
    >>> ladder_width(86_400, 1200), ladder_width(30 * 86_400, 1200)
    (120, 3600)
    >>> c = Cell()
    >>> c.add(2, 2, 1.0, 3.0, 4.0, 0)
    >>> [p.value for p in bucket_points({0: c}, (0, 120_000_000), 60, 10)]
    [2.0, None]
"""

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime

from vakr.domain.enums import SeriesSource
from vakr.domain.errors import ValidationError
from vakr.domain.quality import Quality
from vakr.domain.rollups import ROLLUP_BUCKET_SECONDS
from vakr.domain.series import SeriesPoint, SeriesResolution, TimeWindow
from vakr.domain.series_assembly import resolution_spans
from vakr.domain.timestamps import from_epoch_us

LADDER_S = (60, 120, 300, 600, 900, 1800, 3600, 7200, 10800, 14400, 21600, 28800, 43200, 86400)
"""Downsampling bucket widths in seconds, narrowest first (ADR-021, E.21)."""

_US = 1_000_000


def ladder_width(duration_s: float, max_points: int) -> int:
    """Return the narrowest ladder width keeping a window within ``max_points``.

    Two points are reserved for the partial buckets at the window's ends
    and at a native/rollup boundary.

    Args:
        duration_s: Window length in seconds.
        max_points: Point cap.

    Returns:
        int: Bucket width in seconds; the widest rung if none fits.

    Raises:
        None
    """
    return next((w for w in LADDER_S if -(-duration_s // w) + 2 <= max_points), LADDER_S[-1])


def downsample_spans(
    window: TimeWindow, horizon: datetime | None, *, rollups: bool, source: SeriesSource | None
) -> tuple[SeriesResolution, ...]:
    """Return the spans a downsampled read covers, oldest first.

    Args:
        window: The requested window.
        horizon: Time before which native data has expired, or ``None``.
        rollups: Whether the backend keeps rollups.
        source: ``None`` to split at the horizon, else the one source for
            the whole window.

    Returns:
        tuple[SeriesResolution, ...]: Spans tiling the window; widths are
        filled in by the reader.

    Raises:
        ValidationError: If ``ROLLUP`` is forced on a backend without rollups.
    """
    if source is None:
        return resolution_spans(window, horizon, rollups=rollups)
    if source is SeriesSource.ROLLUP and not rollups:
        raise ValidationError("this telemetry store keeps no rollups", field="source")
    width = ROLLUP_BUCKET_SECONDS if source is SeriesSource.ROLLUP else None
    return (SeriesResolution(source, window.start, window.end, width),)


def span_width(source: SeriesSource, width: int) -> int:
    """Return the bucket width a span uses: rollups never go below 15 minutes.

    Args:
        source: The span's source.
        width: The ladder width for the whole window.

    Returns:
        int: Bucket width in seconds.

    Raises:
        None
    """
    return max(width, ROLLUP_BUCKET_SECONDS) if source is SeriesSource.ROLLUP else width


@dataclass(slots=True)
class Cell:
    """One output bucket while partial aggregates are folded into it.

    Attributes:
        count: Samples (or rollup samples) seen.
        valid: Samples with a value.
        low: Smallest value, once ``valid > 0``.
        high: Largest value, once ``valid > 0``.
        total: Sum of values (rollups: mean times valid count).
        quality: Union of quality flags.
    """

    count: int = 0
    valid: int = 0
    low: float | None = None
    high: float | None = None
    total: float = 0.0
    quality: int = 0

    def add(
        self, count: int, valid: int, low: float | None, high: float | None, total: float,
        quality: int,
    ) -> None:  # fmt: skip
        """Fold one partial aggregate in.

        Args:
            count: Its samples.
            valid: Its samples with a value.
            low: Its minimum, or ``None`` when ``valid`` is 0.
            high: Its maximum, or ``None`` when ``valid`` is 0.
            total: Its sum.
            quality: Its quality flags.

        Returns:
            None

        Raises:
            None
        """
        self.count += count
        if valid and low is not None and high is not None:
            self.low = low if self.low is None else min(self.low, low)
            self.high = high if self.high is None else max(self.high, high)
            self.valid += valid
            self.total += total
        self.quality |= quality


def bucket_points(
    cells: Mapping[int, Cell], bounds: tuple[int, int], width: int, room: int
) -> list[SeriesPoint]:
    """Emit one point per bucket of a span, gaps as ``None``, at most ``room`` of them.

    Args:
        cells: Folded buckets by index (``epoch_us // (width * 1e6)``).
        bounds: ``(start_us, end_us)`` of the span.
        width: Bucket width in seconds.
        room: Most points to emit; the caller truncates past its cap.

    Returns:
        list[SeriesPoint]: Points in time order; the first bucket's point sits
        at the span start when the bucket begins before it.

    Raises:
        None
    """
    w = width * _US
    out = []
    first = bounds[0] // w
    for bucket in range(first, min(-(-bounds[1] // w), first + max(room, 0))):
        at = from_epoch_us(max(bucket * w, bounds[0]))
        cell = cells.get(bucket, Cell())
        if not cell.valid or cell.low is None or cell.high is None:
            out.append(SeriesPoint(at, None, Quality(cell.quality)))
            continue
        mean = min(max(cell.total / cell.valid, cell.low), cell.high)
        out.append(SeriesPoint(at, mean, Quality(cell.quality), cell.low, cell.high))
    return out
