"""Raw observation pages: ``read_raw`` (DB-004, API-005, E.19).

A page is one ``UNION ALL`` over the day partitions in the window plus
staging, filtered to one subject (and optionally one collector), ordered
by ``(ts_us, observation_uuid)`` and limited to ``PageRequest.limit + 1``
rows, so the presence of a next page is known without counting. Cursors
are keyset cursors ``(timestamp µs, observation UUID text)``, the same key
the reference adapter uses, so inserting rows while someone pages never
repeats or skips one. Rows before the native horizon are expired and never
returned.

Dependencies:
    sqlite3: Statements on the caller's read transaction.
    vakr.adapters.sqlite.telemetry.*: Read helpers, keys, and layout.
    vakr.domain: Paging and errors.

Safety:
    A forged cursor is rejected with ``ValidationError`` before any query runs.

Example:
    >>> raw_cursor(None) is None
    True
"""

import sqlite3
from uuid import UUID

from vakr.adapters.sqlite.telemetry.keys import find_key
from vakr.adapters.sqlite.telemetry.layout import FAMILY_OF
from vakr.adapters.sqlite.telemetry.reads import (
    decoder,
    full_columns,
    native_start,
    sources,
    subject_key,
    union,
)
from vakr.adapters.sqlite.telemetry.store import SqliteTelemetryStore
from vakr.domain.enums import ObservationType
from vakr.domain.errors import ValidationError
from vakr.domain.observations import Observation
from vakr.domain.paging import Page, PageRequest, decode_cursor, encode_cursor
from vakr.domain.series import TimeWindow
from vakr.domain.timestamps import to_epoch_us


def raw_cursor(cursor: str | None) -> tuple[int, bytes] | None:
    """Decode a raw-read cursor into ``(ts_us, observation UUID bytes)``.

    Args:
        cursor: ``Page.next_cursor`` from the previous page, or ``None``.

    Returns:
        tuple[int, bytes] | None: The position after which to read.

    Raises:
        ValidationError: If the cursor is malformed.
    """
    if cursor is None:
        return None
    ts, ident = decode_cursor(cursor, 2)
    invalid = ValidationError("cursor is not valid for this list", field="cursor")
    if not isinstance(ts, int) or not isinstance(ident, str):
        raise invalid
    try:
        return ts, UUID(ident).bytes
    except ValueError:
        raise invalid from None


def raw_page(
    store: SqliteTelemetryStore, observation_type: ObservationType, subject: UUID,
    window: TimeWindow, page: PageRequest, collector: UUID | None,
) -> Page[Observation]:  # fmt: skip
    """Return one page of stored observations, oldest first; ``total`` is ``None``.

    Args:
        store: The open store.
        observation_type: Which observations.
        subject: Interface, device, or (health) collector.
        window: The window.
        page: Limit and cursor.
        collector: Only this collector, or every collector.

    Returns:
        Page[Observation]: The page.

    Raises:
        ValidationError: If the cursor is invalid.
    """
    after = raw_cursor(page.cursor)
    family = FAMILY_OF[observation_type]
    with store.reading() as conn:
        key = subject_key(conn, store.keys, family, subject)
        col = None if collector is None else find_key(conn, store.keys, "collector", collector)
        if key is None or (collector is not None and col is None):
            return Page((), None, None)
        rows = _rows(conn, family, key, col, window, after, page.limit + 1)
        decode = decoder(conn, store.keys, family)
        items = [decode(r) for r in rows]
    more = len(items) > page.limit
    items = items[: page.limit]
    if not more or not items:
        return Page(tuple(items), None, None)
    last = items[-1]
    cursor = encode_cursor((to_epoch_us(last.timestamp), str(last.observation_id)))
    return Page(tuple(items), cursor, None)


def _rows(
    conn: sqlite3.Connection, family: str, key: int, collector: int | None, window: TimeWindow,
    after: tuple[int, bytes] | None, limit: int,
) -> list[tuple[object, ...]]:  # fmt: skip
    """Fetch the full rows of one page.

    Args:
        conn: Connection inside the read transaction.
        family: The native family.
        key: The subject's series key.
        collector: The collector's key, or ``None`` for every collector.
        window: The window.
        after: Resume position, or ``None``.
        limit: Rows wanted.

    Returns:
        list[tuple[object, ...]]: Full rows ordered by ``(ts_us, observation_uuid)``.

    Raises:
        sqlite3.Error: If the query fails.
    """
    start = native_start(conn, to_epoch_us(window.start))
    end = to_epoch_us(window.end)
    where = "series_key = :s AND ts_us >= :a AND ts_us < :b"
    if collector is not None:
        where += " AND collector_key = :c"
    if after is not None:
        where += " AND (ts_us, observation_uuid) > (:t, :u)"
    params = {"s": key, "c": collector, "a": start, "b": end, "n": limit,
              "t": after[0] if after else 0, "u": after[1] if after else b""}  # fmt: skip
    sql = union(full_columns(family), sources(conn, family, start, end), where)
    return conn.execute(f"{sql} ORDER BY ts_us, observation_uuid LIMIT :n", params).fetchall()
