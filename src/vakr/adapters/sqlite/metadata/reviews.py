"""SQLite identity-review repository and metadata change counter (IFID-007, 6.2).

Reviews live in ``identity_review`` (migration ``0002``). A review is
closed exactly once; the schema's trigger refuses any later update, and no
row is ever deleted. ``change_counter`` sums ``1 + version`` over every
collection-relevant table in one statement: records are never deleted and
versions only grow, so any add or update raises it, in every process.

Dependencies:
    vakr.adapters.sqlite.encoding, errors, metadata.paging, transaction.
    vakr.domain: Review records, IDs, and errors.

Safety:
    SQL is fixed text with bound values. Nothing here deletes.

Example:
    >>> SqliteReviewRepository(tx).list_page(PageRequest()).total  # doctest: +SKIP
    0
"""

import json
import sqlite3
from dataclasses import replace
from datetime import datetime
from uuid import UUID

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
from vakr.domain.enums import ReconcileOutcome
from vakr.domain.errors import (
    ConflictError,
    DuplicateError,
    NotFoundError,
    StorageError,
    ValidationError,
)
from vakr.domain.ids import DeviceId, InterfaceId, ReviewId
from vakr.domain.paging import Page, PageRequest
from vakr.domain.reviews import IdentityReview, ReviewState
from vakr.ports.metadata.reviews import ReviewFilter

_COLUMNS = (
    "id", "device_id", "opened_at", "outcome", "if_index", "if_name", "if_descr", "if_alias",
    "if_type", "mac", "if_speed", "if_high_speed", "addresses", "candidates", "evidence", "state",
    "closed_at", "resolution", "actor", "version",
)  # fmt: skip
_LISTING = Listing(
    "identity_review", ", ".join(_COLUMNS), KeyOrder(("opened_at", "id"), (Part.INTEGER, Part.UUID))
)
_INSERT = (
    f"INSERT INTO identity_review ({_LISTING.select}) "  # noqa: S608  # constant columns
    f"VALUES ({', '.join('?' for _ in _COLUMNS)})"
)
_SELECT_ONE = f"SELECT {_LISTING.select} FROM identity_review WHERE id = ?"  # noqa: S608
_CLOSE = (
    "UPDATE identity_review SET state = ?, closed_at = ?, resolution = ?, actor = ?, "
    "version = ? WHERE id = ? AND version = ? AND state = 'open'"
)
# Every collection-relevant table; identity_history and audit_event only ever accompany
# changes counted here.
_COUNTER = (
    "SELECT (SELECT count(*) + coalesce(sum(version), 0) FROM device)"
    " + (SELECT count(*) + coalesce(sum(version), 0) FROM interface)"
    " + (SELECT count(*) + coalesce(sum(version), 0) FROM site)"
    " + (SELECT count(*) + coalesce(sum(version), 0) FROM tag)"
    " + (SELECT count(*) + coalesce(sum(version), 0) FROM device_group)"
    " + (SELECT count(*) + coalesce(sum(version), 0) FROM collection_policy)"
    " + (SELECT count(*) + coalesce(sum(version), 0) FROM identity_review)"
)


def change_counter(tx: SqliteTransaction) -> int:
    """Sum ``1 + version`` over every collection-relevant record.

    Args:
        tx: The unit of work's transaction.

    Returns:
        int: The counter in the current snapshot.

    Raises:
        StorageError: If the database cannot be read.
    """
    return int(tx.query(_COUNTER)[0][0])


def _encode(r: IdentityReview) -> tuple[object, ...]:
    """Encode a review as a row.

    Args:
        r: The review.

    Returns:
        tuple[object, ...]: Values in ``_COLUMNS`` order.

    Raises:
        None
    """
    return (
        r.id.bytes, r.device_id.bytes, encode_time(r.opened_at), r.outcome.value, r.if_index,
        r.if_name, r.if_descr, r.if_alias, r.if_type, r.mac, r.if_speed, r.if_high_speed,
        json.dumps(list(r.addresses)), json.dumps([str(c) for c in r.candidates]),
        json.dumps(list(r.evidence), ensure_ascii=False), r.state.value,
        None if r.closed_at is None else encode_time(r.closed_at), optional_blob(r.resolution),
        r.actor, r.version,
    )  # fmt: skip


def _decode(r: sqlite3.Row) -> IdentityReview:
    """Decode a review row.

    Args:
        r: The row.

    Returns:
        IdentityReview: The review.

    Raises:
        StorageError: If the row no longer forms a valid review.
    """
    try:
        closed = r["closed_at"]
        resolution = optional_uuid(r["resolution"])
        return IdentityReview(
            id=ReviewId(blob_to_uuid(r["id"])), device_id=DeviceId(blob_to_uuid(r["device_id"])),
            opened_at=decode_time(r["opened_at"]), outcome=ReconcileOutcome(r["outcome"]),
            if_index=r["if_index"], if_name=r["if_name"], if_descr=r["if_descr"],
            if_alias=r["if_alias"],
            candidates=tuple(InterfaceId(UUID(c)) for c in json.loads(r["candidates"])),
            evidence=tuple(json.loads(r["evidence"])), if_type=r["if_type"], mac=r["mac"],
            if_speed=r["if_speed"], if_high_speed=r["if_high_speed"],
            addresses=tuple(json.loads(r["addresses"])), state=ReviewState(r["state"]),
            closed_at=None if closed is None else decode_time(closed),
            resolution=None if resolution is None else InterfaceId(resolution),
            actor=r["actor"], version=r["version"],
        )  # fmt: skip
    except ValueError, TypeError:
        raise StorageError("a stored identity review is not valid", retryable=False) from None


