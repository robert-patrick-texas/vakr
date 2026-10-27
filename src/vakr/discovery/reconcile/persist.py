"""Write one device's reconciled discovery through a unit of work (IFID-001..006, DISC-007).

For each decision of ``reconcile``:

- ``EXACT`` and ``CONFIDENT_RENUMBER`` refresh the identity's observed
  fields and lifecycle; the UUID, and so its history, is kept;
- ``NEW`` creates an API-managed identity (E.19);
- ``PROBABLE`` and ``AMBIGUOUS`` open an identity review, unless the same
  case is already open; the candidates are left exactly as they were;
- ``MISSING`` detaches the old ``ifIndex`` and makes the identity inactive
  (E.6 ``NOT_DISCOVERED``), never deleting it.

Every interface the agent reported is stored, whether or not policy will
collect it (DISC-007), with its description-keyword tags (META-012).
History is written for every change of identity state: creation, renumber,
return, missing, a case opened for review, and an exact match whose
descriptive evidence changed. An unchanged exact match is not re-recorded
on every rediscovery, which keeps history bounded (E.26). An open review
whose case no longer appears is superseded, except after an incomplete
``ifTable`` walk that did not see its row (DISC-008).

Dependencies:
    vakr.discovery.reconcile.match, records: Decisions and records.
    vakr.inventory.catalog, reads: Tags and bounded lists.
    vakr.logic.lifecycle_model: The lifecycle record a change stores.
    vakr.policy.classify: Description keyword tags.

Safety:
    Writes only inside the caller's unit of work; the caller commits.

Example:
    >>> persist_discovery(uow, DiscoveryWrite(device.id, now, keywords), result)  # doctest: +SKIP
"""

from collections import Counter
from dataclasses import dataclass, replace
from datetime import datetime

from vakr.discovery.model import DiscoveryResult
from vakr.discovery.reconcile.match import reconcile
from vakr.discovery.reconcile.model import DiscoveryWrite, Match, Reconciliation
from vakr.discovery.reconcile.persist_reviews import write_reviews
from vakr.discovery.reconcile.records import (
    history,
    new_interface,
    next_lifecycle,
    observed,
    sanitized,
)
from vakr.domain.enums import ReconcileOutcome
from vakr.domain.ids import TagId
from vakr.domain.interfaces import Interface
from vakr.domain.ownership import API_MANAGED
from vakr.inventory.catalog import ensure_tags
from vakr.inventory.reads import device_interfaces
from vakr.logic.lifecycle_model import LifecycleRecord
from vakr.policy.classify import classify
from vakr.policy.keywords import KeywordSet
from vakr.ports.metadata import MetadataUnitOfWork

KEYWORD_TAG = "Added from an interface description keyword (META-012)."


def inventory_complete(result: DiscoveryResult) -> bool:
    """Whether ``ifTable`` was read completely, so an absent interface is really missing.

    Args:
        result: The discovery result.

    Returns:
        bool: ``True`` when the agent answered and ``ifTable`` is usable.

    Raises:
        None
    """
    table = result.subtable("ifTable")
    return result.answered and table is not None and table.status.usable


@dataclass(frozen=True, slots=True)
class Persisted:
    """What a discovery write decided and what it changed.

    Attributes:
        reconciliation: Every decision.
        changes: Count per change, such as ``new`` or ``review_opened``; empty
            when the rediscovery changed no identity (E.26: not audited).
    """

    reconciliation: Reconciliation
    changes: tuple[tuple[str, int], ...]


def persist_discovery(
    uow: MetadataUnitOfWork, write: DiscoveryWrite, result: DiscoveryResult
) -> Persisted:
    """Reconcile a discovery with stored identities and write the outcome.

    Args:
        uow: The open unit of work; the caller commits.
        write: Device, time, keywords, and stale threshold.
        result: The discovery result.

    Returns:
        Persisted: What was decided and what changed.

    Raises:
        NotFoundError: If the device does not exist.
        DuplicateError, ConflictError: If another unit of work wrote first.
        StorageError: If the store fails or a list exceeds its bound.
    """
    stored = device_interfaces(uow, write.device_id)
    complete = inventory_complete(result)
    rows = tuple(sanitized(r) for r in result.interfaces)
    rec = reconcile(stored, rows, inventory_complete=complete,
                    addresses_complete=result.addresses_complete)  # fmt: skip
    changes: Counter[str] = Counter()
    for match in rec.matches:
        if _write_match(uow, write, match, result.addresses_complete):
            changes[match.outcome.value] += 1
    seen = frozenset(row.if_index for row in rows)
    changes.update(write_reviews(uow, write, rec, {i.id: i for i in stored},
                                  None if complete else seen))  # fmt: skip
    for interface in rec.missing:
        if _write_missing(uow, write, interface):
            changes[ReconcileOutcome.MISSING.value] += 1
    return Persisted(rec, tuple(sorted((k, n) for k, n in changes.items() if n)))


