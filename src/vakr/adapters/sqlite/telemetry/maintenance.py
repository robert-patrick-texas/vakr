"""The staging mover and WAL checkpoint: ``maintain(now)`` (ADR-021, E.21).

Staging turns each 10-second commit into appends at the right edge of a
small, time-ordered table instead of one page write per series. Every 15
minutes the service loop calls ``maintain(now)``, which in **one**
transaction copies every staged row older than ``now - 2 min`` into its day
partition in key order, deletes those rows from staging, and advances the
boundary. A row is therefore in staging exactly when its timestamp is at or
after the boundary, in every snapshot a reader can see (STATE-001): readers
never see a row twice or miss one, and a crash at any point leaves either
the old state or the new one.

After the move, ``PRAGMA wal_checkpoint(TRUNCATE)`` resets the WAL. Under
continuous reads the passive autocheckpoints cannot, and the benchmark's
WAL grew to 1.19 GB without this step.

Dependencies:
    vakr.adapters.sqlite.telemetry.*: Layout, catalog, the store, and tuning.
    vakr.domain: Timestamps and errors.

Safety:
    The boundary only moves forward, so a clock that steps back moves
    nothing. Table names come from ``layout``.

Example:
    >>> MaintenanceReport.__dataclass_fields__.keys() >= {"moved", "boundary"}
    True
"""

# Mover statements interpolate checked partition and staging names only.
# ruff: noqa: S608

import sqlite3
from dataclasses import dataclass
from datetime import datetime

from vakr.adapters.sqlite.encoding import decode_time, encode_time
from vakr.adapters.sqlite.telemetry.layout import (
    DAY_US,
    KEY_COLUMNS,
    MOVED_FAMILIES,
    all_columns,
    day_start,
    hot_table,
)
from vakr.adapters.sqlite.telemetry.partitions import BOUNDARY, ensure_day, set_state, state
from vakr.adapters.sqlite.telemetry.store import SqliteTelemetryStore
from vakr.adapters.sqlite.telemetry.tuning import MOVE_LAG
from vakr.domain.timestamps import require_utc


@dataclass(frozen=True, slots=True)
class MaintenanceReport:
    """What one ``maintain`` call did.

    Attributes:
        boundary: The staging boundary afterwards (UTC).
        moved: Rows moved from staging into day partitions.
        checkpoint_busy: The truncating checkpoint could not finish because
            a reader held an old snapshot; the next call retries.
        wal_frames: WAL frames the checkpoint found.
    """

    boundary: datetime
    moved: int
    checkpoint_busy: bool
    wal_frames: int


def maintain(store: SqliteTelemetryStore, now: datetime) -> MaintenanceReport:
    """Move staged rows older than ``now - MOVE_LAG``, then truncate the WAL.

    Args:
        store: The open store.
        now: Reference time (UTC).

    Returns:
        MaintenanceReport: Rows moved, the boundary, and the checkpoint result.

    Raises:
        ValidationError: If ``now`` is not UTC.
        StorageError: If the database is unavailable; nothing moved.
    """
    require_utc(now, "now")
    target = encode_time(now - MOVE_LAG)
    moved = 0
    with store.writing() as conn:
        boundary = state(conn, BOUNDARY) or 0
        if target > boundary:
            for family in MOVED_FAMILIES:
                moved += _move(conn, family, target)
            set_state(conn, BOUNDARY, target)
            boundary = target
    busy, frames, _done = store.checkpoint()
    return MaintenanceReport(decode_time(boundary), moved, bool(busy), frames)


def _move(conn: sqlite3.Connection, family: str, target: int) -> int:
    """Move one family's staged rows older than ``target`` into day partitions.

    Args:
        conn: Connection holding the write lock.
        family: A native family.
        target: The new boundary, microseconds.

    Returns:
        int: Rows moved.

    Raises:
        sqlite3.Error: If a statement fails; the caller rolls back.
    """
    hot = hot_table(family)
    columns = ", ".join(all_columns(family))
    order = ", ".join(KEY_COLUMNS)
    moved = 0
    first = conn.execute(f"SELECT min(ts_us) FROM {hot} WHERE ts_us < ?", (target,)).fetchone()[0]
    while first is not None:
        day = day_start(first)
        end = min(day + DAY_US, target)
        table = ensure_day(conn, family, day)
        moved += conn.execute(
            f"INSERT OR IGNORE INTO {table} ({columns}) SELECT {columns} FROM {hot} "
            f"WHERE ts_us >= ? AND ts_us < ? ORDER BY {order}",
            (first, end),
        ).rowcount
        first = conn.execute(
            f"SELECT min(ts_us) FROM {hot} WHERE ts_us >= ? AND ts_us < ?", (end, target)
        ).fetchone()[0]
    conn.execute(f"DELETE FROM {hot} WHERE ts_us < ?", (target,))
    return moved
