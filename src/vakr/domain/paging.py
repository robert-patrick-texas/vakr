"""Bounded pagination with stable, opaque cursors (API-005).

Every list a repository returns is a page: at most ``MAX_PAGE_LIMIT`` items,
the cursor for the next page, and the total match count. Cursors are keyset
cursors: they encode the sort key of the last item returned, not an offset,
so inserting a device while someone pages through the list never repeats or
skips a row. They are opaque to callers and must never contain a database
row ID (API-002).

``encode_cursor`` / ``decode_cursor`` give every backend the same cursor
format (URL-safe base64 of a canonical JSON array) so pages from different
adapters look alike; a backend may use another opaque format if it must.
``offset_cursor`` / ``offset_of`` are the one exception to keyset cursors:
lists ordered by live status carry a position (API-T02, E.30 #7).

Dependencies:
    base64, binascii: URL-safe cursor text.
    vakr.domain.json_values: Canonical JSON for the encoded key.

Safety:
    Decoding validates shape and types and rejects anything else with
    ``ValidationError``, so a tampered cursor cannot reach a query.

Example:
    >>> cursor = encode_cursor(("core1", "0190a3c4-0000-7000-8000-000000000001"))
    >>> decode_cursor(cursor, 2)
    ('core1', '0190a3c4-0000-7000-8000-000000000001')
    >>> PageRequest().limit
    100
"""

import base64
import binascii
import json
from dataclasses import dataclass

from vakr.domain.checks import require_int
from vakr.domain.errors import ValidationError
from vakr.domain.json_values import canonical_json

DEFAULT_PAGE_LIMIT = 100
"""Items per page when the caller does not say (API-005)."""

MAX_PAGE_LIMIT = 500
"""Largest page any repository returns (API-005)."""

MAX_CURSOR_LENGTH = 1024  # a key tuple encodes to well under this; longer text is not ours

type CursorKey = tuple[str | int, ...]
"""The sort key a cursor encodes: strings and integers only."""


@dataclass(frozen=True, slots=True)
class PageRequest:
    """Which page of a list to return.

    Attributes:
        limit: Maximum items, 1 to ``MAX_PAGE_LIMIT``.
        cursor: ``Page.next_cursor`` from the previous page, or ``None`` for
            the first page.
    """

    limit: int = DEFAULT_PAGE_LIMIT
    cursor: str | None = None

    def __post_init__(self) -> None:
        """Validate the request.

        Args:
            None

        Returns:
            None

        Raises:
            ValidationError: If ``limit`` is out of range or ``cursor`` is
                not a short string.
        """
        require_int(self.limit, "limit", minimum=1, maximum=MAX_PAGE_LIMIT)
        if self.cursor is not None and (
            not isinstance(self.cursor, str) or not 0 < len(self.cursor) <= MAX_CURSOR_LENGTH
        ):
            raise ValidationError("cursor is not a valid page cursor", field="cursor")


@dataclass(frozen=True, slots=True)
class Page[T]:
    """One page of results.

    Attributes:
        items: The items, in the list's documented stable order.
        next_cursor: Cursor for the following page, or ``None`` on the last page.
        total: Number of items matching the request across all pages, or
            ``None`` where counting is not bounded-cost (raw telemetry).
    """

    items: tuple[T, ...]
    next_cursor: str | None
    total: int | None


def encode_cursor(key: CursorKey) -> str:
    """Encode a sort key as an opaque cursor.

    Args:
        key: The sort key of the last item on a page.

    Returns:
        str: URL-safe base64 text without padding.

    Raises:
        ValidationError: If ``key`` holds anything but strings and integers.
    """
    if not all(isinstance(part, (str, int)) and not isinstance(part, bool) for part in key):
        raise ValidationError("a cursor key holds only strings and integers")
    return base64.urlsafe_b64encode(canonical_json(list(key))).rstrip(b"=").decode("ascii")


def decode_cursor(cursor: str, arity: int) -> CursorKey:
    """Decode a cursor produced by ``encode_cursor``.

    Args:
        cursor: The cursor text.
        arity: Number of parts the sort key must have.

    Returns:
        CursorKey: The sort key.

    Raises:
        ValidationError: If the cursor is malformed or has the wrong shape.
    """
    try:
        raw = base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4))
        key = json.loads(raw.decode("utf-8"))
    except binascii.Error, UnicodeDecodeError, ValueError:
        raise ValidationError("cursor is not a valid page cursor", field="cursor") from None
    valid = isinstance(key, list) and len(key) == arity
    if not valid or not all(isinstance(p, (str, int)) and not isinstance(p, bool) for p in key):
        raise ValidationError("cursor is not a valid page cursor", field="cursor")
    return tuple(key)


MAX_OFFSET = 1_000_000
"""Largest position an offset cursor may name; far beyond any bounded list (E.29)."""
_OFFSET_TAG = "o"


def offset_cursor(offset: int | None) -> str | None:
    """Encode a position in a freshly computed order as an opaque cursor (E.30 #7).

    Device and interface lists are ordered by live status or traffic, which
    no keyset can follow, so their cursor carries the position instead. The
    API and its local clients (``vakr status``, ``vakr tui``) share this codec.

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