def _keyword_tags(
    uow: MetadataUnitOfWork, alias: str | None, keywords: KeywordSet
) -> frozenset[TagId] | None:
    """Resolve a description's keyword tags, creating missing tag records.

    Args:
        uow: The open unit of work.
        alias: ``ifAlias``, or ``None`` when the agent did not return it.
        keywords: Effective keywords.

    Returns:
        frozenset[TagId] | None: The tags, or ``None`` when the description is unknown.

    Raises:
        DuplicateError: If another unit of work created a tag first.
    """
    if alias is None:
        return None
    return ensure_tags(uow, classify(alias, keywords).tags, API_MANAGED, KEYWORD_TAG)


def _write_match(
    uow: MetadataUnitOfWork, write: DiscoveryWrite, match: Match, addresses_complete: bool
) -> bool:
    """Store one automatic match or new identity, its lifecycle, and its history.

    Args:
        uow: The open unit of work.
        write: Device, time, keywords, and stale threshold.
        match: The decision.
        addresses_complete: Whether IP-MIB answered completely.

    Returns:
        bool: Whether the identity's state changed (a new, moved, or returned identity).

    Raises:
        DuplicateError, ConflictError, StorageError: From the repositories.
    """
    row, now = match.row, write.now
    tags = _keyword_tags(uow, row.if_alias, write.keywords)
    before = match.interface
    if before is None:
        created = uow.interfaces.add(new_interface(write.device_id, row, tags or frozenset(), now))
        entry = history(created, ReconcileOutcome.NEW, now, None, row.if_index, match.evidence)
        uow.interfaces.append_identity_history(entry)
        return True
    current = before
    refreshed = observed(before, row, tags, addresses_complete, now)
    if replace(refreshed, updated_at=before.updated_at) != before:
        current = uow.interfaces.update_observed(refreshed)
    _set_lifecycle(uow, current, next_lifecycle(before, row, now, write.stale_after), now)
    if match.outcome is ReconcileOutcome.CONFIDENT_RENUMBER or len(match.evidence) > 1:
        entry = history(before, match.outcome, now, before.if_index, row.if_index, match.evidence)
        uow.interfaces.append_identity_history(entry)
    return match.outcome is ReconcileOutcome.CONFIDENT_RENUMBER


def _set_lifecycle(
    uow: MetadataUnitOfWork, current: Interface, record: LifecycleRecord, now: datetime
) -> Interface:
    """Change lifecycle when it differs, with the stale-clock start the rules computed.

    Args:
        uow: The open unit of work.
        current: The interface as stored now.
        record: The lifecycle it should have (``next_lifecycle``).
        now: Change time (UTC).

    Returns:
        Interface: The interface after the change, if any.

    Raises:
        ConflictError, StorageError: From the repository.
    """
    if record.state is current.lifecycle:
        return current
    at = max(now, current.updated_at)
    return uow.interfaces.set_lifecycle(current.id, record.state, expected_version=current.version,
                                        at=at, inactive_since=record.inactive_since)  # fmt: skip


def _write_missing(uow: MetadataUnitOfWork, write: DiscoveryWrite, before: Interface) -> bool:
    """Detach a missing identity's ifIndex and make it inactive; keep everything else.

    Args:
        uow: The open unit of work.
        write: Device, time, keywords, and stale threshold.
        before: The stored identity not seen in a complete ``ifTable``.

    Returns:
        bool: Whether it went missing now, rather than being missing already.

    Raises:
        ConflictError, StorageError: From the repository.
    """
    record = next_lifecycle(before, None, write.now, write.stale_after)
    current = before
    if before.if_index is not None:
        detached = replace(before, if_index=None, updated_at=max(write.now, before.updated_at))
        current = uow.interfaces.update_observed(detached)
        why = (f"not in the latest complete ifTable; ifIndex {before.if_index} released",)
        entry = history(before, ReconcileOutcome.MISSING, write.now, before.if_index, None, why)
        uow.interfaces.append_identity_history(entry)
    _set_lifecycle(uow, current, record, write.now)
    return before.if_index is not None