class SqliteReviewRepository:
    """Identity reviews in one unit of work, oldest first ``(opened_at, id)``."""

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

    def add(self, review: IdentityReview) -> IdentityReview:
        """Store a new open review.

        Args:
            review: The review.

        Returns:
            IdentityReview: The review.

        Raises:
            ValidationError, DuplicateError, NotFoundError: As the port documents.
            StorageError: If the write path stays busy or the write fails.
        """
        if review.version != 1 or not review.is_open:
            raise ValidationError("a new review is open with version 1", field="version")
        self._tx.begin_write()
        if self.get(review.id) is not None:
            raise DuplicateError("identity_review", "id", review.id)
        if not self._tx.query("SELECT 1 FROM device WHERE id = ?", (review.device_id.bytes,)):
            raise NotFoundError("device", review.device_id)
        for candidate in review.candidates:
            self._require_on_device(candidate, review.device_id)
        self._tx.execute(_INSERT, _encode(review), ErrorContext("identity_review", review.id))
        return review

    def get(self, review_id: ReviewId) -> IdentityReview | None:
        """Return one review.

        Args:
            review_id: The review's UUID.

        Returns:
            IdentityReview | None: The review, or ``None``.

        Raises:
            StorageError: If the database cannot be read.
        """
        rows = self._tx.query(_SELECT_ONE, (review_id.bytes,))
        return _decode(rows[0]) if rows else None

    def list_page(
        self, page: PageRequest, where: ReviewFilter | None = None
    ) -> Page[IdentityReview]:
        """List reviews oldest first.

        Args:
            page: Limit and cursor.
            where: Filter, or ``None``.

        Returns:
            Page[IdentityReview]: The page, with ``total``.

        Raises:
            ValidationError: If the cursor is invalid.
        """
        w = where or ReviewFilter()
        parts: list[Clause] = []
        if w.device_id is not None:
            parts.append(Clause("device_id = ?", (w.device_id.bytes,)))
        if w.state is not None:
            parts.append(Clause("state = ?", (w.state.value,)))
        rows, cursor, total = fetch_page(self._tx, _LISTING, every(parts), page)
        return Page(tuple(_decode(r) for r in rows), cursor, total)

    def close(
        self,
        review_id: ReviewId,
        state: ReviewState,
        *,
        expected_version: int,
        at: datetime,
        resolution: InterfaceId | None = None,
        actor: str | None = None,
    ) -> IdentityReview:
        """Close an open review.

        Args:
            review_id: The review.
            state: The closing state.
            expected_version: Version the caller read.
            at: When it was closed.
            resolution: Interface mapped to or created.
            actor: The administrator.

        Returns:
            IdentityReview: The stored review.

        Raises:
            NotFoundError, ConflictError, ValidationError: As the port documents.
            StorageError: If the write path stays busy or the write fails.
        """
        self._tx.begin_write()
        stored = self.get(review_id)
        if stored is None:
            raise NotFoundError("identity_review", review_id)
        if resolution is not None:
            self._require_on_device(resolution, stored.device_id)
        if expected_version != stored.version:
            raise ConflictError("identity_review", review_id, expected_version=expected_version,
                                actual_version=stored.version)  # fmt: skip
        if not stored.is_open:
            raise ValidationError("the review is already closed", field="state")
        closed = replace(stored, state=state, closed_at=at, resolution=resolution, actor=actor,
                         version=stored.version + 1)  # fmt: skip
        values = (state.value, encode_time(at), optional_blob(resolution), actor, closed.version,
                  review_id.bytes, stored.version)  # fmt: skip
        self._tx.execute(_CLOSE, values, ErrorContext("identity_review", review_id))
        return closed

    def _require_on_device(self, interface_id: InterfaceId, device_id: DeviceId) -> None:
        """Require an interface that exists on ``device_id``.

        Args:
            interface_id: The interface.
            device_id: The review's device.

        Returns:
            None

        Raises:
            NotFoundError: If the interface does not exist.
            ValidationError: If it belongs to another device.
        """
        rows = self._tx.query("SELECT device_id FROM interface WHERE id = ?", (interface_id.bytes,))
        if not rows:
            raise NotFoundError("interface", interface_id)
        if blob_to_uuid(rows[0][0]) != device_id:
            raise ValidationError("a review's interfaces are on its device", field="candidates")
