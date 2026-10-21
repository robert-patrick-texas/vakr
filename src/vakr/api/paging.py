"""One paging contract for every list endpoint: ``limit``, ``cursor``, ``next_cursor`` (API-005).

Every list answer carries ``page``: the page size used, the number of items
matching across all pages, and an opaque ``next_cursor`` (``null`` on the
last page). The client passes ``next_cursor`` back as ``cursor`` and changes
nothing else. Pages hold 1 to 500 items, 100 by default, and cursors are at
most 1,024 characters (E.19).

Two kinds of list sit behind the same contract. Repository lists (sites,
tags, groups, collectors, identity history, reviews) use the store's keyset
cursors, so a record added while someone pages never repeats or skips a
row. Device and interface lists are ordered by live status or traffic,
which no keyset can follow; their cursor carries the position in the
order, and each page is computed afresh, so a device whose status changes
between two page requests can move across the page boundary. Under a fixed
snapshot the traversal has no duplicates and no gaps (API-T02).

Dependencies:
    vakr.domain.paging: Cursor encoding and bounds.

Safety:
    Cursors are validated before use; a tampered one is a 422, never a query.

Example:
    >>> offset_of(offset_cursor(200))
    200
    >>> offset_of(None)
    0
"""

from dataclasses import dataclass

from vakr.domain.errors import ValidationError
from vakr.domain.paging import (
    DEFAULT_PAGE_LIMIT,
    MAX_CURSOR_LENGTH,
    MAX_PAGE_LIMIT,
    decode_cursor,
    encode_cursor,
)

__all__ = [
    "DEFAULT_PAGE_LIMIT",
    "MAX_CURSOR_LENGTH",
    "MAX_OFFSET",
    "MAX_PAGE_LIMIT",
    "PageInfo",
    "offset_cursor",
    "offset_of",
]

MAX_OFFSET = 1_000_000
"""Largest position a cursor may name; far beyond any bounded list (E.29)."""
_OFFSET_TAG = "o"


@dataclass(frozen=True, slots=True)
class PageInfo:
    """Paging facts of one list answer (API-005).

    Attributes:
        limit: Page size used.
        total: Items matching across all pages, or ``None`` when not counted.
        next_cursor: Pass as ``cursor`` for the next page; ``None`` on the last page.
    """

    limit: int
    total: int | None
    next_cursor: str | None


def offset_cursor(offset: int | None) -> str | None:
    """Encode a list position as an opaque cursor.

    Args:
        offset: Index of the next page's first row, or ``None`` at the end.

    Returns:
        str | None: The cursor, or ``None``.

    Raises:
        None
    """
    return None if offset is None else encode_cursor((_OFFSET_TAG, offset))


def offset_of(cursor: str | None) -> int:
    """Decode a cursor made by ``offset_cursor``.

    Args:
        cursor: The cursor, or ``None`` for the first page.

    Returns:
        int: The position.

    Raises:
        ValidationError: If the cursor is not one of these.
    """
    if cursor is None:
        return 0
    tag, offset = decode_cursor(cursor, 2)
    if tag != _OFFSET_TAG or not isinstance(offset, int) or not 0 <= offset <= MAX_OFFSET:
        raise ValidationError("cursor is not a valid page cursor", field="cursor")
    return offset
