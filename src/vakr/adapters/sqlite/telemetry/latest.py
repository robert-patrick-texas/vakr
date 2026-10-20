"""Latest-row lookups behind current state (STATE-001..004, DB-005, ADR-021).

Current state is read from the observations themselves, so it is always
rebuildable (STATE-002) and needs no projection table: a measured upserted
projection halved ingest throughput (ADR-021). For one series the newest
row is looked for in staging first, then in the day partitions newest
first, one primary-key seek each, stopping at the first hit. Staged rows
are always newer than partitioned ones, so the first hit is the newest
row, and a stale interface whose last sample is days old is still found
with its true age (STATE-003).

"Evidence" excludes rows flagged ``NOT_ATTEMPTED``, which record a poll
the collector never made and say nothing about the target (invariant 2).
Listings find the subjects that have evidence by driving from the
dictionary: one ``EXISTS`` seek per subject and partition.

Listings are keyset pages ordered by subject UUID text, with cursors in
the reference adapter's ``(uuid text,)`` form.

Dependencies:
    sqlite3: Statements on the caller's read transaction.
    vakr.adapters.sqlite.telemetry.*: Layout, catalog, and read helpers.
    vakr.domain: Paging and errors.

Safety:
    Lookups are bounded by the retained partitions; names come from the catalog.

Example:
    >>> newest.__name__, with_evidence.__name__
    ('newest', 'with_evidence')
"""

# Lookup SQL interpolates checked partition names only; values are bound parameters.
# ruff: noqa: S608

import itertools
import sqlite3
from datetime import timedelta
from uuid import UUID

from vakr.adapters.sqlite.telemetry.layout import hot_table
from vakr.adapters.sqlite.telemetry.partitions import tables
from vakr.adapters.sqlite.telemetry.reads import (
    NOT_ATTEMPTED_BIT,
    full_columns,
    native_start,
    sources,
)
from vakr.adapters.sqlite.telemetry.tuning import POLL_LOOKAHEAD, POLL_LOOKBACK
from vakr.domain.errors import ValidationError
from vakr.domain.paging import decode_cursor, encode_cursor

_FAR_FUTURE = 1 << 62
_CHUNK = 500  # candidates per statement, within SQLite's compound-select limit
_ONE_US = timedelta(microseconds=1)
_EVIDENCE = f" AND (quality_flags & {NOT_ATTEMPTED_BIT}) = 0"


def _newest_first(conn: sqlite3.Connection, family: str) -> list[str]:
    """List staging, then the retained day partitions newest first.

    Args:
        conn: Connection inside the read transaction.
        family: A native family.

    Returns:
        list[str]: Table names in lookup order.

    Raises:
        sqlite3.Error: If the catalog cannot be read.
    """
    start = native_start(conn, 0)
    return [hot_table(family), *reversed(tables(conn, family, start, _FAR_FUTURE))]


def newest(
    conn: sqlite3.Connection, family: str, series: int, collector: int, *, evidence: bool
) -> tuple[object, ...] | None:
    """Return the newest retained full row of one series from one collector.

    Args:
        conn: Connection inside the read transaction.
        family: A native family.
        series: The subject's series key.
        collector: The collector key.
        evidence: Skip rows flagged ``NOT_ATTEMPTED``.

    Returns:
        tuple[object, ...] | None: The row, ties broken by the larger
        observation UUID (DB-005), or ``None``.

    Raises:
        sqlite3.Error: If a query fails.
    """
    since = native_start(conn, 0)
    flag = _EVIDENCE if evidence else ""
    for table in _newest_first(conn, family):
        row = conn.execute(
            f"SELECT {full_columns(family)} FROM {table} WHERE series_key = ? "
            f"AND collector_key = ? AND ts_us >= ?{flag} "
            "ORDER BY ts_us DESC, observation_uuid DESC LIMIT 1",
            (series, collector, since),
        ).fetchone()
        if row is not None:
            return tuple(row)
    return None


