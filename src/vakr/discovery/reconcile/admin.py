"""Administrator decisions on identity reviews (IFID-007, IFID-T07, SEC-018).

An open review says "this discovered row may be one of these interfaces".
An administrator answers it in one of two ways:

- **map**: the row *is* an existing interface. The interface takes the
  row's ``ifIndex`` and names and keeps its UUID, so its history continues;
- **new**: the row is a different interface. A new identity is created.

Either way any other interface still holding the row's ``ifIndex`` lets
it go (it is then missing, E.6), the review closes, identity history
records an ``ADMIN`` decision with the administrator's name, and an audit
event records the action. The next rediscovery then matches exactly.

Dependencies:
    vakr.discovery.reconcile.records: History entries.
    vakr.domain, vakr.ports.metadata: Records and the unit of work.

Safety:
    One unit of work per decision; a refused decision writes nothing.

Example:
    >>> map_review(factory, review_id, interface_id, "cli:netops", clock)  # doctest: +SKIP
"""

from dataclasses import replace
from datetime import datetime

from vakr.domain.control import AuditEvent, IdentityHistoryEntry
from vakr.domain.enums import (
    AuditOutcome,
    IdentityDecisionSource,
    InterfaceLifecycle,
    ReconcileOutcome,
)
from vakr.domain.ids import AuditEventId, IdentityHistoryId, InterfaceId, ReviewId, new_id
from vakr.domain.interfaces import Interface
from vakr.domain.ownership import API_MANAGED
from vakr.domain.reviews import IdentityReview, ReviewState
from vakr.inventory.reads import device_interfaces
from vakr.ports.clock import Clock
from vakr.ports.metadata import MetadataUnitOfWork, MetadataUnitOfWorkFactory


class ReviewError(ValueError):
    """The decision cannot be made; the message says why."""


def _admin_entry(
    interface: Interface, review: IdentityReview, outcome: ReconcileOutcome, now: datetime,
    actor: str, why: str,
) -> IdentityHistoryEntry:  # fmt: skip
    """Record an administrator's identity decision (IFID-005, IFID-007).

    Args:
        interface: The identity concerned.
        review: The review decided.
        outcome: The outcome recorded.
        now: Decision time (UTC).
        actor: The administrator.
        why: What was decided.

    Returns:
        IdentityHistoryEntry: The entry.

    Raises:
        None
    """
    return IdentityHistoryEntry(
        id=new_id(IdentityHistoryId), interface_id=interface.id, device_id=interface.device_id,
        decided_at=now, outcome=outcome, source=IdentityDecisionSource.ADMIN,
        previous_if_index=interface.if_index, observed_if_index=review.if_index,
        evidence=(why, f"review {review.id} ({review.outcome.value})"), actor=actor,
    )  # fmt: skip


def _open(uow: MetadataUnitOfWork, review_id: ReviewId) -> IdentityReview:
    """Return an open review.

    Args:
        uow: The open unit of work.
        review_id: The review.

    Returns:
        IdentityReview: The review.

    Raises:
        ReviewError: If it does not exist or is closed.
    """
    review = uow.reviews.get(review_id)
    if review is None:
        raise ReviewError(f"no identity review {review_id}")
    if not review.is_open:
        raise ReviewError(f"review {review_id} is already {review.state.value}")
    return review


def _release_index(
    uow: MetadataUnitOfWork, review: IdentityReview, keep: InterfaceId | None, now: datetime,
    actor: str,
) -> None:  # fmt: skip
    """Detach the review's ifIndex from every other interface that still holds it.

    Args:
        uow: The open unit of work.
        review: The review.
        keep: The interface that takes the index, if any.
        now: Decision time (UTC).
        actor: The administrator.

    Returns:
        None

    Raises:
        DomainError: From the repositories.
    """
    for other in device_interfaces(uow, review.device_id):
        if other.if_index != review.if_index or other.id == keep:
            continue
        why = f"ifIndex {review.if_index} given to another identity by an administrator"
        entry = _admin_entry(other, review, ReconcileOutcome.MISSING, now, actor, why)
        uow.interfaces.append_identity_history(entry)
        detached = uow.interfaces.update_observed(
            replace(other, if_index=None, updated_at=max(now, other.updated_at))
        )
        if detached.lifecycle is InterfaceLifecycle.ACTIVE:
            # Its stale clock starts at the decision that took its ifIndex (E.6 NOT_DISCOVERED).
            uow.interfaces.set_lifecycle(detached.id, InterfaceLifecycle.INACTIVE,
                                         expected_version=detached.version,
                                         at=detached.updated_at, inactive_since=now)  # fmt: skip


