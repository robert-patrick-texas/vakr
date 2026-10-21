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
    vakr.domain.paging: Cursor encoding and bounds; the offset codec lives
        there so the local clients share it without importing the API.

Safety:
    Cursors are validated before use; a tampered one is a 422, never a query.

Example:
    >>> offset_of(offset_cursor(200))
    200
    >>> offset_of(None)
    0
"""

from dataclasses import dataclass

from vakr.domain.paging import (
    DEFAULT_PAGE_LIMIT,
    MAX_CURSOR_LENGTH,
    MAX_OFFSET,
    MAX_PAGE_LIMIT,
    offset_cursor,
    offset_of,
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
