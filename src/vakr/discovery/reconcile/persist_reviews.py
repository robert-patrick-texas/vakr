"""Persist identity reviews: keep the same case open, supersede stale ones, open new ones.

A rediscovery that shows the same unresolved case (same ifIndex, names,
candidates, and outcome) keeps the existing review, so an administrator's
queue does not churn daily. A case that no longer appears is superseded,
except when an incomplete ``ifTable`` walk did not see its row and none of
its candidates was matched elsewhere (DISC-008). Opening a review notes it
in each candidate's identity history (IFID-005).

Dependencies:
    vakr.discovery.reconcile.records: Review and history records.

Safety:
    Writes only inside the caller's unit of work.

Example:
    >>> write_reviews(uow, write, rec, stored, None)  # doctest: +SKIP
"""

from collections import Counter
from datetime import datetime

from vakr.discovery.model import DiscoveredInterface
from vakr.discovery.reconcile.evidence import normalize
from vakr.discovery.reconcile.model import DiscoveryWrite, Reconciliation, Unresolved
from vakr.discovery.reconcile.records import bounded, history
from vakr.domain.enums import ReconcileOutcome
from vakr.domain.ids import DeviceId, InterfaceId, ReviewId, new_id
from vakr.domain.interfaces import Interface
from vakr.domain.reviews import IdentityReview, ReviewState
from vakr.inventory.reads import open_reviews
from vakr.ports.metadata import MetadataUnitOfWork

type CaseKey = tuple[int, str, str, tuple[InterfaceId, ...], ReconcileOutcome]


def _case_key(row: DiscoveredInterface | IdentityReview, candidates: tuple[InterfaceId, ...],
              outcome: ReconcileOutcome) -> CaseKey:  # fmt: skip
    """Key that says whether an open review shows the same case as a new decision.

    Args:
        row: The discovered row or the review holding it.
        candidates: The candidates.
        outcome: ``PROBABLE`` or ``AMBIGUOUS``.

    Returns:
        CaseKey: ifIndex, normalised names, candidates, and outcome.

    Raises:
        None
    """
    return (row.if_index, normalize(row.if_name), normalize(row.if_descr), candidates, outcome)


def write_reviews(
    uow: MetadataUnitOfWork, write: DiscoveryWrite, rec: Reconciliation,
    stored: dict[InterfaceId, Interface], seen: frozenset[int] | None,
) -> Counter[str]:  # fmt: skip
    """Keep reviews that still apply, supersede the rest, and open new ones.

    Args:
        uow: The open unit of work.
        write: Device, time, keywords, and stale threshold.
        rec: The decisions.
        stored: Stored interfaces by ID, before this discovery.
        seen: ``None`` after a complete walk, else the ifIndexes the walk saw.

    Returns:
        Counter[str]: ``review_opened`` and ``review_superseded`` counts.

    Raises:
        DuplicateError, ConflictError, StorageError: From the repositories.
    """
    wanted = {_case_key(u.row, u.candidates, u.outcome): u for u in rec.unresolved}
    taken = {m.interface.id for m in rec.matches if m.interface is not None}
    kept: set[CaseKey] = set()
    changes: Counter[str] = Counter()
    for old in open_reviews(uow, write.device_id):
        key = _case_key(old, old.candidates, old.outcome)
        if key in wanted and key not in kept:
            kept.add(key)
            continue
        unseen = seen is not None and old.if_index not in seen
        if unseen and not taken & set(old.candidates):
            continue  # an incomplete walk missed its row: keep waiting (DISC-008)
        uow.reviews.close(old.id, ReviewState.SUPERSEDED, expected_version=old.version,
                          at=max(write.now, old.opened_at))  # fmt: skip
        changes["review_superseded"] += 1
    for key, case in wanted.items():
        if key not in kept:
            _open_review(uow, write, case, stored)
            changes[f"review_opened_{case.outcome.value}"] += 1
    return changes


def _open_review(
    uow: MetadataUnitOfWork, write: DiscoveryWrite, case: Unresolved,
    stored: dict[InterfaceId, Interface],
) -> None:  # fmt: skip
    """Open a review and note it in each candidate's identity history.

    Args:
        uow: The open unit of work.
        write: Device, time, keywords, and stale threshold.
        case: The unresolved row.
        stored: Stored interfaces by ID.

    Returns:
        None

    Raises:
        DuplicateError, StorageError: From the repositories.
    """
    opened = uow.reviews.add(review_record(write.device_id, case, write.now))
    why = (f"held for review {opened.id}; receives no observations until resolved", *case.evidence)
    for candidate in case.candidates:
        before = stored[candidate]
        entry = history(before, case.outcome, write.now, before.if_index, case.row.if_index, why)
        uow.interfaces.append_identity_history(entry)


def review_record(device_id: DeviceId, case: Unresolved, now: datetime) -> IdentityReview:
    """Open a review for an unresolved row (IFID-004, IFID-007).

    Args:
        device_id: The device.
        case: The unresolved row and its candidates.
        now: Discovery time (UTC).

    Returns:
        IdentityReview: An open review holding the observed row.

    Raises:
        None
    """
    row = case.row
    return IdentityReview(
        id=new_id(ReviewId), device_id=device_id, opened_at=now, outcome=case.outcome,
        if_index=row.if_index, if_name=row.if_name or "", if_descr=row.if_descr or "",
        if_alias=row.if_alias or "", candidates=case.candidates, evidence=bounded(case.evidence),
        if_type=row.if_type, mac=row.mac, if_speed=row.if_speed, if_high_speed=row.if_high_speed,
        addresses=row.addresses,
    )  # fmt: skip
