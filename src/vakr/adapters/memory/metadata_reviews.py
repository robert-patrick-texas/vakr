"""In-memory identity-review repository and change counter (IFID-007, 6.2).

The reference implementation of ``IdentityReviewRepository``: reviews are
staged in the unit of work's transaction like every other record and are
never deleted. ``change_counter`` sums one plus ``version`` over every
collection-relevant record, which grows on every add and every update.

Dependencies:
    vakr.adapters.memory.metadata_store, paging: Transaction and pages.
    vakr.domain: Review records and errors.

Safety:
    Nothing here deletes a review.

Example:
    >>> from vakr.adapters.memory.metadata_store import MemoryMetadataStore, Transaction
    >>> change_counter(Transaction(MemoryMetadataStore()))
    0
"""

from dataclasses import replace
from datetime import datetime

from vakr.adapters.memory.metadata_store import Transaction
from vakr.adapters.memory.paging import paginate
from vakr.domain.errors import ConflictError, DuplicateError, NotFoundError, ValidationError
from vakr.domain.ids import InterfaceId, ReviewId
from vakr.domain.paging import Page, PageRequest
from vakr.domain.reviews import IdentityReview, ReviewState
from vakr.domain.timestamps import to_epoch_us
from vakr.ports.metadata.reviews import ReviewFilter

COUNTED_TABLES = ("device", "interface", "site", "tag", "group", "policy", "review")
"""Tables whose changes can alter what a collector polls (``change_counter``)."""


def change_counter(tx: Transaction) -> int:
    """Sum one plus ``version`` over every counted record.

    Args:
        tx: The unit of work's transaction.

    Returns:
        int: The counter as the transaction sees the data.

    Raises:
        None
    """
    return sum(1 + record.version for table in COUNTED_TABLES for record in tx.records(table))


class MemoryReviewRepository:
    """Identity reviews, oldest first ``(opened_at, id)``."""

    def __init__(self, tx: Transaction) -> None:
        """Bind the repository to a transaction.

        Args:
            tx: The unit of work's transaction.

        Returns:
            None

        Raises:
            None
        """
        self._tx = tx

    def add(self, review: IdentityReview) -> IdentityReview:
        """Stage a new open review.

        Args:
            review: The review.

        Returns:
            IdentityReview: The review.

        Raises:
            ValidationError, DuplicateError, NotFoundError: As the port documents.
        """
        if review.version != 1 or not review.is_open:
            raise ValidationError("a new review is open with version 1", field="version")
        if self._tx.get("review", review.id) is not None:
            raise DuplicateError("identity_review", "id", review.id)
        if self._tx.get("device", review.device_id) is None:
            raise NotFoundError("device", review.device_id)
        for candidate in review.candidates:
            self._interface_on(candidate, review)
        self._tx.put("review", review, None)
        return review

    def get(self, review_id: ReviewId) -> IdentityReview | None:
        """Return one review.

        Args:
            review_id: The review's UUID.

        Returns:
            IdentityReview | None: The review, or ``None``.

        Raises:
            None
        """
        return self._tx.get("review", review_id)

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
        reviews = [
            r for r in self._tx.records("review")
            if (w.device_id is None or r.device_id == w.device_id)
            and (w.state is None or r.state is w.state)
        ]  # fmt: skip
        return paginate(reviews, lambda r: (to_epoch_us(r.opened_at), str(r.id)), page)

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
            IdentityReview: The staged review.

        Raises:
            NotFoundError, ConflictError, ValidationError: As the port documents.
        """
        stored: IdentityReview | None = self._tx.get("review", review_id)
        if stored is None:
            raise NotFoundError("identity_review", review_id)
        if resolution is not None:
            self._interface_on(resolution, stored)
        if expected_version != stored.version:
            raise ConflictError("identity_review", review_id, expected_version=expected_version,
                                actual_version=stored.version)  # fmt: skip
        if not stored.is_open:
            raise ValidationError("the review is already closed", field="state")
        closed = replace(
            stored, state=state, closed_at=at, resolution=resolution, actor=actor,
            version=stored.version + 1,
        )  # fmt: skip
        self._tx.put("review", closed, stored.version)
        return closed

    def _interface_on(self, interface_id: InterfaceId, review: IdentityReview) -> None:
        """Require an interface that exists on the review's device.

        Args:
            interface_id: The interface.
            review: The review naming it.

        Returns:
            None

        Raises:
            NotFoundError: If the interface does not exist.
            ValidationError: If it belongs to another device.
        """
        interface = self._tx.get("interface", interface_id)
        if interface is None:
            raise NotFoundError("interface", interface_id)
        if interface.device_id != review.device_id:
            raise ValidationError("a review's interfaces are on its device", field="candidates")
