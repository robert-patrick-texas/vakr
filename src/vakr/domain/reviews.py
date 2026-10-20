"""Identity reviews: discovered interfaces waiting for an administrator (IFID-004, IFID-007).

When rediscovery cannot tell for certain which persistent interface a row
of the agent's ``ifTable`` is (a name changed at the same ``ifIndex``, two
ports swapped, a name appears twice), vakr never guesses: merging the wrong
histories would put one circuit's traffic on another's graph. Instead it
opens an ``IdentityReview`` holding what the agent reported, the candidate
identities, and the evidence. Until an administrator maps the row to an
existing interface or creates a new identity, neither the row nor any
candidate receives observations.

A review is closed exactly once: ``MAPPED`` or ``CREATED`` by an
administrator, or ``SUPERSEDED`` when a later discovery no longer shows the
same case. The record keeps the observed row so a resolution never needs
the device to be polled again.

Dependencies:
    dataclasses, enum: Frozen record and its state vocabulary.
    vakr.domain.checks, addressing, timestamps: Shared invariant checks.

Safety:
    No field holds credentials; descriptions are operator-visible text.

Example:
    >>> from datetime import UTC, datetime
    >>> from vakr.domain.ids import DeviceId, InterfaceId, ReviewId, new_id
    >>> t = datetime(2026, 10, 20, tzinfo=UTC)
    >>> r = IdentityReview(new_id(ReviewId), new_id(DeviceId), t, ReconcileOutcome.PROBABLE,
    ...                    5, "Te0/0/5", "TenGigabitEthernet0/0/5", "", (new_id(InterfaceId),))
    >>> r.state, r.is_open
    (<ReviewState.OPEN: 'open'>, True)
"""

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from vakr.domain.addressing import require_interface_addresses, require_mac
from vakr.domain.checks import (
    require_enum,
    require_int,
    require_name,
    require_order,
    require_text,
    require_uuid,
)
from vakr.domain.enums import ReconcileOutcome
from vakr.domain.errors import ValidationError
from vakr.domain.ids import DeviceId, InterfaceId, ReviewId
from vakr.domain.timestamps import require_utc

REVIEW_OUTCOMES = frozenset({ReconcileOutcome.PROBABLE, ReconcileOutcome.AMBIGUOUS})
"""The only outcomes that open a review; the others merge or create automatically."""


class ReviewState(StrEnum):
    """Where an identity review is in its life."""

    OPEN = "open"
    MAPPED = "mapped"  # an administrator mapped the row to an existing interface
    CREATED = "created"  # an administrator created a new identity for the row
    SUPERSEDED = "superseded"  # a later discovery no longer showed this case


_ADMIN_STATES = frozenset({ReviewState.MAPPED, ReviewState.CREATED})


