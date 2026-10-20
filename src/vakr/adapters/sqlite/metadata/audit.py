"""SQLite audit repository: append-only events, newest first (SEC-018).

Events are inserted and listed, never changed: the schema's triggers
refuse ``UPDATE`` and ``DELETE`` on ``audit_event``. The list order is
``(occurred_at, id)`` descending, served by the ``audit_event_by_*``
indexes for the unfiltered, per-actor, and per-object lists.

Dependencies:
    vakr.adapters.sqlite.connection, metadata.paging, metadata.transaction.
    vakr.domain: The audit record and errors.

Safety:
    ``summary`` arrives already redacted (``AuditEvent`` contract); this
    module stores it verbatim and never logs it.

Example:
    >>> SqliteAuditRepository(tx).list_page(PageRequest()).total  # doctest: +SKIP
    0
"""

import sqlite3

from vakr.adapters.sqlite.encoding import (
    blob_to_uuid,
    decode_time,
    encode_time,
    optional_blob,
    optional_uuid,
)
from vakr.adapters.sqlite.errors import ErrorContext
from vakr.adapters.sqlite.metadata.paging import Clause, KeyOrder, Listing, Part, every, fetch_page
from vakr.adapters.sqlite.metadata.transaction import SqliteTransaction
from vakr.domain.control import AuditEvent
from vakr.domain.enums import AuditOutcome
from vakr.domain.errors import DuplicateError, StorageError
from vakr.domain.ids import AuditEventId
from vakr.domain.paging import Page, PageRequest
from vakr.ports.metadata.filters import AuditFilter

_COLUMNS = (
    "id", "occurred_at", "actor", "action", "object_type", "object_id", "outcome", "summary",
    "request_id",
)  # fmt: skip
_LISTING = Listing(
    "audit_event",
    ", ".join(_COLUMNS),
    KeyOrder(("occurred_at", "id"), (Part.INTEGER, Part.UUID), descending=True),
)
_INSERT = (
    f"INSERT INTO audit_event ({_LISTING.select}) "  # noqa: S608  # constant columns
    f"VALUES ({', '.join('?' for _ in _COLUMNS)})"
)


def _decode(r: sqlite3.Row) -> AuditEvent:
    """Decode an audit row.

    Args:
        r: The row.

    Returns:
        AuditEvent: The event.

    Raises:
        StorageError: If the row no longer forms a valid event.
    """
    try:
        return AuditEvent(
            id=AuditEventId(blob_to_uuid(r["id"])), occurred_at=decode_time(r["occurred_at"]),
            actor=r["actor"], action=r["action"], object_type=r["object_type"],
            object_id=optional_uuid(r["object_id"]), outcome=AuditOutcome(r["outcome"]),
            summary=r["summary"], request_id=r["request_id"],
        )  # fmt: skip
    except ValueError:
        raise StorageError("a stored audit event is not valid", retryable=False) from None


class SqliteAuditRepository:
    """Append-only audit events in one unit of work."""

    def __init__(self, tx: SqliteTransaction) -> None:
        """Bind to a unit of work's transaction.

        Args:
            tx: The transaction.

        Returns:
            None

        Raises:
            None
        """
        self._tx = tx

    def append(self, event: AuditEvent) -> None:
        """Record an event in the current unit of work.

        Args:
            event: The redacted event.

        Returns:
            None

        Raises:
            DuplicateError: If the event ID is already used.
            StorageError: If the write path stays busy or the write fails.
        """
        self._tx.begin_write()
        if self._tx.query("SELECT 1 FROM audit_event WHERE id = ?", (event.id.bytes,)):
            raise DuplicateError("audit", "id", event.id)
        values = (
            event.id.bytes, encode_time(event.occurred_at), event.actor, event.action,
            event.object_type, optional_blob(event.object_id), event.outcome.value, event.summary,
            event.request_id,
        )  # fmt: skip
        self._tx.execute(_INSERT, values, ErrorContext("audit", event.id))

    def list_page(self, page: PageRequest, where: AuditFilter | None = None) -> Page[AuditEvent]:
        """List events newest first, by ``(occurred_at, id)`` descending.

        Args:
            page: Limit and cursor.
            where: Filter, or ``None`` for every event.

        Returns:
            Page[AuditEvent]: The page, with ``total``.

        Raises:
            ValidationError: If the cursor is invalid.
        """
        w = where or AuditFilter()
        parts: list[Clause] = []
        if w.object_id is not None:
            parts.append(Clause("object_id = ?", (w.object_id.bytes,)))
        if w.actor is not None:
            parts.append(Clause("actor = ?", (w.actor,)))
        rows, cursor, total = fetch_page(self._tx, _LISTING, every(parts), page)
        return Page(tuple(_decode(r) for r in rows), cursor, total)