def poll_interval(
    conn: sqlite3.Connection, device: int, collector: int, poll_uuid: bytes, ts_us: int
) -> int | None:
    """Return the effective interval of the poll an interface sample came from.

    The poll is searched from ``POLL_LOOKBACK`` before the sample to
    ``POLL_LOOKAHEAD`` after it (E.21), which bounds the lookup.

    Args:
        conn: Connection inside the read transaction.
        device: The device key.
        collector: The collector key.
        poll_uuid: The sample's poll UUID as bytes.
        ts_us: The sample's timestamp.

    Returns:
        int | None: The interval in seconds, or ``None`` if the poll is not stored.

    Raises:
        sqlite3.Error: If a query fails.
    """
    lo = native_start(conn, ts_us - POLL_LOOKBACK // _ONE_US)
    hi = ts_us + POLL_LOOKAHEAD // _ONE_US + 1
    for table in sources(conn, "obs_poll", lo, hi):
        row = conn.execute(
            f"SELECT effective_interval_s FROM {table} WHERE series_key = ? AND collector_key = ? "
            "AND ts_us >= ? AND ts_us < ? AND poll_uuid = ? LIMIT 1",
            (device, collector, lo, hi, poll_uuid),
        ).fetchone()
        if row is not None:
            return int(row[0])
    return None


def with_evidence(
    conn: sqlite3.Connection, family: str, collector: int, candidates: list[int]
) -> set[int]:
    """Return the series keys among ``candidates`` with any retained evidence.

    Staging is scanned once; each day partition, newest first, is then asked
    with one ``EXISTS`` seek per candidate not yet found.

    Args:
        conn: Connection inside the read transaction.
        family: A native family.
        collector: The collector key.
        candidates: Series keys from the dictionary.

    Returns:
        set[int]: The candidates with at least one evidence row.

    Raises:
        sqlite3.Error: If a query fails.
    """
    since = native_start(conn, 0)
    remaining = set(candidates)
    staged = conn.execute(
        f"SELECT DISTINCT series_key FROM {hot_table(family)} "
        f"WHERE collector_key = ? AND ts_us >= ?{_EVIDENCE}",
        (collector, since),
    )
    found = {int(r[0]) for r in staged} & remaining
    remaining -= found
    for table in reversed(tables(conn, family, since, _FAR_FUTURE)):
        for chunk in itertools.batched(sorted(remaining), _CHUNK, strict=False):
            sql = (
                f"WITH c(k) AS (VALUES {', '.join(['(?)'] * len(chunk))}) SELECT k FROM c "
                f"WHERE EXISTS (SELECT 1 FROM {table} WHERE series_key = c.k "
                f"AND collector_key = ? AND ts_us >= ?{_EVIDENCE})"
            )
            found.update(int(r[0]) for r in conn.execute(sql, (*chunk, collector, since)))
        remaining -= found
        if not remaining:
            break
    return found


def listing_after(cursor: str | None) -> str | None:
    """Decode a listing cursor into the last subject UUID text shown.

    Args:
        cursor: ``Page.next_cursor``, or ``None``.

    Returns:
        str | None: The UUID text after which to continue.

    Raises:
        ValidationError: If the cursor is malformed.
    """
    if cursor is None:
        return None
    (last,) = decode_cursor(cursor, 1)
    if not isinstance(last, str):
        raise ValidationError("cursor is not valid for this list", field="cursor")
    return last


def keyset_page(ids: list[UUID], after: str | None, limit: int) -> tuple[list[UUID], str | None]:
    """Cut one keyset page from IDs sorted by their text.

    Args:
        ids: Every subject with state, sorted by ``str``.
        after: UUID text of the last item already shown, or ``None``.
        limit: Page size.

    Returns:
        tuple[list[UUID], str | None]: The page's IDs and the next cursor.

    Raises:
        None
    """
    rest = [i for i in ids if after is None or str(i) > after]
    chosen = rest[:limit]
    more = len(rest) > limit
    return chosen, encode_cursor((str(chosen[-1]),)) if more else None
