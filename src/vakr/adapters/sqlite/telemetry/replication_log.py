"""The replication log and cursors in SQL (MPOS-004, MPOS-005, schema 3, ADR-024).

``repl_log`` gives every observation this node's spool ingested a durable,
never-reused ``seq``; ``repl_cursor`` keeps, per partner collector, the
newest partner seq stored here. The writer logs rows in the transaction
that inserts them; ``read_page`` follows the log back to each row, in its
staging table or day partition; retention purges log rows whose
observations expired, in bounded batches.

Dependencies:
    sqlite3: Statements on the caller's transaction.
    vakr.adapters.sqlite.telemetry.layout, partitions: Table names and state.

Safety:
    Table names come from ``layout``; values are bound parameters.

Example:
    >>> FAMILY_CODE["obs_iface"], FAMILY_OF_CODE[5]
    (1, 'obs_health')
"""

# SELECTs interpolate checked partition names only.
# ruff: noqa: S608

import sqlite3
from collections.abc import Iterable
from dataclasses import dataclass
from typing import cast
from uuid import UUID

from vakr.adapters.sqlite.telemetry.layout import all_columns, day_table, hot_table
from vakr.adapters.sqlite.telemetry.partitions import BOUNDARY, state, tables

FAMILY_CODE = {"obs_iface": 1, "obs_icmp": 2, "obs_snmp": 3, "obs_poll": 4, "obs_health": 5}
"""Pinned codes of the native families in ``repl_log.family``."""
FAMILY_OF_CODE = {code: family for family, code in FAMILY_CODE.items()}
PURGE_ROWS = 1_000_000
"""Log rows one retention pass removes at most (E.13); a day at MVP scale is about 2.7 million."""

type LogEntry = tuple[int, int, int, int, bytes]
"""``(collector_key, family code, series_key, ts_us, observation UUID bytes)``."""


@dataclass(frozen=True, slots=True)
class LoggedRow:
    """One log entry followed back to its observation row.

    Attributes:
        seq: The entry's seq.
        family: The native family.
        row: Every column of the row, keys first, or ``None`` if it expired.
    """

    seq: int
    family: str
    row: tuple[object, ...] | None


def log_rows(conn: sqlite3.Connection, entries: Iterable[LogEntry]) -> None:
    """Append freshly inserted rows to the log, in the order given.

    Args:
        conn: Connection holding the write lock.
        entries: One entry per row inserted for the first time.

    Returns:
        None

    Raises:
        sqlite3.Error: If the insert fails; the caller rolls back.
    """
    conn.executemany(
        "INSERT INTO repl_log (collector_key, family, series_key, ts_us, observation_uuid) "
        "VALUES (?, ?, ?, ?, ?)",
        entries,
    )


def store_part(
    conn: sqlite3.Connection, sql: str, part: list[tuple[object, ...]], code: int,
    log: list[LogEntry] | None,
) -> int:  # fmt: skip
    """Insert one table's rows, noting which were new when they are to be logged.

    Args:
        conn: Connection holding the write lock.
        sql: The table's idempotent insert.
        part: Its keyed rows.
        code: Their family's ``FAMILY_CODE``.
        log: Where new rows' log entries go, or ``None``.

    Returns:
        int: Rows inserted for the first time.

    Raises:
        sqlite3.Error: If a statement fails; the caller rolls back.
    """
    if log is None:
        return conn.executemany(sql, part).rowcount
    # One statement per row tells which rows were new; a replayed row must not be logged
    # twice, or a partner would be served it twice.
    stored = 0
    for row in part:
        if conn.execute(sql, row).rowcount:
            stored += 1
            series, collector, ts_us, uuid = cast(tuple[int, int, int, bytes], row[:4])
            log.append((collector, code, series, ts_us, uuid))
    return stored


def bounds(conn: sqlite3.Connection, collector_key: int) -> tuple[int | None, int | None]:
    """Return the oldest and newest seq the log holds for a collector.

    Args:
        conn: Connection inside the caller's transaction.
        collector_key: The collector's dictionary key.

    Returns:
        tuple: ``(oldest, newest)``, both ``None`` for an empty log.

    Raises:
        sqlite3.Error: If the log cannot be read.
    """
    sql = "SELECT min(seq), max(seq) FROM repl_log WHERE collector_key = ?"
    oldest, newest = conn.execute(sql, (collector_key,)).fetchone()
    return oldest, newest