@dataclass(frozen=True, slots=True)
class IdentityReview:
    """One discovered interface whose identity waits for an administrator.

    Attributes:
        id: UUID of the review.
        device_id: The device the row and every candidate belong to.
        opened_at: When reconciliation opened the review (UTC).
        outcome: ``PROBABLE`` (one candidate) or ``AMBIGUOUS``.
        if_index: ``ifIndex`` the agent reported for the row.
        if_name: ``ifName`` as reported; empty when not returned.
        if_descr: ``ifDescr`` as reported.
        if_alias: ``ifAlias`` as reported.
        candidates: Existing interfaces the row may be, in UUID order.
        evidence: Human-readable evidence, agreeing and conflicting.
        if_type: IANAifType number, if reported.
        mac: ``ifPhysAddress`` as lower-case colon hex, if any.
        if_speed: ``ifSpeed`` in bits per second, if reported.
        if_high_speed: ``ifHighSpeed`` in megabits per second, if reported.
        addresses: IP interface addresses reported for the row.
        state: Open, or how it was closed.
        closed_at: When it was closed (UTC); ``None`` while open.
        resolution: Interface the row was mapped to or created as.
        actor: Administrator who resolved it; ``None`` otherwise.
        version: Optimistic-concurrency version.
    """

    id: ReviewId
    device_id: DeviceId
    opened_at: datetime
    outcome: ReconcileOutcome
    if_index: int
    if_name: str
    if_descr: str
    if_alias: str
    candidates: tuple[InterfaceId, ...]
    evidence: tuple[str, ...] = ()
    if_type: int | None = None
    mac: str | None = None
    if_speed: int | None = None
    if_high_speed: int | None = None
    addresses: tuple[str, ...] = ()
    state: ReviewState = ReviewState.OPEN
    closed_at: datetime | None = None
    resolution: InterfaceId | None = None
    actor: str | None = None
    version: int = 1

    def __post_init__(self) -> None:
        """Validate the review.

        Args:
            None

        Returns:
            None

        Raises:
            ValidationError: If a field breaks its rule, or the closing
                fields do not match ``state``.
        """
        require_uuid(self.id, "id")
        require_uuid(self.device_id, "device_id")
        require_utc(self.opened_at, "opened_at")
        if self.outcome not in REVIEW_OUTCOMES:
            raise ValidationError("a review is PROBABLE or AMBIGUOUS", field="outcome")
        require_int(self.if_index, "if_index", minimum=1, maximum=2_147_483_647)
        for name in ("if_name", "if_descr", "if_alias"):
            require_text(getattr(self, name), name)
        _require_ids(self.candidates)
        if not isinstance(self.evidence, tuple):
            raise ValidationError("evidence must be a tuple of strings", field="evidence")
        for item in self.evidence:
            require_name(item, "evidence")
        require_int(self.if_type, "if_type", minimum=1, optional=True)
        require_mac(self.mac, "mac")
        require_int(self.if_speed, "if_speed", minimum=0, optional=True)
        require_int(self.if_high_speed, "if_high_speed", minimum=0, optional=True)
        require_interface_addresses(self.addresses, "addresses")
        require_int(self.version, "version", minimum=1)
        self._check_closing()

    @property
    def is_open(self) -> bool:
        """Whether the review still waits for a decision.

        Args:
            None

        Returns:
            bool: ``True`` while ``state`` is ``OPEN``.

        Raises:
            None
        """
        return self.state is ReviewState.OPEN

    def _check_closing(self) -> None:
        """Require the closing fields that ``state`` implies.

        Args:
            None

        Returns:
            None

        Raises:
            ValidationError: If closing fields are missing or present wrongly.
        """
        require_enum(self.state, ReviewState, "state")
        require_uuid(self.resolution, "resolution", optional=True)
        require_name(self.actor, "actor", optional=True)
        if self.state is ReviewState.OPEN:
            if (self.closed_at, self.resolution, self.actor) != (None, None, None):
                raise ValidationError("an open review has no closing fields", field="state")
            return
        if self.closed_at is None:
            raise ValidationError("a closed review needs closed_at", field="closed_at")
        require_utc(self.closed_at, "closed_at")
        require_order(self.opened_at, self.closed_at, "closed_at")
        admin = self.state in _ADMIN_STATES
        if admin != (self.resolution is not None) or admin != (self.actor is not None):
            raise ValidationError(
                "an administrator's decision names its interface and actor; nothing else does",
                field="resolution",
            )


def _require_ids(values: object) -> None:
    """Require a non-empty tuple of distinct UUIDs in ascending order.

    Args:
        values: Candidate interface IDs.

    Returns:
        None

    Raises:
        ValidationError: If the tuple is empty, unordered, or repeats an ID.
    """
    if not isinstance(values, tuple) or not values:
        raise ValidationError("candidates must be a non-empty tuple", field="candidates")
    for value in values:
        require_uuid(value, "candidates")
    if list(values) != sorted(set(values), key=str):
        raise ValidationError("candidates must be distinct and in UUID order", field="candidates")
