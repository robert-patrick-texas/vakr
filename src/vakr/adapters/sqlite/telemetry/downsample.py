"""Downsampled series on the fixed width ladder (blueprint 10.6, ADR-021, E.19).

The port's series return one point per observation and truncate at the
cap, which suits 1-24 hour graphs. A 7-day or 30-day graph needs server
side downsampling instead, so ``downsampled_series`` picks the narrowest
ladder width (60 s ... 1 day) that keeps the whole window within the point
cap and returns one point per UTC-aligned bucket: the mean, with the min
and max envelope so peaks survive, and the union of quality flags. A
bucket with no samples is an explicit ``None`` gap (STORE-012).

Native buckets are aggregated in SQL (``GROUP BY`` bucket and quality
flags over the same ``UNION ALL`` of key ranges as raw series; SQLite has
no bitwise-OR aggregate, so the few flag combinations per bucket are
merged in Python). Before the native horizon, 15-minute rollups are
re-bucketed to at least 15 minutes: the valid-weighted mean, the minimum
of minimums, and the maximum of maximums. Each span's resolution and
bucket width are disclosed (STATE-005). A caller may force one source for
the whole window (``source``); the bucketing rules are the backend-neutral
ones in ``vakr.domain.downsampling``, which the memory adapter shares
(``HistoryQuery.downsampled_series``, M4).

Dependencies:
    vakr.adapters.sqlite.telemetry.*: Read helpers and rollup reads.
    vakr.domain: Series read models, spans, and the bucketing rules.

Safety:
    Every result has at most ``max_points`` points; table names come from
    the catalog.

Example:
    >>> ladder_width(86_400, 1200), ladder_width(30 * 86_400, 1200)
    (120, 3600)
"""

# Aggregation SQL interpolates checked partition names and integer widths only.
# ruff: noqa: S608

import sqlite3
from collections import defaultdict
from uuid import UUID

from vakr.adapters.sqlite.telemetry.keys import find_key
from vakr.adapters.sqlite.telemetry.layout import FAMILY_OF, METRIC_COLUMN
from vakr.adapters.sqlite.telemetry.reads import (
    horizon,
    native_start,
    sources,
    subject_key,
    union,
)
from vakr.adapters.sqlite.telemetry.rollup_reads import stored_slots
from vakr.adapters.sqlite.telemetry.store import SqliteTelemetryStore
from vakr.domain.downsampling import (
    Cell,
    bucket_points,
    downsample_spans,
    ladder_width,
    span_width,
)
from vakr.domain.enums import SeriesSource
from vakr.domain.errors import ValidationError
from vakr.domain.ids import CollectorId
from vakr.domain.rollups import ROLLUP_BUCKET_SECONDS
from vakr.domain.series import (
    MAX_SERIES_POINTS,
    Metric,
    Series,
    SeriesPoint,
    SeriesResolution,
    TimeWindow,
)
from vakr.domain.timestamps import from_epoch_us, to_epoch_us

__all__ = ["downsampled_series", "ladder_width"]

_US = 1_000_000


def downsampled_series(
    store: SqliteTelemetryStore, subject: UUID, collector: CollectorId, metric: Metric,
    window: TimeWindow, max_points: int, source: SeriesSource | None = None,
) -> Series:  # fmt: skip
    """Return a bucketed series as the module docstring describes.

    Args:
        store: The open store.
        subject: Interface or device.
        collector: The collector.
        metric: The metric.
        window: The window.
        max_points: Point cap, 1 to ``MAX_SERIES_POINTS``.
        source: ``None`` to split at the native horizon, else the one source
            for the whole window.

    Returns:
        Series: Points per bucket, spans with their widths, and truncation.

    Raises:
        ValidationError: If ``max_points`` is out of range.
    """
    if not 1 <= max_points <= MAX_SERIES_POINTS:
        raise ValidationError("max_points is out of range", field="max_points")
    width = ladder_width(window.duration.total_seconds(), max_points)
    points: list[SeriesPoint] = []
    shown: list[SeriesResolution] = []
    with store.reading() as conn:
        cut = horizon(conn)
        spans = downsample_spans(window, None if cut is None else from_epoch_us(cut),
                                 rollups=True, source=source)  # fmt: skip
        for span in spans:
            bounds = (to_epoch_us(span.start), to_epoch_us(span.end))
            w = span_width(span.source, width)
            if span.source is SeriesSource.ROLLUP:
                cells = _rollup_cells(store, conn, subject, collector, metric, bounds, w)
            else:
                cells = _native_cells(store, conn, subject, collector, metric, bounds, w)
            points.extend(bucket_points(cells, bounds, w, max_points + 1 - len(points)))
            shown.append(SeriesResolution(span.source, span.start, span.end, w))
    return Series(subject, collector, metric, window, tuple(points[:max_points]), tuple(shown),
                  len(points) > max_points)  # fmt: skip


