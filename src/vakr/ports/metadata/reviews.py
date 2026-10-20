"""Repository protocol for identity reviews (IFID-004, IFID-007, port extension by M3-inventory).

Reconciliation opens a review whenever it cannot match a discovered row to
an interface with certainty; an administrator later maps it or creates a
new identity, and a later discovery may supersede it. Reviews are never
deleted. Every backend must reproduce:

- ``add`` requires ``version == 1`` and an ``OPEN`` review, and raises
  ``DuplicateError`` for a reused ID and ``NotFoundError`` for a missing
  device or candidate interface;
- ``close`` raises, in this order, ``NotFoundError`` (no such review, or a
  missing resolution interface), ``ConflictError`` (stale version), and
  ``ValidationError`` (the review is already closed, or the closing fields
  do not match the state); on success it stores ``version + 1``;
- lists are ordered ``(opened_at, id)``, oldest first, with ``total``.

Dependencies:
    vakr.domain: Review records, IDs, and paging types.

Safety:
    Implementations return domain records only (5.2 rule 2).

Example:
    >>> ReviewFilter().state is None
    True
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from vakr.domain.ids import DeviceId, InterfaceId, ReviewId
from vakr.domain.paging import Page, PageRequest
from vakr.domain.reviews import IdentityReview, ReviewState


@dataclass(frozen=True, slots=True)
class ReviewFilter:
    """Which reviews to list.

    Attributes:
        device_id: Only reviews of this device.
        state: Only reviews in this state; ``None`` lists every state.
    """

    device_id: DeviceId | None = None
    state: ReviewState | None = None


class IdentityReviewRepository(Protocol):
    """Identity reviews, oldest first ``(opened_at, id)``."""

    def add(self, review: IdentityReview) -> IdentityReview:
        """Store a new open review.

        Args:
            review: The review, ``OPEN`` with ``version == 1``.

        Returns:
            IdentityReview: The stored review.

        Raises:
            DuplicateError: If the ID is already used.
            NotFoundError: If the device or a candidate interface does not exist.
            ValidationError: If ``version`` is not 1 or the review is not open.
        """
        ...

    def get(self, review_id: ReviewId) -> IdentityReview | None:
        """Return one review, open or closed.

        Args:
            review_id: The review's UUID.

        Returns:
            IdentityReview | None: The review, or ``None``.

        Raises:
            None
        """
        ...

    def list_page(
        self, page: PageRequest, where: ReviewFilter | None = None
    ) -> Page[IdentityReview]:
        """List reviews oldest first.

        Args:
            page: Limit and cursor.
            where: Filter, or ``None`` for every review.

        Returns:
            Page[IdentityReview]: The reviews and the total match count.

        Raises:
            ValidationError: If ``page.cursor`` is invalid.
        """
        ...

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
        """Close an open review exactly once.

        Args:
            review_id: The review.
            state: ``MAPPED``, ``CREATED``, or ``SUPERSEDED``.
            expected_version: Version the caller read.
            at: When it was closed (UTC).
            resolution: The interface mapped to or created; administrator states only.
            actor: The administrator; administrator states only.

        Returns:
            IdentityReview: The stored review, with ``version`` incremented.

        Raises:
            NotFoundError: If the review or the resolution interface does not exist.
            ConflictError: If ``expected_version`` is stale.
            ValidationError: If the review is closed already, or the closing
                fields do not match ``state``.
        """
        ...
