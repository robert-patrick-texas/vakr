"""Reading rollup buckets back out of the monthly partitions (RET-002, RET-004, 10.5).

A rollup row holds every metric of one subject, collector, and bucket in a
packed payload (``rollup_codec``). Reading one metric seeks the row range
of ``(series_key, collector_key)`` in each month partition the window
touches, decodes the metric's slot, and skips rows that do not hold it.
Rows come back ordered by ``(bucket_start, bucket_seconds)``, the order the
port defines, because that is the partition's clustering order and months
are read oldest first.

Dependencies:
    vakr.adapters.sqlite.telemetry.*: Layout, catalog, keys, and the codec.
    vakr.domain: ``RollupBucket``, metrics, and series spans.

Safety:
    Every read is bounded by a limit; table names come from the catalog.

Example:
    >>> rollup_dictionary("roll_iface"), rollup_dictionary("roll_icmp")
    ('interface', 'device')
"""

# Rollup SQL interpolates checked partition names only; values are bound parameters.
# ruff: noqa: S608

import sqlite3
from collections.abc import Iterator
from uuid import UUID

from vakr.adapters.sqlite.encoding import decode_time
from vakr.adapters.sqlite.telemetry.codec_rows import rebuild
from vakr.adapters.sqlite.telemetry.keys import find_key
from vakr.adapters.sqlite.telemetry.layout import ROLLUP_FAMILY_OF
from vakr.adapters.sqlite.telemetry.partitions import ROLLUP_HORIZON, spans, state
from vakr.adapters.sqlite.telemetry.rollup_codec import Aggregates, decode_slot
from vakr.adapters.sqlite.telemetry.store import SqliteTelemetryStore
from vakr.adapters.sqlite.telemetry.vocab import ROLLUP_SLOTS
from vakr.domain.ids import CollectorId
from vakr.domain.quality import Quality
from vakr.domain.rollups import ROLLUP_BUCKET_SECONDS
from vakr.domain.series import Metric, SeriesResolution
from vakr.domain.state import RollupBucket
from vakr.domain.timestamps import to_epoch_us

_US = 1_000_000


def rollup_dictionary(family: str) -> str:
    """Return the dictionary of a rollup family's subjects.

    Args:
        family: ``roll_iface`` or ``roll_icmp``.

    Returns:
        str: ``interface`` or ``device``.

    Raises:
        None
    """
    return "interface" if family == "roll_iface" else "device"


def to_bucket(
    subject: UUID, collector: UUID, metric: Metric, start_us: int, seconds: int, a: Aggregates
) -> RollupBucket:
    """Rebuild a ``RollupBucket`` from a decoded slot.

    Args:
        subject: Interface or device.
        collector: The collector.
        metric: The slot's metric.
        start_us: Bucket start, microseconds.
        seconds: Bucket width.
        a: The slot's aggregates.

    Returns:
        RollupBucket: The bucket.

    Raises:
        StorageError: If the stored values break a bucket rule.
    """
    return rebuild(
        RollupBucket, subject_id=subject, collector_id=CollectorId(collector), metric=metric,
        bucket_start=decode_time(start_us), bucket_seconds=seconds,
        expected_count=a.expected_count, sample_count=a.sample_count, valid_count=a.valid_count,
        minimum=a.minimum, mean=a.mean, p95=a.p95, maximum=a.maximum, quality=Quality(a.quality),
    )  # fmt: skip


def read_buckets(
    store: SqliteTelemetryStore, conn: sqlite3.Connection, subject: UUID, collector: UUID,
    metric: Metric, window: tuple[int, int], limit: int, *, width: int | None = None,
) -> list[RollupBucket]:  # fmt: skip
    """Return a metric's buckets starting in ``[start_us, end_us)``, oldest first.

    Args:
        store: The store (for its key cache).
        conn: Connection inside a read transaction.
        subject: Interface or device.
        collector: The collector.
        metric: The metric.
        window: ``(start_us, end_us)`` on ``bucket_start``.
        limit: At most this many buckets.
        width: Only buckets of this width, or every width.

    Returns:
        list[RollupBucket]: The buckets.

    Raises:
        StorageError: If a stored payload is damaged.
        sqlite3.Error: If a query fails.
    """
    found: list[RollupBucket] = []
    for start_us, seconds, a in stored_slots(store, conn, subject, collector, metric, window,
                                             width):  # fmt: skip
        if len(found) >= limit:
            break
        found.append(to_bucket(subject, collector, metric, start_us, seconds, a))
    return found


def stored_slots(
    store: SqliteTelemetryStore, conn: sqlite3.Connection, subject: UUID, collector: UUID,
    metric: Metric, window: tuple[int, int], width: int | None,
) -> Iterator[tuple[int, int, Aggregates]]:  # fmt: skip
    """Yield ``(bucket start, width, aggregates)`` of one metric's live buckets, oldest first.

    Args:
        store: The store.
        conn: Connection inside a read transaction.
        subject: Interface or device.
        collector: The collector.
        metric: The metric.
        window: ``(start_us, end_us)`` on ``bucket_start``.
        width: Only this width, or every width.

    Returns:
        Iterator[tuple[int, int, Aggregates]]: The stored slots.

    Raises:
        StorageError: If a stored payload is damaged.
        sqlite3.Error: If a query fails.
    """
    family = ROLLUP_FAMILY_OF[metric.observation_type]
    s = find_key(conn, store.keys, rollup_dictionary(family), subject)
    c = find_key(conn, store.keys, "collector", collector)
    if s is None or c is None:
        return
    slot = ROLLUP_SLOTS[family][metric]
    expired = state(conn, ROLLUP_HORIZON) or -(1 << 62)
    where = "" if width is None else " AND width_adj_s = :w"
    for name, month_us in spans(conn, family, *window):
        rows = conn.execute(
            f"SELECT bucket_offset_s, width_adj_s, payload FROM {name} WHERE series_key = :s "
            f"AND collector_key = :c AND bucket_offset_s >= :lo AND bucket_offset_s < :hi{where} "
            "ORDER BY bucket_offset_s, width_adj_s",
            {"s": s, "c": c, "w": (width or 0) - ROLLUP_BUCKET_SECONDS,
             "lo": -(-(window[0] - month_us) // _US), "hi": -(-(window[1] - month_us) // _US)},
        )  # fmt: skip
        try:
            for offset, adj, payload in rows:
                start, seconds = month_us + offset * _US, adj + ROLLUP_BUCKET_SECONDS
                stored = decode_slot(payload, slot)
                # Buckets ending at or before the rollup horizon have expired.
                if stored is not None and start + seconds * _US > expired:
                    yield start, seconds, stored
        finally:
            rows.close()


def buckets_for_span(
    store: SqliteTelemetryStore, conn: sqlite3.Connection, subject: UUID, collector: UUID,
    metric: Metric, span: SeriesResolution, budget: int,
) -> list[RollupBucket]:  # fmt: skip
    """Return the reference-width buckets a series' rollup span can show.

    Args:
        store: The store.
        conn: Connection inside a read transaction.
        subject: Interface or device.
        collector: The collector.
        metric: The metric.
        span: A rollup span from ``resolution_spans``.
        budget: Points the series can still show.

    Returns:
        list[RollupBucket]: At most ``budget`` 15-minute buckets in the span.

    Raises:
        StorageError: If a stored payload is damaged.
        sqlite3.Error: If a query fails.
    """
    window = (to_epoch_us(span.start), to_epoch_us(span.end))
    return read_buckets(store, conn, subject, collector, metric, window, max(budget, 0),
                        width=ROLLUP_BUCKET_SECONDS)  # fmt: skip
