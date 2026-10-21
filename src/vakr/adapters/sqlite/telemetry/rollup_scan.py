"""Fleet-wide native scans for the rollup job and its health check (RET-004, RET-009, ADR-021).

Two kinds of read live here, both driven from the subject dictionaries so
SQLite seeks once per series instead of scanning a day partition:

- **Edges.** ``first_native_us`` and ``last_native_us`` find the earliest
  or latest interface or ICMP row in a window. Staging is a time range; a
  day partition is one ``ORDER BY ts_us LIMIT 1`` seek per series, newest
  or oldest partition first, stopping at the first that has a row.
- **Subject pages.** ``page_rows`` reads every row of one family in a
  window for the subjects whose UUIDs lie in ``(after, last]``: one arm per
  day partition (``dictionary CROSS JOIN dict_collector CROSS JOIN day``)
  plus the staging arm, joined with ``UNION ALL`` inside one read
  transaction, so the staging invariant keeps every row exactly once.

Every read starts at the native horizon: expired rows are never seen.

Dependencies:
    sqlite3: Statements on the caller's read transaction.
    vakr.adapters.sqlite.telemetry.*: Layout, catalog, keys, and read helpers.

Safety:
    Table and column names come from ``layout``, ``keys.DICTIONARIES``, and
    the catalog (checked); every value is a bound parameter.

Example:
    >>> ROLLED_FAMILIES
    ('obs_iface', 'obs_icmp')
"""

# Scan SQL interpolates checked partition names and fixed dictionary columns only.
# ruff: noqa: S608

import sqlite3
from collections.abc import Sequence

from vakr.adapters.sqlite.telemetry.keys import DICTIONARIES
from vakr.adapters.sqlite.telemetry.layout import hot_table
from vakr.adapters.sqlite.telemetry.partitions import tables
from vakr.adapters.sqlite.telemetry.reads import SUBJECT_DICTIONARY, native_start

ROLLED_FAMILIES = ("obs_iface", "obs_icmp")
"""Native families whose metrics are rolled up."""


def _edge(conn: sqlite3.Connection, family: str, table: str, span: tuple[int, int],
          newest: bool) -> int | None:  # fmt: skip
    """Return the earliest or latest timestamp in one day partition, one seek per series.

    Args:
        conn: Connection inside a read transaction.
        family: A native family.
        table: One of its day partitions.
        span: ``(lo, hi)`` microseconds, half-open.
        newest: Latest instead of earliest.

    Returns:
        int | None: The timestamp, or ``None`` when the partition has no row in ``span``.

    Raises:
        sqlite3.Error: If a query fails.
    """
    dictionary, key, _uuid = DICTIONARIES[SUBJECT_DICTIONARY[family]]
    order, pick = ("DESC", "max") if newest else ("ASC", "min")
    row = conn.execute(
        f"SELECT {pick}((SELECT t.ts_us FROM {table} t WHERE t.series_key = d.{key} "
        "AND t.collector_key = c.collector_key AND t.ts_us >= ? AND t.ts_us < ? "
        f"ORDER BY t.ts_us {order} LIMIT 1)) FROM {dictionary} d CROSS JOIN dict_collector c",
        span,
    ).fetchone()
    return None if row[0] is None else int(row[0])


def _family_edge(conn: sqlite3.Connection, family: str, span: tuple[int, int],
                 newest: bool) -> int | None:  # fmt: skip
    """Return one family's earliest or latest timestamp in ``span``.

    Args:
        conn: Connection inside a read transaction.
        family: A native family.
        span: ``(lo, hi)`` microseconds.
        newest: Latest instead of earliest.

    Returns:
        int | None: The timestamp, or ``None``.

    Raises:
        sqlite3.Error: If a query fails.
    """
    pick = "max" if newest else "min"
    staged = conn.execute(
        f"SELECT {pick}(ts_us) FROM {hot_table(family)} WHERE ts_us >= ? AND ts_us < ?", span
    ).fetchone()[0]
    found = [] if staged is None else [int(staged)]
    days = tables(conn, family, *span)
    for table in reversed(days) if newest else days:
        hit = _edge(conn, family, table, span, newest)
        if hit is not None:
            found.append(hit)
            break
    if not found:
        return None
    return max(found) if newest else min(found)


