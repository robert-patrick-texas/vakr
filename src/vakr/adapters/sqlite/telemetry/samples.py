"""Native samples and rollup point budgets for one series (DB-004, DB-006, API-006).

``native_samples`` reads one subject's metric from one collector over a
span as a ``UNION ALL`` of primary-key ranges, one arm per day partition
the catalog lists plus the staging arm, ordered by ``(ts_us,
observation_uuid)``, inside the caller's read transaction (ADR-021). It
stops once it has the distinct timestamps the point cap can use, keeping
every row that shares a timestamp so ``assemble_series`` can apply the
larger-UUID rule.

Dependencies:
    sqlite3: Statements on the caller's read transaction.
    vakr.adapters.sqlite.telemetry.*: Read helpers, keys, and layout.
    vakr.domain: Native samples and quality.

Safety:
    Reads are bounded by the span and the budget; table names come from the catalog.

Example:
    >>> from datetime import UTC, datetime
    >>> bucket_starts(datetime(2026, 1, 1, tzinfo=UTC), datetime(2026, 1, 1, 1, tzinfo=UTC))
    4
"""

import itertools
import sqlite3
from datetime import datetime
from uuid import UUID

from vakr.adapters.sqlite.encoding import decode_time
from vakr.adapters.sqlite.telemetry.keys import find_key
from vakr.adapters.sqlite.telemetry.layout import METRIC_COLUMN
from vakr.adapters.sqlite.telemetry.reads import native_start, sources, subject_key, union
from vakr.adapters.sqlite.telemetry.store import SqliteTelemetryStore
from vakr.domain.quality import Quality
from vakr.domain.rollups import ROLLUP_BUCKET_SECONDS
from vakr.domain.series import Metric
from vakr.domain.series_assembly import NativeSample
from vakr.domain.timestamps import to_epoch_us


def bucket_starts(start: datetime, end: datetime) -> int:
    """Count the 15-minute bucket starts a rollup span shows as points.

    Args:
        start: Span start.
        end: Span end.

    Returns:
        int: Aligned bucket starts in ``[start, end)``.

    Raises:
        None
    """
    width = ROLLUP_BUCKET_SECONDS * 1_000_000
    first = -(-to_epoch_us(start) // width) * width
    return max(0, -(-(to_epoch_us(end) - first) // width))


def native_samples(
    store: SqliteTelemetryStore, conn: sqlite3.Connection, family: str, subject: UUID,
    collector: UUID, metric: Metric, span: tuple[int, int], budget: int,
) -> list[NativeSample]:  # fmt: skip
    """Fetch one span's native samples, up to ``budget`` distinct timestamps.

    Args:
        store: The store (for its key cache).
        conn: Connection inside the read transaction.
        family: The metric's native family.
        subject: Interface or device.
        collector: The collector.
        metric: The metric.
        span: ``(start_us, end_us)``.
        budget: Distinct timestamps wanted.

    Returns:
        list[NativeSample]: Samples in time order, equal timestamps included.

    Raises:
        sqlite3.Error: If a query fails.
    """
    key = subject_key(conn, store.keys, family, subject)
    col = find_key(conn, store.keys, "collector", collector)
    if key is None or col is None or budget <= 0:
        return []
    start = native_start(conn, span[0])
    columns = f"ts_us, observation_uuid, {METRIC_COLUMN[metric]}, quality_flags"
    where = "series_key = :s AND collector_key = :c AND ts_us >= :a AND ts_us < :b"
    sql = union(columns, sources(conn, family, start, span[1]), where)
    rows = conn.execute(
        f"{sql} ORDER BY ts_us, observation_uuid", {"s": key, "c": col, "a": start, "b": span[1]}
    )
    out: list[NativeSample] = []
    try:
        # Stop reading once the cap is met; rows sharing a timestamp arrive together.
        for ts, group in itertools.groupby(rows, key=lambda r: r[0]):
            if budget == 0:
                break
            budget -= 1
            out.extend(
                NativeSample(decode_time(ts), UUID(bytes=r[1]),
                             None if r[2] is None else float(r[2]), Quality(r[3]))
                for r in group
            )  # fmt: skip
    finally:
        rows.close()
    return out
