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

from vakr.adapters.sqlite.errors import translated
from vakr.adapters.sqlite.telemetry.keys import find_key
from vakr.adapters.sqlite.telemetry.reads import decoder
from vakr.adapters.sqlite.telemetry.replication_log import bounds, read_page
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
        missing = tuple(e.seq for e in logged if e.row is None)
        last = logged[-1].seq if logged else after
        return FeedPage(entries, last, more, oldest, newest, missing)

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
