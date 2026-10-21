"""``RankingProjection`` for the SQLite telemetry store: ``rank_unit`` and ``rank_iface`` (E.28).

Migration 0002 adds the two tables. ``project(bucket)`` runs inside one
``BEGIN IMMEDIATE`` transaction: it scans the bucket's native interface
samples of every collector with the same grouped statements a ranking read
uses (``rankings.scan``), replaces the bucket's ``rank_iface`` rows
with one packed ``Partial`` per collector and interface, and lists the
bucket in ``rank_unit``. Once all four buckets of an hour are listed, the
same transaction compacts them into one hour unit, so a 24-hour ranking
reads about 24 rows per interface instead of 96. Holding the write lock
across the scan is what keeps the projection exact: no sample can land in
the bucket between the read and the write. The other writers keep it
exact afterwards:

- ``forget_buckets`` runs inside every batch write that stores interface
  samples, and unlists the buckets and hours they fall in (``writer.py``);
- ``prune`` runs inside every retention pass and removes units before the
  native horizon or older than ``PROJECTION_KEEP`` (``retention.py``).

Dependencies:
    sqlite3, time: Statements on the store's transactions; build times.
    vakr.adapters.sqlite.telemetry.*: Partials, read helpers, and the store.
    vakr.ports.telemetry.ranking_projection: Bounds and checks.

Safety:
    One bucket (and at most one hour) per write transaction, at most 20,000
    interfaces per collector (E.28); listings cover at most 25 hours. Every
    value is a bound parameter.

Example:
    >>> _starts((0, 2_700_000_000), 2)
    [1800000000, 900000000]
"""

# Statements interpolate the fixed _COLLECTORS condition only; values are bound parameters.
# ruff: noqa: S608

import sqlite3
import time
from collections.abc import Iterable
from datetime import datetime

from vakr.adapters.sqlite.telemetry.ranking_parts import Partial
from vakr.adapters.sqlite.telemetry.rankings import BUCKET_US, HOUR_US, scan
from vakr.adapters.sqlite.telemetry.reads import horizon, native_start
from vakr.adapters.sqlite.telemetry.store import SqliteTelemetryStore
from vakr.domain.series import TimeWindow
from vakr.domain.timestamps import from_epoch_us, to_epoch_us
from vakr.ports.telemetry.ranking_projection import (
    MAX_PROJECTION_BUCKETS,
    PROJECTION_KEEP,
    check_bucket,
    check_projection_window,
)

_KEEP_US = int(PROJECTION_KEEP.total_seconds()) * 1_000_000
_COLLECTORS = "collector_key IN (SELECT collector_key FROM dict_collector)"
_QUARTER, _HOUR = BUCKET_US // 1_000_000, HOUR_US // 1_000_000

type Packed = tuple[int, int, bytes]


