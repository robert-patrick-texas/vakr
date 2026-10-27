"""``ObservationFeed`` for the SQLite telemetry store: serve the replication log (MPOS-004).

A page is one read transaction: the log entries after ``after`` for one
collector, each followed to its row and decoded back into the observation
exactly as written. An entry whose row retention already removed is counted
in ``missing`` rather than served, so the partner can report the gap
(MPOS-005). The read never takes the write lock.

Dependencies:
    vakr.adapters.sqlite.telemetry.*: The log, keys, decoders, and the store.
    vakr.ports.telemetry.replication: The port and its page types.

Safety:
    Read-only; bounds are checked before any query runs.

Example:
    >>> MAX_FEED_LIMIT
    10000
"""

# The status query interpolates fixed layout names only; values are bound parameters.
# ruff: noqa: S608

import sqlite3

from vakr.adapters.sqlite.errors import translated
from vakr.adapters.sqlite.telemetry.keys import find_key
from vakr.adapters.sqlite.telemetry.layout import all_columns, hot_table
from vakr.adapters.sqlite.telemetry.reads import decoder
from vakr.adapters.sqlite.telemetry.replication_log import LoggedRow, bounds, read_page
from vakr.adapters.sqlite.telemetry.store import SqliteTelemetryStore
from vakr.domain.errors import ValidationError
from vakr.domain.ids import CollectorId
from vakr.ports.telemetry.replication import FeedEntry, FeedPage

MAX_FEED_LIMIT = 10_000
"""Most observations one page holds (E.13)."""


class SqliteObservationFeed:
    """Reads one store's replication log."""

    def __init__(self, store: SqliteTelemetryStore) -> None:
        """Bind the feed.

        Args:
            store: The open store.

        Returns:
            None

        Raises:
            None
        """
        self._store = store

    def feed(self, collector: CollectorId, after: int, limit: int) -> FeedPage:
        """Return up to ``limit`` observations of ``collector`` logged after ``after``.

        Args:
            collector: A collector whose observations this store logged.
            after: Last seq the caller holds (0 for none).
            limit: 1 to ``MAX_FEED_LIMIT``.

        Returns:
            FeedPage: The page; empty for a collector never logged here.

        Raises:
            ValidationError: If ``after`` or ``limit`` is out of range.
            StorageError: If the store cannot be read.
        """
        if after < 0 or not 1 <= limit <= MAX_FEED_LIMIT:
            raise ValidationError(f"after must be 0 or more and limit 1 to {MAX_FEED_LIMIT}")
        with self._store.reading() as conn, translated():
            key = find_key(conn, self._store.keys, "collector", collector)
            if key is None:
                return FeedPage((), after, False, None, None)
            oldest, newest = bounds(conn, key)
            logged, more = read_page(conn, key, after, limit)
            decoders = {f: decoder(conn, self._store.keys, f) for f in {e.family for e in logged}}
            entries = tuple(FeedEntry(e.seq, decoders[e.family](e.row))
                            for e in logged if e.row is not None)  # fmt: skip
            attached = _attached(conn, self._store, key, logged)
        missing = tuple(e.seq for e in logged if e.row is None)
        last = logged[-1].seq if logged else after
        return FeedPage(entries, last, more, oldest, newest, missing, attached)

    def bounds(self, collector: CollectorId) -> tuple[int | None, int | None]:
        """Return the oldest and newest seq logged for ``collector``.

        Args:
            collector: The collector.

        Returns:
            tuple: ``(oldest, newest)``, both ``None`` when nothing is logged.

        Raises:
            StorageError: If the store cannot be read.
        """
        with self._store.reading() as conn, translated():
            key = find_key(conn, self._store.keys, "collector", collector)
            return (None, None) if key is None else bounds(conn, key)


def _attached(
    conn: sqlite3.Connection, store: SqliteTelemetryStore, collector_key: int,
    logged: list[LoggedRow],
) -> tuple[FeedEntry, ...]:  # fmt: skip
    """Read the interface status records of each logged SNMP device observation's poll.

    Stage 1 emits a poll's status records before its SNMP device observation, so they
    are stored no later than it and are found whenever its log entry is (E.35).

    Args:
        conn: Connection inside the read transaction.
        store: The store (for its key cache).
        collector_key: The collector's key.
        logged: The page's log entries followed to their rows.

    Returns:
        tuple[FeedEntry, ...]: Status records, each with its SNMP entry's seq.

    Raises:
        sqlite3.Error: If a query fails.
    """
    sql = (f"SELECT {', '.join(all_columns('obs_ifstat'))} FROM {hot_table('obs_ifstat')} "
           "WHERE device_key = ? AND poll_uuid = ? AND collector_key = ? ORDER BY ts_us, "
           "observation_uuid")  # fmt: skip
    decode = decoder(conn, store.keys, "obs_ifstat")
    found: list[FeedEntry] = []
    for e in logged:
        if e.family != "obs_snmp" or e.row is None:
            continue
        device_key, poll_uuid = e.row[0], e.row[4]
        rows = conn.execute(sql, (device_key, poll_uuid, collector_key)).fetchall()
        found.extend(FeedEntry(e.seq, decode(r)) for r in rows)
    return tuple(found)