def _close(uow: MetadataUnitOfWork, review: IdentityReview, state: ReviewState,
           interface: Interface, now: datetime, actor: str) -> None:  # fmt: skip
    """Close the review and audit the decision.

    Args:
        uow: The open unit of work.
        review: The review.
        state: ``MAPPED`` or ``CREATED``.
        interface: The interface the row became.
        now: Decision time (UTC).
        actor: The administrator.

    Returns:
        None

    Raises:
        DomainError: From the repositories.
    """
    at = max(now, review.opened_at)
    uow.reviews.close(review.id, state, expected_version=review.version, at=at,
                      resolution=interface.id, actor=actor)  # fmt: skip
    summary = (f"review {review.id}: ifIndex {review.if_index} {review.if_name!r} -> "
               f"interface {interface.id} ({state.value})")  # fmt: skip
    action = f"interface.review.{state.value}"
    uow.audit.append(AuditEvent(new_id(AuditEventId), now, actor, action, "interface",
                                interface.id, AuditOutcome.SUCCESS, summary))  # fmt: skip


def map_review(
    factory: MetadataUnitOfWorkFactory, review_id: ReviewId, interface_id: InterfaceId,
    actor: str, clock: Clock,
) -> Interface:  # fmt: skip
    """Decide that a review's row is an existing interface; its history continues.

    Args:
        factory: Opens units of work.
        review_id: The open review.
        interface_id: The interface the row is; it must be on the review's device.
        actor: The administrator (SEC-018).
        clock: Time source.

    Returns:
        Interface: The interface, now at the row's ifIndex.

    Raises:
        ReviewError: If the review or interface does not allow the decision.
        DomainError: If storage refuses the write; nothing is written.
    """
    with factory() as uow:
        review = _open(uow, review_id)
        target = uow.interfaces.get(interface_id)
        if target is None or target.device_id != review.device_id:
            raise ReviewError(f"interface {interface_id} is not on the review's device")
        now = max(clock.now_utc(), target.updated_at)
        _release_index(uow, review, target.id, now, actor)
        why = "an administrator confirmed this identity"
        uow.interfaces.append_identity_history(
            _admin_entry(target, review, review.outcome, now, actor, why)
        )
        moved = uow.interfaces.update_observed(replace(
            target, if_index=review.if_index, if_name=review.if_name or target.if_name,
            if_descr=review.if_descr or target.if_descr, if_alias=review.if_alias,
            if_type=review.if_type, mac=review.mac, if_speed=review.if_speed,
            if_high_speed=review.if_high_speed, addresses=review.addresses, updated_at=now,
        ))  # fmt: skip
        if moved.lifecycle in (InterfaceLifecycle.INACTIVE, InterfaceLifecycle.STALE):
            active = InterfaceLifecycle.ACTIVE
            moved = uow.interfaces.set_lifecycle(moved.id, active, expected_version=moved.version,
                                                 at=now, inactive_since=None)  # fmt: skip
        _close(uow, review, ReviewState.MAPPED, moved, now, actor)
        uow.commit()
    return moved


def create_identity(
    factory: MetadataUnitOfWorkFactory, review_id: ReviewId, actor: str, clock: Clock
) -> Interface:
    """Decide that a review's row is a new interface with its own identity.

    Args:
        factory: Opens units of work.
        review_id: The open review.
        actor: The administrator (SEC-018).
        clock: Time source.

    Returns:
        Interface: The new identity, ``INACTIVE`` until Stage 1 or discovery
        sees it up (E.6, E.17 #12).

    Raises:
        ReviewError: If the review is not open.
        DomainError: If storage refuses the write; nothing is written.
    """
    with factory() as uow:
        review = _open(uow, review_id)
        now = max(clock.now_utc(), review.opened_at)
        _release_index(uow, review, None, now, actor)
        created = uow.interfaces.add(Interface(
            id=new_id(InterfaceId), device_id=review.device_id, if_index=review.if_index,
            if_name=review.if_name, if_descr=review.if_descr, if_alias=review.if_alias,
            lifecycle=InterfaceLifecycle.INACTIVE, managed_by=API_MANAGED, created_at=now,
            updated_at=now, if_type=review.if_type, mac=review.mac, if_speed=review.if_speed,
            if_high_speed=review.if_high_speed, addresses=review.addresses, inactive_since=now,
        ))  # fmt: skip
        why = "an administrator created a new identity for this row"
        entry = _admin_entry(created, review, ReconcileOutcome.NEW, now, actor, why)
        uow.interfaces.append_identity_history(replace(entry, previous_if_index=None))
        _close(uow, review, ReviewState.CREATED, created, now, actor)
        uow.commit()
    return created
