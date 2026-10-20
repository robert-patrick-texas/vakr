"""Build the records a reconciliation writes: observed fields, identities, and history.

Discovery refreshes only *observed* fields (``INTERFACE_OBSERVED_FIELDS``)
and never an operator's comment, tags, or annotation. A table that did not
answer keeps what vakr already knew (DISC-008): a row without ``ifAlias``
because ``ifXTable`` failed keeps the stored description, and incomplete
IP-MIB answers keep the stored addresses.

Lifecycle follows ``vakr.logic.lifecycle`` (E.6, E.17 #12). The M2 schema
stores only the state, so the record it needs is rebuilt from what is
stored: an interface without an ``ifIndex`` went missing
(``NOT_DISCOVERED``), and an inactive interface's stale clock starts at its
last change (``updated_at``); E.26 records this approximation.

Dependencies:
    vakr.discovery: Rows and reconciliation results.
    vakr.domain: Interfaces, history entries, reviews.
    vakr.logic.lifecycle: Transition rules.

Safety:
    Pure; the caller writes the results inside one unit of work.

Example:
    >>> from vakr.domain.enums import IfAdminStatus, IfOperStatus
    >>> row = DiscoveredInterface(3, admin_status=IfAdminStatus.UP, oper_status=IfOperStatus.UP)
    >>> first_lifecycle(row).value
    'active'
"""

from dataclasses import replace
from datetime import datetime, timedelta

from vakr.discovery.model import DiscoveredInterface
from vakr.domain.control import IdentityHistoryEntry
from vakr.domain.enums import IdentityDecisionSource, InterfaceLifecycle, ReconcileOutcome
from vakr.domain.ids import DeviceId, IdentityHistoryId, InterfaceId, TagId, new_id
from vakr.domain.interfaces import Interface
from vakr.domain.ownership import API_MANAGED
from vakr.logic.lifecycle import apply_evidence, tick
from vakr.logic.lifecycle_model import (
    InterfaceEvidence,
    LifecycleReason,
    LifecycleRecord,
    LifecycleSource,
)

MAX_EVIDENCE = 16
"""Evidence items kept per history entry or review (E.26); the rest are summarised."""


def bounded(evidence: tuple[str, ...]) -> tuple[str, ...]:
    """Keep at most ``MAX_EVIDENCE`` items, noting how many were dropped.

    Args:
        evidence: Evidence items.

    Returns:
        tuple[str, ...]: The first items and, if needed, a count of the rest.

    Raises:
        None
    """
    if len(evidence) <= MAX_EVIDENCE:
        return evidence
    kept = evidence[: MAX_EVIDENCE - 1]
    return (*kept, f"{len(evidence) - len(kept)} more evidence items not stored")


def sanitized(row: DiscoveredInterface) -> DiscoveredInterface:
    """Make a row safe to store: repeated addresses once, an impossible ifType unknown.

    Agents occasionally list one address twice or report ``ifType`` 0. The
    domain refuses both, and one odd row must not fail the whole device's
    discovery write, so such values are normalised here, never invented.

    Args:
        row: A discovered row.

    Returns:
        DiscoveredInterface: The row, cleaned.

    Raises:
        None
    """
    addresses = tuple(dict.fromkeys(row.addresses))
    if_type = row.if_type if row.if_type is None or row.if_type >= 1 else None
    if (addresses, if_type) == (row.addresses, row.if_type):
        return row
    return replace(row, addresses=addresses, if_type=if_type)


def _keep[T](new: T | None, old: T) -> T:
    """Prefer a newly reported value; ``None`` means the agent did not return it.

    Args:
        new: The reported value, or ``None``.
        old: The stored value.

    Returns:
        T: The value to store.

    Raises:
        None
    """
    return old if new is None else new


def _mac(row: DiscoveredInterface, old: str | None) -> str | None:
    """Return the MAC to store: an empty ``ifPhysAddress`` clears it, a missing one keeps it.

    Args:
        row: The row.
        old: The stored MAC.

    Returns:
        str | None: The MAC.

    Raises:
        None
    """
    return old if "ifPhysAddress" in row.missing else row.mac


def observed(
    stored: Interface, row: DiscoveredInterface, tags: frozenset[TagId] | None,
    addresses_complete: bool, now: datetime,
) -> Interface:  # fmt: skip
    """Refresh a stored interface's observed fields from a row.

    Args:
        stored: The matched identity.
        row: Its discovered row.
        tags: Description-derived tags, or ``None`` when ``ifAlias`` is unknown.
        addresses_complete: Whether IP-MIB answered completely.
        now: Discovery time (UTC).

    Returns:
        Interface: The record to write; equal to ``stored`` apart from
        ``updated_at`` when nothing changed.

    Raises:
        None
    """
    return replace(
        stored,
        if_index=row.if_index,
        if_name=_keep(row.if_name, stored.if_name),
        if_descr=_keep(row.if_descr, stored.if_descr),
        if_alias=_keep(row.if_alias, stored.if_alias),
        if_type=_keep(row.if_type, stored.if_type),
        mac=_mac(row, stored.mac),
        if_speed=_keep(row.if_speed, stored.if_speed),
        if_high_speed=_keep(row.if_high_speed, stored.if_high_speed),
        addresses=row.addresses if addresses_complete else stored.addresses,
        description_tags=stored.description_tags if tags is None else tags,
        updated_at=max(now, stored.updated_at),
    )


