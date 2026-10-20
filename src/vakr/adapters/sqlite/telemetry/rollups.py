"""``RollupRepository`` for the SQLite telemetry store (RET-002..006, RET-009, E.10).

Buckets are stored in one partition per UTC month (``roll_iface_mYYYYMM``
and ``roll_icmp_mYYYYMM``), one row per subject, collector, and bucket,
with every metric of that bucket packed into the row's payload. Writing a
bucket whose identity ``(subject, collector, metric, start, width)`` is
already stored replaces that metric's slot and leaves the row's other
metrics alone, so a re-run rollup job is safe (RET-009). The catalog keeps
each month's latest bucket end, which is ``coverage_end``, and its bucket
count, which retention reports when it drops the month.

Interface rollup values are stored at float32 precision when they need
more than four bytes exactly, which keeps 400 days of all six interface
metrics inside the 27.3 disk envelope (E.21, ADR-021 amendment 3). ICMP
rollups are exact.

Dependencies:
    vakr.adapters.sqlite.telemetry.*: Codec, catalog, keys, reads, and the store.
    vakr.domain: ``RollupBucket`` and errors.

Safety:
    Writes run in one transaction; table names come from ``layout``.

Example:
    >>> SqliteRollupRepository.__name__
    'SqliteRollupRepository'
"""

# Rollup SQL interpolates checked partition names only; values are bound parameters.
# ruff: noqa: S608

import sqlite3
from collections import defaultdict
from collections.abc import Sequence
from datetime import datetime
from uuid import UUID

from vakr.adapters.sqlite.encoding import decode_time, encode_time
from vakr.adapters.sqlite.telemetry.keys import KeyAllocator
from vakr.adapters.sqlite.telemetry.layout import ROLLUP_FAMILY_OF, month_bounds
from vakr.adapters.sqlite.telemetry.partitions import ROLLUP_HORIZON, ensure_month, state
from vakr.adapters.sqlite.telemetry.rollup_codec import Aggregates, decode_payload, encode_payload
from vakr.adapters.sqlite.telemetry.rollup_reads import read_buckets, rollup_dictionary
from vakr.adapters.sqlite.telemetry.store import SqliteTelemetryStore
from vakr.adapters.sqlite.telemetry.tuning import QUANTIZED_ROLLUPS
from vakr.adapters.sqlite.telemetry.vocab import ROLLUP_SLOTS
from vakr.domain.checks import require_int
from vakr.domain.errors import ValidationError
from vakr.domain.ids import CollectorId
from vakr.domain.rollups import ROLLUP_BUCKET_SECONDS
from vakr.domain.series import MAX_SERIES_POINTS, Metric, TimeWindow
from vakr.domain.state import RollupBucket
from vakr.domain.timestamps import to_epoch_us

type RowKey = tuple[str, UUID, UUID, int, int]
"""``(family, subject, collector, bucket start µs, bucket seconds)``: one stored row."""


def _aggregates(b: RollupBucket) -> Aggregates:
    """Extract the stored fields of a bucket.

    Args:
        b: The bucket.

    Returns:
        Aggregates: Counts, values, and quality.

    Raises:
        None
    """
    return Aggregates(b.expected_count, b.sample_count, b.valid_count, b.minimum, b.mean, b.p95,
                      b.maximum, int(b.quality))  # fmt: skip