def _native_cells(
    store: SqliteTelemetryStore, conn: sqlite3.Connection, subject: UUID, collector: UUID,
    metric: Metric, bounds: tuple[int, int], width: int,
) -> dict[int, Cell]:  # fmt: skip
    """Aggregate native samples per bucket in SQL.

    Args:
        store: The store.
        conn: Connection inside the read transaction.
        subject: Interface or device.
        collector: The collector.
        metric: The metric.
        bounds: ``(start_us, end_us)`` of the native span.
        width: Bucket width in seconds.

    Returns:
        dict[int, Cell]: Bucket index to its merged aggregates.

    Raises:
        sqlite3.Error: If the query fails.
    """
    family = FAMILY_OF[metric.observation_type]
    s = subject_key(conn, store.keys, family, subject)
    c = find_key(conn, store.keys, "collector", collector)
    cells: dict[int, Cell] = defaultdict(Cell)
    if s is None or c is None:
        return cells
    # A forced native span may start before the horizon; expired rows must not count.
    start = native_start(conn, bounds[0])
    where = "series_key = :s AND collector_key = :c AND ts_us >= :a AND ts_us < :b"
    arms = union(f"ts_us, quality_flags, {METRIC_COLUMN[metric]} AS v",
                 sources(conn, family, start, bounds[1]), where)  # fmt: skip
    rows = conn.execute(
        f"SELECT ts_us / {int(width) * _US}, quality_flags, count(*), count(v), min(v), max(v), "
        f"total(v) FROM ({arms}) GROUP BY 1, 2",
        {"s": s, "c": c, "a": start, "b": bounds[1]},
    )
    for bucket, flags, count, valid, low, high, total in rows:
        lo, hi = (None, None) if low is None else (float(low), float(high))
        cells[bucket].add(count, valid, lo, hi, float(total), flags)
    return cells


def _rollup_cells(
    store: SqliteTelemetryStore, conn: sqlite3.Connection, subject: UUID, collector: UUID,
    metric: Metric, bounds: tuple[int, int], width: int,
) -> dict[int, Cell]:  # fmt: skip
    """Re-bucket stored 15-minute rollups to ``width``.

    Args:
        store: The store.
        conn: Connection inside the read transaction.
        subject: Interface or device.
        collector: The collector.
        metric: The metric.
        bounds: ``(start_us, end_us)`` of the rollup span.
        width: Bucket width in seconds, at least 15 minutes.

    Returns:
        dict[int, Cell]: Bucket index to its merged aggregates.

    Raises:
        StorageError: If a stored payload is damaged.
        sqlite3.Error: If a query fails.
    """
    cells: dict[int, Cell] = defaultdict(Cell)
    for start, _seconds, a in stored_slots(store, conn, subject, collector, metric, bounds,
                                           ROLLUP_BUCKET_SECONDS):  # fmt: skip
        cells[start // (width * _US)].add(a.sample_count, a.valid_count, a.minimum, a.maximum,
                                          (a.mean or 0.0) * a.valid_count, a.quality)  # fmt: skip
    return cells