def read_page(
    conn: sqlite3.Connection, collector_key: int, after: int, limit: int
) -> tuple[list[LoggedRow], bool]:
    """Read up to ``limit`` log entries after ``after`` and follow each to its row.

    Args:
        conn: Connection inside the caller's read transaction.
        collector_key: The collector's dictionary key.
        after: Last seq the caller holds.
        limit: Most entries.

    Returns:
        tuple: The entries in seq order, and whether later entries exist.

    Raises:
        sqlite3.Error: If a table cannot be read.
    """
    sql = ("SELECT seq, family, series_key, ts_us, observation_uuid FROM repl_log "
           "WHERE collector_key = ? AND seq > ? ORDER BY seq LIMIT ?")  # fmt: skip
    found = conn.execute(sql, (collector_key, after, limit + 1)).fetchall()
    boundary = state(conn, BOUNDARY) or 0
    present: dict[str, set[str]] = {}
    rows = [_follow(conn, collector_key, e, boundary, present) for e in found[:limit]]
    return rows, len(found) > limit


def _follow(
    conn: sqlite3.Connection,
    collector_key: int,
    entry: tuple[int, int, int, int, bytes],
    boundary: int,
    present: dict[str, set[str]],
) -> LoggedRow:
    """Read the row one log entry points at.

    Args:
        conn: Connection inside the caller's read transaction.
        collector_key: The collector's key.
        entry: ``(seq, family code, series_key, ts_us, uuid)``.
        boundary: The staging boundary of this snapshot.
        present: Day partitions known to exist, per family (filled here).

    Returns:
        LoggedRow: The row, or ``None`` when retention removed it.

    Raises:
        sqlite3.Error: If the table cannot be read.
    """
    seq, code, series, ts_us, uuid = entry
    family = FAMILY_OF_CODE[code]
    # The mover changes the boundary in the transaction that moves rows, so one snapshot
    # tells exactly where a row is: staging at or after the boundary, its day before it.
    table = hot_table(family) if ts_us >= boundary else day_table(family, ts_us)
    if ts_us < boundary:
        known = present.setdefault(family, set(tables(conn, family, -(1 << 62), boundary)))
        if table not in known:
            return LoggedRow(seq, family, None)
    sql = (f"SELECT {', '.join(all_columns(family))} FROM {table} WHERE series_key = ? "
           "AND collector_key = ? AND ts_us = ? AND observation_uuid = ?")  # fmt: skip
    row = conn.execute(sql, (series, collector_key, ts_us, uuid)).fetchone()
    return LoggedRow(seq, family, None if row is None else tuple(row))


def purge_log(conn: sqlite3.Connection, horizon_us: int | None) -> int:
    """Remove log rows whose observations retention expired, oldest first, bounded.

    Args:
        conn: Connection holding the write lock.
        horizon_us: The native horizon; nothing is removed without one.

    Returns:
        int: Rows removed.

    Raises:
        sqlite3.Error: If the delete fails; the caller rolls back.
    """
    if horizon_us is None:
        return 0
    sql = ("DELETE FROM repl_log WHERE seq IN (SELECT seq FROM repl_log WHERE ts_us < ? "
           "ORDER BY seq LIMIT ?)")  # fmt: skip
    return conn.execute(sql, (horizon_us, PURGE_ROWS)).rowcount


def cursor_of(conn: sqlite3.Connection, source: UUID) -> int | None:
    """Return the newest partner seq stored for ``source``.

    Args:
        conn: Any connection.
        source: The partner collector.

    Returns:
        int | None: The seq, or ``None`` before the first page.

    Raises:
        sqlite3.Error: If the table cannot be read.
    """
    sql = "SELECT last_seq FROM repl_cursor WHERE source_collector = ?"
    row = conn.execute(sql, (source.bytes,)).fetchone()
    return None if row is None else int(row[0])


def set_cursor(conn: sqlite3.Connection, source: UUID, seq: int, now_us: int) -> None:
    """Store ``source``'s cursor in the caller's transaction.

    Args:
        conn: Connection holding the write lock.
        source: The partner collector.
        seq: The newest partner seq now stored.
        now_us: When, for diagnostics.

    Returns:
        None

    Raises:
        sqlite3.Error: If the upsert fails; the caller rolls back.
    """
    conn.execute(
        "INSERT INTO repl_cursor (source_collector, last_seq, updated_us) VALUES (?, ?, ?) "
        "ON CONFLICT (source_collector) DO UPDATE SET last_seq = excluded.last_seq, "
        "updated_us = excluded.updated_us",
        (source.bytes, seq, now_us),
    )