class SqliteRollupRepository:
    """Stores and serves 15-minute (or other fixed-width) rollup buckets."""

    def __init__(self, store: SqliteTelemetryStore) -> None:
        """Bind the repository.

        Args:
            store: The open store.

        Returns:
            None

        Raises:
            None
        """
        self._store = store

    def write(self, buckets: Sequence[RollupBucket]) -> int:
        """Insert or replace buckets by identity, in one transaction (RET-009).

        Args:
            buckets: Buckets to store; a later bucket with the same identity wins.

        Returns:
            int: Number of buckets written.

        Raises:
            ValidationError: If an item is not a ``RollupBucket``; nothing is written.
            StorageError: If the database is unavailable; nothing is committed.
        """
        rows: dict[RowKey, dict[int, Aggregates]] = defaultdict(dict)
        for b in buckets:
            if not isinstance(b, RollupBucket):
                raise ValidationError(f"{type(b).__name__} is not a rollup bucket")
            family = ROLLUP_FAMILY_OF[b.metric.observation_type]
            key = (family, b.subject_id, b.collector_id, encode_time(b.bucket_start),
                   b.bucket_seconds)  # fmt: skip
            rows[key][ROLLUP_SLOTS[family][b.metric]] = _aggregates(b)
        if rows:
            with self._store.writing() as conn:
                keys = KeyAllocator(conn, self._store.keys)
                _store_rows(conn, keys, rows)
            keys.publish()
        return len(buckets)

    def read(
        self, subject_id: UUID, collector_id: CollectorId, metric: Metric, window: TimeWindow,
        *, limit: int,
    ) -> tuple[RollupBucket, ...]:  # fmt: skip
        """Return buckets whose start lies in the window, oldest first.

        Args:
            subject_id: Interface or device.
            collector_id: The collector.
            metric: The metric.
            window: Window on ``bucket_start``.
            limit: At most this many buckets, 1 to ``MAX_SERIES_POINTS``.

        Returns:
            tuple[RollupBucket, ...]: The buckets.

        Raises:
            ValidationError: If ``limit`` is out of range.
        """
        require_int(limit, "limit", minimum=1, maximum=MAX_SERIES_POINTS)
        span = (to_epoch_us(window.start), to_epoch_us(window.end))
        with self._store.reading() as conn:
            found = read_buckets(self._store, conn, subject_id, collector_id, metric, span, limit)
        return tuple(found)

    def coverage_end(self) -> datetime | None:
        """Return the latest ``bucket_end`` of any stored bucket.

        Args:
            None

        Returns:
            datetime | None: The rollup frontier, or ``None`` if none stored.

        Raises:
            None
        """
        with self._store.reading() as conn:
            return coverage_end_us(conn)


def coverage_end_us(conn: sqlite3.Connection) -> datetime | None:
    """Return the rollup frontier from the catalog, ignoring expired buckets.

    Args:
        conn: Connection inside a transaction.

    Returns:
        datetime | None: The latest stored bucket end, or ``None``.

    Raises:
        sqlite3.Error: If the catalog cannot be read.
    """
    row = conn.execute(
        "SELECT max(max_end_us) FROM partition_catalog WHERE substr(family, 1, 5) = 'roll_'"
    ).fetchone()
    expired = state(conn, ROLLUP_HORIZON)
    if row[0] is None or (expired is not None and row[0] <= expired):
        return None
    return decode_time(row[0])


def _store_rows(
    conn: sqlite3.Connection, keys: KeyAllocator, rows: dict[RowKey, dict[int, Aggregates]]
) -> None:
    """Merge each row's new slots into what is stored, and update the catalog.

    Args:
        conn: Connection holding the write lock.
        keys: The transaction's key allocator.
        rows: Row identity to the slots being written.

    Returns:
        None

    Raises:
        StorageError: If a stored payload is damaged.
        sqlite3.Error: If a statement fails; the caller rolls back.
    """
    months: dict[tuple[str, int], str] = {}
    prepared = []
    for (family, subject, collector, start_us, seconds), slots in rows.items():
        month = month_bounds(start_us)[0]
        if (family, month) not in months:
            months[family, month] = ensure_month(conn, family, start_us)
        pk = (keys.key(rollup_dictionary(family), subject), keys.key("collector", collector),
              (start_us - month) // 1_000_000, seconds - ROLLUP_BUCKET_SECONDS)  # fmt: skip
        prepared.append((months[family, month], pk, family, start_us + seconds * 1_000_000, slots))
    prepared.sort(key=lambda p: (p[0], p[1]))  # key order touches each page once
    added: dict[str, list[int]] = defaultdict(lambda: [0, 0, 0])
    where = "series_key = ? AND collector_key = ? AND bucket_offset_s = ? AND width_adj_s = ?"
    for table, pk, family, end_us, slots in prepared:
        old = conn.execute(f"SELECT payload FROM {table} WHERE {where}", pk).fetchone()
        merged = {} if old is None else decode_payload(old[0])
        stats = added[table]
        stats[0] += len(slots.keys() - merged.keys())
        stats[1] = max(stats[1], end_us)
        stats[2] = max(stats[2], pk[3] + ROLLUP_BUCKET_SECONDS)
        merged.update(slots)
        payload = encode_payload(merged, quantize=family in QUANTIZED_ROLLUPS)
        conn.execute(f"INSERT OR REPLACE INTO {table} VALUES (?, ?, ?, ?, ?)", (*pk, payload))
    for table, (count, end_us, width) in added.items():
        conn.execute(
            "UPDATE partition_catalog SET bucket_count = bucket_count + ?, "
            "max_end_us = max(coalesce(max_end_us, 0), ?), "
            "max_width_s = max(coalesce(max_width_s, 0), ?) WHERE table_name = ?",
            (count, end_us, width, table),
        )