def first_lifecycle(row: DiscoveredInterface) -> InterfaceLifecycle:
    """Lifecycle of a new identity: ACTIVE only when discovery saw it up/up (E.6).

    Args:
        row: The discovered row.

    Returns:
        InterfaceLifecycle: ``ACTIVE`` or ``INACTIVE``.

    Raises:
        None
    """
    return InterfaceLifecycle.ACTIVE if row.up_up else InterfaceLifecycle.INACTIVE


def new_interface(
    device_id: DeviceId, row: DiscoveredInterface, tags: frozenset[TagId], now: datetime
) -> Interface:
    """Create a new, API-managed identity for a row (E.19: discovered interfaces start API-managed).

    Args:
        device_id: The device.
        row: The row.
        tags: Description-derived tags.
        now: Discovery time (UTC).

    Returns:
        Interface: The new interface, version 1.

    Raises:
        ValidationError: If the agent reported a value the domain rejects.
    """
    return Interface(
        id=new_id(InterfaceId), device_id=device_id, if_index=row.if_index,
        if_name=row.if_name or "", if_descr=row.if_descr or "", if_alias=row.if_alias or "",
        lifecycle=first_lifecycle(row), managed_by=API_MANAGED, created_at=now, updated_at=now,
        if_type=row.if_type, mac=row.mac, if_speed=row.if_speed, if_high_speed=row.if_high_speed,
        addresses=row.addresses, description_tags=tags,
    )  # fmt: skip


def current_record(stored: Interface) -> LifecycleRecord:
    """Rebuild the lifecycle record ``vakr.logic.lifecycle`` needs from a stored interface.

    Args:
        stored: The interface.

    Returns:
        LifecycleRecord: State, inferred reason, and stale-clock start.

    Raises:
        None
    """
    state = stored.lifecycle
    if state is InterfaceLifecycle.RETIRED:
        reason = LifecycleReason.RETIRED_BY_ADMIN
    elif stored.if_index is None:
        reason = LifecycleReason.NOT_DISCOVERED
    elif state is InterfaceLifecycle.ACTIVE:
        reason = LifecycleReason.UP_UP
    else:
        reason = LifecycleReason.OPER_NOT_UP
    inactive = state in (InterfaceLifecycle.INACTIVE, InterfaceLifecycle.STALE)
    return LifecycleRecord(
        state, reason, stored.created_at, stored.updated_at if inactive else None
    )


def next_lifecycle(
    stored: Interface, row: DiscoveredInterface | None, now: datetime, stale_after: timedelta
) -> InterfaceLifecycle:
    """Lifecycle after this discovery: present with its status, missing, or just older.

    Args:
        stored: The interface before this discovery.
        row: Its row, or ``None`` when it was not discovered.
        now: Discovery time (UTC).
        stale_after: ``lifecycle.stale_after`` (LIFE-003).

    Returns:
        InterfaceLifecycle: The new state; unchanged when the evidence proves nothing.

    Raises:
        None
    """
    record = current_record(stored)
    if row is None and stored.if_index is None:
        return tick(record, now, stale_after).record.state
    admin, oper = (None, None) if row is None else (row.admin_status, row.oper_status)
    evidence = InterfaceEvidence(now, LifecycleSource.DISCOVERY, row is not None, admin, oper)
    return apply_evidence(record, evidence, stale_after).record.state


def history(
    interface: Interface, outcome: ReconcileOutcome, now: datetime,
    previous: int | None, observed_index: int | None, evidence: tuple[str, ...],
) -> IdentityHistoryEntry:  # fmt: skip
    """Record one automatic identity decision (IFID-005).

    Args:
        interface: The identity the decision concerns.
        outcome: The outcome.
        now: Decision time (UTC).
        previous: ``ifIndex`` the identity had before.
        observed_index: ``ifIndex`` observed now.
        evidence: Why.

    Returns:
        IdentityHistoryEntry: The entry; unscored, because MVP rules do not weigh.

    Raises:
        None
    """
    return IdentityHistoryEntry(
        id=new_id(IdentityHistoryId), interface_id=interface.id, device_id=interface.device_id,
        decided_at=now, outcome=outcome, source=IdentityDecisionSource.AUTOMATIC,
        previous_if_index=previous, observed_if_index=observed_index, evidence=bounded(evidence),
    )  # fmt: skip
