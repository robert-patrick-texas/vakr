"""Retention for the kept families: rows behind the native horizon, deleted in bounded batches.

Kept families (``layout.KEPT_FAMILIES``, interface status records from
schema 4) live in one table for good, so retention cannot drop a day
partition for them. Each retention pass deletes their rows older than the
native horizon instead, at most ``KEPT_PURGE_ROWS`` per family, oldest
first; reads already ignore those rows, exactly as for expired partitions.

Dependencies:
    sqlite3: Statements on the caller's write transaction.
    vakr.adapters.sqlite.telemetry.layout: Family and table names.

Safety:
    Table names come from ``layout``; values are bound parameters.

Example:
    >>> KEPT_PURGE_ROWS
    100000
"""

# The delete interpolates fixed layout names only; values are bound parameters.
# ruff: noqa: S608

import sqlite3

from vakr.adapters.sqlite.telemetry.layout import KEPT_FAMILIES, KEY_COLUMNS, hot_table

KEPT_PURGE_ROWS = 100_000
"""Status rows one retention pass deletes at most (E.35); a day holds a few thousand."""


def purge_kept(conn: sqlite3.Connection, horizon_us: int | None) -> int:
    """Delete rows of the kept families behind the native horizon, oldest first, bounded.

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
    removed = 0
    for family in KEPT_FAMILIES:
        table, keys = hot_table(family), ", ".join(KEY_COLUMNS)
        removed += conn.execute(
            f"DELETE FROM {table} WHERE ({keys}) IN (SELECT {keys} FROM {table} "
            "WHERE ts_us < ? ORDER BY ts_us LIMIT ?)", (horizon_us, KEPT_PURGE_ROWS),
        ).rowcount  # fmt: skip
    return removed
