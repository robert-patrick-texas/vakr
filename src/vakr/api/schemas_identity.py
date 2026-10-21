"""Shapes of the identity endpoints: history, open reviews, and decisions (IFID-005, IFID-007).

An interface keeps its UUID across ``ifIndex`` renumbering; every decision
that kept or changed an identity is recorded in its identity history. When
discovery cannot tell which existing interface a row is, it opens a review,
and an administrator answers it through
``POST /api/v1/interfaces/{uuid}/identity-resolution``.

Dependencies:
    dataclasses, pydantic: Response shapes and the request body.

Safety:
    Reviews and history carry interface names and indexes only.

Example:
    >>> ResolutionRequest(review_id="0190a3c4-0000-7000-8000-000000000001", decision="new").decision
    'new'
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from vakr.api.paging import PageInfo


@dataclass(frozen=True, slots=True)
class IdentityEntryOut:
    """One identity decision (IFID-005).

    Attributes:
        id: Entry UUID.
        decided_at: When it was decided.
        outcome: Reconciliation outcome, such as ``matched`` or ``renumbered``.
        source: ``automatic`` or ``admin``.
        previous_if_index: ``ifIndex`` before, if any.
        observed_if_index: ``ifIndex`` observed, if any.
        score: Match score of the chosen candidate.
        runner_up_score: Score of the next-best candidate (IFID-006).
        evidence: Human-readable evidence.
        actor: The administrator, for ``admin`` decisions.
    """

    id: UUID
    decided_at: datetime
    outcome: str
    source: str
    previous_if_index: int | None
    observed_if_index: int | None
    score: float | None
    runner_up_score: float | None
    evidence: tuple[str, ...]
    actor: str | None


@dataclass(frozen=True, slots=True)
class IdentityHistoryPage:
    """A page of one interface's identity history, oldest first.

    Attributes:
        interface_id: The interface.
        items: The entries.
        page: Paging facts.
    """

    interface_id: UUID
    items: tuple[IdentityEntryOut, ...]
    page: PageInfo


@dataclass(frozen=True, slots=True)
class CandidateOut:
    """An existing interface a review's row may be.

    Attributes:
        interface_id: The candidate.
        name: Its ``ifName`` (else ``ifDescr``).
        if_index: Its current ``ifIndex``.
        lifecycle: Its lifecycle state.
    """

    interface_id: UUID
    name: str
    if_index: int | None
    lifecycle: str


@dataclass(frozen=True, slots=True)
class ReviewOut:
    """An open identity review (IFID-007).

    Attributes:
        id: Review UUID.
        device_id: The device.
        device: Its display name.
        opened_at: When discovery opened it.
        outcome: Why it was opened.
        if_index: The discovered row's ``ifIndex``.
        if_name: Its ``ifName``.
        if_descr: Its ``ifDescr``.
        if_alias: Its ``ifAlias``.
        candidates: Existing interfaces it may be.
        evidence: Human-readable evidence.
    """

    id: UUID
    device_id: UUID
    device: str
    opened_at: datetime
    outcome: str
    if_index: int
    if_name: str
    if_descr: str
    if_alias: str
    candidates: tuple[CandidateOut, ...]
    evidence: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ReviewPage:
    """A page of open reviews, oldest first.

    Attributes:
        items: The reviews.
        page: Paging facts.
    """

    items: tuple[ReviewOut, ...]
    page: PageInfo


class ResolutionRequest(BaseModel):
    """Body of ``POST /api/v1/interfaces/{uuid}/identity-resolution``.

    ``map``: the review's row *is* the interface in the path; it takes the
    row's ``ifIndex`` and names and its history continues. ``new``: the row
    is a different interface and gets a new identity; the interface in the
    path must be on the review's device (it is the one the administrator
    was looking at) and keeps its own identity.

    Attributes:
        review_id: The open review.
        decision: ``map`` or ``new``.
    """

    model_config = ConfigDict(extra="forbid")

    review_id: UUID
    decision: Literal["map", "new"]


@dataclass(frozen=True, slots=True)
class ResolutionOut:
    """The decision's result.

    Attributes:
        review_id: The closed review.
        decision: ``map`` or ``new``.
        interface_id: The interface the row now is.
        if_index: Its ``ifIndex``.
    """

    review_id: UUID
    decision: str
    interface_id: UUID
    if_index: int | None
