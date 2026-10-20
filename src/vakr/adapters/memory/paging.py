"""Keyset pagination over in-memory records, matching what an SQL adapter does.

Records are sorted by a key tuple; a cursor encodes the key of the last
record returned, and the next page starts strictly after it. Because the key
always ends with a unique ID, the order is total and a page boundary is
never ambiguous, so concurrent inserts cannot repeat or skip a record.

Dependencies:
    vakr.domain.paging: Cursor encoding and page types.

Safety:
    A cursor whose parts have the wrong types is rejected, not compared.

Example:
    >>> page = paginate(["b", "a", "c"], lambda s: (s,), PageRequest(limit=2))
    >>> page.items, page.total, paginate(["b", "a", "c"], lambda s: (s,),
    ...     PageRequest(limit=2, cursor=page.next_cursor)).items
    (('a', 'b'), 3, ('c',))
"""

from collections.abc import Callable, Iterable
from typing import Any

from vakr.domain.errors import ValidationError
from vakr.domain.paging import CursorKey, Page, PageRequest, decode_cursor, encode_cursor


def paginate[T](
    records: Iterable[T],
    key: Callable[[T], CursorKey],
    page: PageRequest,
    *,
    count: bool = True,
) -> Page[T]:
    """Return one page of ``records`` in ascending ``key`` order.

    Args:
        records: Every matching record, in any order.
        key: Total sort key; its last part must be unique per record.
        page: Limit and cursor.
        count: Fill ``Page.total``; ``False`` leaves it ``None``.

    Returns:
        Page[T]: The page.

    Raises:
        ValidationError: If the cursor is malformed or its parts do not match
            the key's types.
    """
    ordered = sorted(records, key=key)
    total = len(ordered) if count else None
    if page.cursor is not None and ordered:
        after: tuple[Any, ...] = decode_cursor(page.cursor, len(key(ordered[0])))
        try:
            # Parts of a forged cursor may not compare with the real key's types.
            ordered = [r for r in ordered if _after(key(r), after)]
        except TypeError:
            raise ValidationError("cursor is not valid for this list", field="cursor") from None
    items = ordered[: page.limit]
    more = len(ordered) > page.limit
    return Page(tuple(items), encode_cursor(key(items[-1])) if more else None, total)


def _after(candidate: tuple[Any, ...], cursor: tuple[Any, ...]) -> bool:
    """Compare a record key with a cursor key.

    Args:
        candidate: A record's sort key.
        cursor: The decoded cursor key.

    Returns:
        bool: ``True`` when the record sorts after the cursor.

    Raises:
        TypeError: If corresponding parts have incomparable types.
    """
    return candidate > cursor
