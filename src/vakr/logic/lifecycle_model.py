"""Interface lifecycle vocabulary, records, and default polling (E.6, LIFE-001, LIFE-004).

``vakr.logic.lifecycle`` holds the transition rules; this module holds what
they read and return: the reasons, the sources a transition can come from
(LIFE-006), the per-interface record, the evidence from Stage 1 and
discovery, and the default collection each state receives. ACTIVE and
INACTIVE interfaces get Stage 1 status checks every poll; only ACTIVE ones
get Stage 2 counters (SNMP-006). STALE ones drop to the discovery cadence
(LIFE-004). RETIRED ones leave default polling, and every state keeps its
history and stays visible to discovery so identities still reconcile.

Dependencies:
    dataclasses, datetime, enum: Immutable records and aware times.
    vakr.domain.enums: ``InterfaceLifecycle`` and IF-MIB status values.

Safety:
    Pure data. Nothing here deletes history.

Example:
    >>> polling_eligibility(InterfaceLifecycle.STALE)
    PollingEligibility(status_checks=<StatusCadence.DISCOVERY: 'discovery'>, counters=False)
"""

from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum

from vakr.domain.enums import IfAdminStatus, IfOperStatus, InterfaceLifecycle

STALE_AFTER = timedelta(days=10)  # E.6: lifecycle.stale_after = "10d" (LIFE-003)


class LifecycleReason(StrEnum):
    """Why an interface is in its current lifecycle state."""

    UP_UP = "up_up"
    RECONCILED = "reconciled"  # rediscovery matched an existing identity
    ADMIN_DOWN = "admin_down"
    OPER_NOT_UP = "oper_not_up"  # down, lowerLayerDown, notPresent, dormant, testing
    NOT_DISCOVERED = "not_discovered"
    STALE_AFTER = "stale_after"  # transition reason only; the record keeps the cause
    RETIRED_BY_ADMIN = "retired_by_admin"
    RESTORED_BY_ADMIN = "restored_by_admin"


class LifecycleSource(StrEnum):
    """What produced a transition (LIFE-006)."""

    STAGE1 = "stage1"
    DISCOVERY = "discovery"
    TIMER = "timer"
    ADMIN = "admin"


class StatusCadence(StrEnum):
    """How often Stage 1 checks an interface's admin and oper status (LIFE-004)."""

    EVERY_POLL = "every_poll"
    DISCOVERY = "discovery"
    NONE = "none"


@dataclass(frozen=True, slots=True)
class LifecycleRecord:
    """Current lifecycle of one interface.

    Attributes:
        state: Current state.
        reason: Why it is in that state; for STALE, why it became inactive.
        since: When the current state began.
        inactive_since: Start of the unbroken INACTIVE/STALE period, if any.
    """

    state: InterfaceLifecycle
    reason: LifecycleReason
    since: datetime
    inactive_since: datetime | None = None


@dataclass(frozen=True, slots=True)
class InterfaceEvidence:
    """One Stage 1 or discovery result for an interface.

    Attributes:
        observed_at: When it was observed.
        source: ``STAGE1`` or ``DISCOVERY``.
        present: False when the latest discovery did not find it.
        admin: ``ifAdminStatus``; None if not returned.
        oper: ``ifOperStatus``; None if not returned.
    """

    observed_at: datetime
    source: LifecycleSource
    present: bool = True
    admin: IfAdminStatus | None = None
    oper: IfOperStatus | None = None


@dataclass(frozen=True, slots=True)
class LifecycleTransition:
    """One recorded lifecycle change (LIFE-006).

    Attributes:
        prior: State before.
        new: State after.
        reason: Why it changed.
        effective_at: When the change took effect.
        source: What produced it.
        actor: Administrator for admin actions; None otherwise.
    """

    prior: InterfaceLifecycle
    new: InterfaceLifecycle
    reason: LifecycleReason
    effective_at: datetime
    source: LifecycleSource
    actor: str | None = None


@dataclass(frozen=True, slots=True)
class LifecycleUpdate:
    """The record after an update and the transitions to store, oldest first.

    Attributes:
        record: The updated record.
        transitions: Zero or more transitions.
    """

    record: LifecycleRecord
    transitions: tuple[LifecycleTransition, ...] = ()


@dataclass(frozen=True, slots=True)
class PollingEligibility:
    """What collection a lifecycle state receives by default (LIFE-004, SNMP-006).

    Attributes:
        status_checks: Stage 1 cadence.
        counters: Whether Stage 2 counters are collected.
    """

    status_checks: StatusCadence
    counters: bool


_POLLING = {
    InterfaceLifecycle.ACTIVE: PollingEligibility(StatusCadence.EVERY_POLL, counters=True),
    InterfaceLifecycle.INACTIVE: PollingEligibility(StatusCadence.EVERY_POLL, counters=False),
    InterfaceLifecycle.STALE: PollingEligibility(StatusCadence.DISCOVERY, counters=False),
    InterfaceLifecycle.RETIRED: PollingEligibility(StatusCadence.NONE, counters=False),
}


def polling_eligibility(state: InterfaceLifecycle) -> PollingEligibility:
    """Return the default collection for a state; every state stays discoverable.

    Args:
        state: Lifecycle state.

    Returns:
        PollingEligibility: Stage 1 cadence and Stage 2 eligibility.

    Raises:
        None
    """
    return _POLLING[state]
