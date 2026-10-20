"""Keyset pages over SQLite tables in the ports' list orders (API-005, DB-007).

A list's order is a tuple of columns that ends with the unique ``id``
(``KeyOrder``). A page selects ``limit + 1`` rows strictly after the
cursor's key with a row-value comparison such as ``(name, id) > (?, ?)``,
which the composite indexes in ``0001_initial.sql`` answer without a sort;
the extra row says whether another page follows. The cursor is
``vakr.domain.paging.encode_cursor`` of the last row's key with UUID blobs
written as canonical text, so it looks like the reference adapter's and
never contains a row number. The total is counted in the same snapshot.

Dependencies:
    vakr.adapters.sqlite.metadata.transaction: Running the statements.
    vakr.domain.paging, ids: Cursor encoding and UUID parsing.

Safety:
    Table and column names come from this package's constants; every value,
    including each decoded cursor part, is a bound parameter. A cursor part
    of the wrong type is rejected before it reaches SQL.

Example:
    >>> order = KeyOrder(("name", "id"), (Part.TEXT, Part.UUID))
    >>> decode_key(encode_cursor(("a", "0190a3c4-0000-7000-8000-000000000001")), order)[0]
    'a'
"""

import contextlib
import sqlite3
from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum

from vakr.adapters.sqlite.encoding import blob_to_uuid
from vakr.adapters.sqlite.metadata.transaction import Params, SqliteTransaction
from vakr.domain.errors import ValidationError
from vakr.domain.ids import parse_uuid
from vakr.domain.paging import PageRequest, decode_cursor, encode_cursor


class Part(StrEnum):
    """The type of one sort-key column."""

    TEXT = "text"
    INTEGER = "integer"
    UUID = "uuid"


@dataclass(frozen=True, slots=True)
class KeyOrder:
    """A list's total order.

    Attributes:
        columns: Sort columns, ending with the unique ``id``.
        parts: The type of each column.
        descending: Newest first (audit events).
    """

    columns: tuple[str, ...]
    parts: tuple[Part, ...]
    descending: bool = False


@dataclass(frozen=True, slots=True)
class Clause:
    """A ``WHERE`` fragment and its bound values.

    Attributes:
        sql: Boolean SQL expression built from constants only.
        params: Values for its placeholders.
    """

    sql: str = "TRUE"
    params: tuple[object, ...] = ()


def every(clauses: Iterable[Clause]) -> Clause:
    """Combine clauses with ``AND``.

    Args:
        clauses: The fragments.

    Returns:
        Clause: Their conjunction; ``TRUE`` when there are none.

    Raises:
        None
    """
    parts = list(clauses)
    if not parts:
        return Clause()
    sql = " AND ".join(f"({c.sql})" for c in parts)
    return Clause(sql, tuple(p for c in parts for p in c.params))


@dataclass(frozen=True, slots=True)
class Listing:
    """What one list reads.

    Attributes:
        table: Table name.
        select: Column list.
        order: Its total order.
    """

    table: str
    select: str
    order: KeyOrder


def decode_key(cursor: str, order: KeyOrder) -> tuple[object, ...]:
    """Turn a cursor into bound values for ``order``'s columns.

    Args:
        cursor: ``Page.next_cursor`` from this list.
        order: The list's order.

    Returns:
        tuple[object, ...]: One value per column; UUIDs as blobs.

    Raises:
        ValidationError: If the cursor is malformed or a part has the wrong type.
    """
    raw = decode_cursor(cursor, len(order.parts))
    return tuple(_bind(value, part) for value, part in zip(raw, order.parts, strict=True))


def _bind(value: object, part: Part) -> object:
    """Check one cursor part against its column type.

    Args:
        value: Decoded part.
        part: Expected type.

    Returns:
        object: The value to bind.

    Raises:
        ValidationError: If it does not fit.
    """
    if part is Part.TEXT and isinstance(value, str):
        return value
    if part is Part.INTEGER and isinstance(value, int):
        return value
    if part is Part.UUID and isinstance(value, str):
        with contextlib.suppress(ValidationError):
            return parse_uuid(value).bytes
    raise ValidationError("cursor is not valid for this list", field="cursor")


def encode_key(row: sqlite3.Row, order: KeyOrder) -> str:
    """Build the cursor that resumes after ``row``.

    Args:
        row: The last row of a page.
        order: The list's order.

    Returns:
        str: Opaque cursor text.

    Raises:
        None
    """
    parts = (
        str(blob_to_uuid(row[c])) if p is Part.UUID else row[c]
        for c, p in zip(order.columns, order.parts, strict=True)
    )
    return encode_cursor(tuple(parts))


def fetch_page(
    tx: SqliteTransaction, listing: Listing, where: Clause, page: PageRequest
) -> tuple[list[sqlite3.Row], str | None, int]:
    """Read one page and the total match count.

    Args:
        tx: The unit of work's transaction.
        listing: Table, columns, and order.
        where: The filter.
        page: Limit and cursor.

    Returns:
        tuple[list[sqlite3.Row], str | None, int]: Rows, next cursor, total.

    Raises:
        ValidationError: If the cursor is invalid.
        StorageError: If the database cannot be read.
    """
    order = listing.order
    after = decode_key(page.cursor, order) if page.cursor is not None else None
    # Identifiers below come from Listing constants; values are all bound.
    count_sql = f"SELECT count(*) FROM {listing.table} WHERE {where.sql}"  # noqa: S608
    total = int(tx.query(count_sql, where.params)[0][0])
    bounded = where
    if after is not None:
        marks = ", ".join("?" for _ in after)
        op = "<" if order.descending else ">"
        bounded = every([where, Clause(f"({', '.join(order.columns)}) {op} ({marks})", after)])
    direction = " DESC" if order.descending else ""
    order_by = ", ".join(f"{column}{direction}" for column in order.columns)
    sql = (
        f"SELECT {listing.select} FROM {listing.table} "  # noqa: S608
        f"WHERE {bounded.sql} ORDER BY {order_by} LIMIT ?"
    )
    params: Params = (*bounded.params, page.limit + 1)
    rows = tx.query(sql, params)
    more = len(rows) > page.limit
    rows = rows[: page.limit]
    return rows, encode_key(rows[-1], order) if more else None, total