def native_edge(conn: sqlite3.Connection, lo: int, hi: int, *, newest: bool) -> int | None:
    """Return the earliest (or latest) interface or ICMP observation time in ``[lo, hi)``.

    Args:
        conn: Connection inside a read transaction.
        lo: Window start, microseconds; clamped to the native horizon.
        hi: Window end, exclusive.
        newest: Latest instead of earliest.

    Returns:
        int | None: Microseconds, or ``None`` when the window holds no such row.

    Raises:
        sqlite3.Error: If a query fails.
    """
    span = (native_start(conn, lo), hi)
    if span[0] >= span[1]:
        return None
    found = [t for f in ROLLED_FAMILIES if (t := _family_edge(conn, f, span, newest)) is not None]
    if not found:
        return None
    return max(found) if newest else min(found)


def page_rows(
    conn: sqlite3.Connection, family: str, columns: Sequence[str], span: tuple[int, int],
    subjects: tuple[bytes, bytes], extra: str = "",
) -> list[tuple[object, ...]]:  # fmt: skip
    """Read ``columns`` of every row of ``family`` in ``span`` for a range of subjects.

    Args:
        conn: Connection inside a read transaction.
        family: A native family.
        columns: Column names of the family's tables.
        span: ``(lo, hi)`` microseconds; ``lo`` is clamped to the native horizon.
        subjects: ``(after, last)``: subject UUID blobs, exclusive and inclusive.
        extra: A further condition on ``t``, with no parameters.

    Returns:
        list[tuple[object, ...]]: The rows, unordered.

    Raises:
        StorageError: If the catalog holds a foreign name.
        sqlite3.Error: If a query fails.
    """
    lo, hi = native_start(conn, span[0]), span[1]
    if lo >= hi:
        return []
    dictionary, key, uuid = DICTIONARIES[SUBJECT_DICTIONARY[family]]
    cols = ", ".join(f"t.{c}" for c in columns)
    window = f"t.ts_us >= :lo AND t.ts_us < :hi{extra}"
    chosen = f"d.{uuid} > :after AND d.{uuid} <= :last"
    arms = [
        f"SELECT {cols} FROM {dictionary} d CROSS JOIN dict_collector c CROSS JOIN {table} t "
        f"ON t.series_key = d.{key} AND t.collector_key = c.collector_key AND {window} "
        f"WHERE {chosen}"
        for table in tables(conn, family, lo, hi)
    ]
    arms.append(
        f"SELECT {cols} FROM {hot_table(family)} t CROSS JOIN {dictionary} d "
        f"ON d.{key} = t.series_key WHERE {window} AND {chosen}"
    )
    params = {"lo": lo, "hi": hi, "after": subjects[0], "last": subjects[1]}
    return [tuple(r) for r in conn.execute(" UNION ALL ".join(arms), params)]


def candidates(
    conn: sqlite3.Connection, dictionary: str, after: bytes, limit: int
) -> list[tuple[int, bytes]]:
    """Return the next ``limit`` subjects of a dictionary in UUID order.

    Args:
        conn: Connection inside a read transaction.
        dictionary: ``interface`` or ``device``.
        after: Exclusive lower bound on the UUID blob (``b""`` for the start).
        limit: Most subjects.

    Returns:
        list[tuple[int, bytes]]: ``(key, uuid blob)`` pairs, ascending.

    Raises:
        sqlite3.Error: If the dictionary cannot be read.
    """
    table, key, uuid = DICTIONARIES[dictionary]
    rows = conn.execute(
        f"SELECT {key}, {uuid} FROM {table} WHERE {uuid} > ? ORDER BY {uuid} LIMIT ?",
        (after, limit),
    )
    return [(int(r[0]), bytes(r[1])) for r in rows]