def _starts(bounds: tuple[int, int], limit: int) -> list[int]:
    """List the bucket starts lying wholly inside ``[start, end)``, newest first.

    Args:
        bounds: ``(start_us, end_us)``.
        limit: Most starts.

    Returns:
        list[int]: Bucket starts, microseconds.

    Raises:
        None
    """
    first = -(-bounds[0] // BUCKET_US) * BUCKET_US
    last = (bounds[1] // BUCKET_US - 1) * BUCKET_US
    return list(range(last, first - 1, -BUCKET_US))[:limit]


def forget_buckets(conn: sqlite3.Connection, ts_us: Iterable[int]) -> None:
    """Unlist the buckets and hours holding these interface sample times (writer hook).

    Args:
        conn: Connection holding the write lock, inside the batch transaction.
        ts_us: Timestamps of interface samples the batch stores.

    Returns:
        None

    Raises:
        sqlite3.Error: If the statement fails; the caller rolls back.
    """
    units = set()
    for ts in ts_us:
        units.add((_QUARTER, ts - ts % BUCKET_US))
        units.add((_HOUR, ts - ts % HOUR_US))
    conn.executemany("DELETE FROM rank_unit WHERE width_s = ? AND start_us = ?", sorted(units))


def prune(conn: sqlite3.Connection, now_us: int) -> None:
    """Remove units before the native horizon or older than ``PROJECTION_KEEP`` (retention hook).

    Args:
        conn: Connection holding the write lock, inside the retention transaction.
        now_us: The retention pass's reference time.

    Returns:
        None

    Raises:
        sqlite3.Error: If a statement fails; the caller rolls back.
    """
    cut = now_us - _KEEP_US - (now_us - _KEEP_US) % BUCKET_US
    expired = horizon(conn)
    cut = cut if expired is None else max(cut, expired)
    conn.execute("DELETE FROM rank_unit WHERE width_s IN (900, 3600) AND start_us < ?", (cut,))
    conn.execute(
        f"DELETE FROM rank_iface WHERE {_COLLECTORS} AND width_s IN (900, 3600) AND start_us < ?",
        (cut,),
    )


def _replace(conn: sqlite3.Connection, width_s: int, start: int, rows: list[Packed]) -> None:
    """Replace one unit's partials and list the unit.

    Args:
        conn: Connection holding the write lock.
        width_s: 900 or 3600.
        start: The unit start, microseconds.
        rows: ``(collector_key, series_key, payload)`` per partial.

    Returns:
        None

    Raises:
        sqlite3.Error: If a statement fails; the caller rolls back.
    """
    conn.execute(f"DELETE FROM rank_iface WHERE {_COLLECTORS} AND width_s = ? AND start_us = ?",
                 (width_s, start))  # fmt: skip
    conn.executemany(
        "INSERT INTO rank_iface (collector_key, width_s, start_us, series_key, payload) "
        "VALUES (?, ?, ?, ?, ?)",
        [(c, width_s, start, s, payload) for c, s, payload in rows],
    )
    conn.execute("INSERT OR REPLACE INTO rank_unit (width_s, start_us, built_us) VALUES (?, ?, ?)",
                 (width_s, start, time.time_ns() // 1000))  # fmt: skip


def _partials(conn: sqlite3.Connection, bounds: tuple[int, int]) -> list[Packed]:
    """Scan one bucket for every collector and pack one partial per collector and interface.

    Args:
        conn: Connection holding the write lock.
        bounds: The bucket's ``(start_us, end_us)``.

    Returns:
        list[Packed]: ``(collector_key, series_key, payload)`` rows.

    Raises:
        sqlite3.Error: If a query fails.
        ValueError: If a stored value is not a number.
    """
    rows: list[Packed] = []
    collectors = [int(r[0]) for r in conn.execute("SELECT collector_key FROM dict_collector")]
    for collector in collectors:
        found: dict[int, Partial] = {}
        scan(conn, collector, bounds, found)
        rows += [(collector, key, part.payload()) for key, part in found.items()]
    return rows


def _compact(conn: sqlite3.Connection, hour: int) -> None:
    """Merge an hour's four listed buckets into one hour unit, if all four are listed.

    Args:
        conn: Connection holding the write lock.
        hour: The hour start, microseconds.

    Returns:
        None

    Raises:
        sqlite3.Error: If a query fails.
        StorageError: If a stored payload is damaged.
    """
    span = (hour, hour + HOUR_US)
    listed = conn.execute("SELECT count(*) FROM rank_unit WHERE width_s = 900 AND start_us >= ? "
                          "AND start_us < ?", span).fetchone()[0]  # fmt: skip
    if listed < HOUR_US // BUCKET_US:
        return
    merged: dict[tuple[int, int], Partial] = {}
    for collector, key, payload in conn.execute(
        f"SELECT collector_key, series_key, payload FROM rank_iface WHERE {_COLLECTORS} "
        "AND width_s = 900 AND start_us >= ? AND start_us < ?",
        span,
    ):
        merged.setdefault((collector, key), Partial()).add_payload(payload)
    _replace(conn, _HOUR, hour, [(c, k, p.payload()) for (c, k), p in merged.items()])


class SqliteRankingProjection:
    """``RankingProjection`` over one ``SqliteTelemetryStore``."""

    def __init__(self, store: SqliteTelemetryStore) -> None:
        """Bind the projection.

        Args:
            store: The open store.

        Returns:
            None

        Raises:
            None
        """
        self._store = store

    def unprojected(
        self, window: TimeWindow, *, limit: int = MAX_PROJECTION_BUCKETS
    ) -> tuple[datetime, ...]:
        """List buckets inside ``window`` that are not projected, newest first; see the port.

        Args:
            window: At most ``PROJECTION_KEEP`` long.
            limit: Most buckets.

        Returns:
            tuple[datetime, ...]: Bucket starts (UTC).

        Raises:
            ValidationError: If the window or limit is out of bounds.
            StorageError: If the database is unavailable.
        """
        check_projection_window(window.duration.total_seconds(), limit)
        with self._store.reading() as conn:
            start = native_start(conn, to_epoch_us(window.start))
            end = to_epoch_us(window.end)
            done = {int(r[0]) for r in conn.execute(
                "SELECT start_us FROM rank_unit WHERE width_s = 900 AND start_us >= ? "
                "AND start_us < ?", (start, end),
            )}  # fmt: skip
        missing = [b for b in _starts((start, end), MAX_PROJECTION_BUCKETS) if b not in done]
        return tuple(from_epoch_us(b) for b in missing[:limit])

    def project(self, bucket: datetime) -> int:
        """Store one bucket's partials for every collector and interface; see the port.

        Args:
            bucket: A 15-minute bucket start (UTC).

        Returns:
            int: Interface partials stored.

        Raises:
            ValidationError: If ``bucket`` is not on the 15-minute grid.
            StorageError: If the database is unavailable; nothing changes.
        """
        check_bucket(bucket)
        start = to_epoch_us(bucket)
        hour = start - start % HOUR_US
        with self._store.writing() as conn:
            if native_start(conn, start) > start:
                return 0
            # The hour is rebuilt from its buckets below, never trusted across a re-projection.
            conn.execute("DELETE FROM rank_unit WHERE width_s = 3600 AND start_us = ?", (hour,))
            rows = _partials(conn, (start, start + BUCKET_US))
            _replace(conn, _QUARTER, start, rows)
            _compact(conn, hour)
        return len(rows)

    def forget(self) -> int:
        """Unlist every projected bucket and hour; see the port.

        Args:
            None

        Returns:
            int: Buckets unlisted.

        Raises:
            StorageError: If the database is unavailable; nothing changes.
        """
        with self._store.writing() as conn:
            count = conn.execute("DELETE FROM rank_unit WHERE width_s = 900").rowcount
            conn.execute("DELETE FROM rank_unit")
        return count
