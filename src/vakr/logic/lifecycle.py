"""Interface lifecycle transitions (Appendix E.6, LIFE-001, LIFE-003, LIFE-004, LIFE-006).

An interface is ACTIVE while it is discovered and up/up, INACTIVE when it is
present but not up/up or missing from discovery, STALE after ten days of
unbroken inactivity, and RETIRED only by an administrator (LIFE-001). Every
state keeps its history (LIFE-002). The point of STALE is cost: a port that
has been unplugged for weeks drops from minute-level status checks to the
discovery cadence (LIFE-004) without losing its identity or graphs.

Each change is returned as a ``LifecycleTransition`` with prior state, new
state, reason, effective time, source, and actor (LIFE-006). The stale
transition takes effect when the threshold was crossed, not when the timer
happened to run, so replays give identical history.

Rules beyond the E.6 table, chosen to keep evidence honest:

- ``ifOperStatus`` ``unknown`` and missing status are not evidence; they
  change nothing. ``testing`` counts as not up/up.
- Rediscovery without status returns an interface to ACTIVE only when it was
  inactive because it was missing (``NOT_DISCOVERED``); known not-up/up
  status keeps it inactive and its stale clock running.
- Evidence older than the current state is ignored, so late results cannot
  rewind history. RETIRED changes only through ``restore``.

Dependencies:
    dataclasses, datetime: Record updates; aware times.
    vakr.logic.lifecycle_model: Reasons, sources, records, and evidence.
    vakr.logic.freshness: ``require_aware``.
    vakr.domain.enums: Lifecycle and IF-MIB status vocabularies.

Safety:
    Pure: callers pass every time. No transition deletes or rewrites history.

Example:
    >>> from datetime import UTC, datetime, timedelta
    >>> t0 = datetime(2026, 1, 1, tzinfo=UTC)
    >>> rec = LifecycleRecord(InterfaceLifecycle.ACTIVE, LifecycleReason.UP_UP, t0)
    >>> down = (IfAdminStatus.UP, IfOperStatus.DOWN)
    >>> ev = InterfaceEvidence(t0, LifecycleSource.STAGE1, True, *down)
    >>> u = apply_evidence(rec, ev)
    >>> u.record.state.name, [t.reason.name for t in u.transitions]
    ('INACTIVE', ['OPER_NOT_UP'])
"""

from dataclasses import replace
from datetime import datetime, timedelta

from vakr.domain.enums import IfAdminStatus, IfOperStatus, InterfaceLifecycle
from vakr.logic.freshness import require_aware
from vakr.logic.lifecycle_model import (
    STALE_AFTER,
    InterfaceEvidence,
    LifecycleReason,
    LifecycleRecord,
    LifecycleSource,
    LifecycleTransition,
    LifecycleUpdate,
)

_NOT_UP = frozenset(set(IfOperStatus) - {IfOperStatus.UP, IfOperStatus.UNKNOWN})


def _move(
    rec: LifecycleRecord,
    new: InterfaceLifecycle,
    reason: LifecycleReason,
    at: datetime,
    source: LifecycleSource,
    actor: str | None = None,
) -> LifecycleUpdate:
    """Make one transition, tracking the start of unbroken inactivity.

    A STALE record keeps the cause of its inactivity as its reason, so a
    missing interface can still return when rediscovered; the transition
    itself records ``STALE_AFTER``.

    Args:
        rec: Current record.
        new: New state.
        reason: Why.
        at: Effective time.
        source: What produced it.
        actor: Administrator, for admin actions.

    Returns:
        LifecycleUpdate: The new record and its single transition.

    Raises:
        None
    """
    stale = new is InterfaceLifecycle.STALE
    inactive_from = (rec.inactive_since or at) if stale else at
    inactive = stale or new is InterfaceLifecycle.INACTIVE
    record = LifecycleRecord(
        new, rec.reason if stale else reason, at, inactive_from if inactive else None
    )
    return LifecycleUpdate(
        record, (LifecycleTransition(rec.state, new, reason, at, source, actor),)
    )


def tick(
    rec: LifecycleRecord, now: datetime, stale_after: timedelta = STALE_AFTER
) -> LifecycleUpdate:
    """Move an interface to STALE once it has been INACTIVE for ``stale_after``.

    Args:
        rec: Current record.
        now: The current time.
        stale_after: ``lifecycle.stale_after`` (LIFE-003).

    Returns:
        LifecycleUpdate: Unchanged, or STALE effective when the threshold
        was crossed.

    Raises:
        ValueError: If ``now`` is naive or ``stale_after`` is not positive.
    """
    require_aware(now, "now")
    if stale_after <= timedelta(0):
        raise ValueError("stale_after must be positive")
    start = rec.inactive_since
    if rec.state is not InterfaceLifecycle.INACTIVE or start is None or now - start < stale_after:
        return LifecycleUpdate(rec)
    return _move(
        rec,
        InterfaceLifecycle.STALE,
        LifecycleReason.STALE_AFTER,
        start + stale_after,
        LifecycleSource.TIMER,
    )


def _target(
    rec: LifecycleRecord, ev: InterfaceEvidence
) -> tuple[InterfaceLifecycle, LifecycleReason] | None:
    """Read the state the evidence points to, or None when it proves nothing.

    Args:
        rec: Current record.
        ev: The evidence.

    Returns:
        tuple | None: Target state and reason.

    Raises:
        None
    """
    if not ev.present:
        return InterfaceLifecycle.INACTIVE, LifecycleReason.NOT_DISCOVERED
    if ev.admin is not None and ev.admin is not IfAdminStatus.UP:
        return InterfaceLifecycle.INACTIVE, LifecycleReason.ADMIN_DOWN
    if ev.oper in _NOT_UP:
        return InterfaceLifecycle.INACTIVE, LifecycleReason.OPER_NOT_UP
    discovered = ev.source is LifecycleSource.DISCOVERY
    if ev.admin is IfAdminStatus.UP and ev.oper is IfOperStatus.UP:
        return InterfaceLifecycle.ACTIVE, (
            LifecycleReason.RECONCILED if discovered else LifecycleReason.UP_UP
        )
    if discovered and rec.reason is LifecycleReason.NOT_DISCOVERED:
        return InterfaceLifecycle.ACTIVE, LifecycleReason.RECONCILED
    return None


def apply_evidence(
    rec: LifecycleRecord, ev: InterfaceEvidence, stale_after: timedelta = STALE_AFTER
) -> LifecycleUpdate:
    """Apply one Stage 1 or discovery result, first catching up the stale timer.

    Args:
        rec: Current record.
        ev: The evidence.
        stale_after: ``lifecycle.stale_after`` (LIFE-003).

    Returns:
        LifecycleUpdate: The new record and up to two transitions (STALE
        effective earlier, then the evidence's own transition).

    Raises:
        ValueError: If a datetime is naive or ``stale_after`` is not positive.
    """
    caught_up = tick(rec, ev.observed_at, stale_after)
    rec, earlier = caught_up.record, caught_up.transitions
    target = _target(rec, ev)
    if rec.state is InterfaceLifecycle.RETIRED or target is None or ev.observed_at < rec.since:
        return LifecycleUpdate(rec, earlier)
    state, reason = target
    stale_still_inactive = (
        rec.state is InterfaceLifecycle.STALE and state is InterfaceLifecycle.INACTIVE
    )
    if state is rec.state or stale_still_inactive:
        # Still inactive: the stale clock keeps running; only the cause may change.
        kept = replace(rec, reason=reason) if state is InterfaceLifecycle.INACTIVE else rec
        return LifecycleUpdate(kept, earlier)
    moved = _move(rec, state, reason, ev.observed_at, ev.source)
    return LifecycleUpdate(moved.record, earlier + moved.transitions)


def retire(rec: LifecycleRecord, at: datetime, actor: str) -> LifecycleUpdate:
    """Retire an interface by administrator action (E.6: never automatic).

    Args:
        rec: Current record.
        at: When the action took effect.
        actor: The administrator.

    Returns:
        LifecycleUpdate: RETIRED, or unchanged if already retired.

    Raises:
        ValueError: If ``at`` is naive.
    """
    require_aware(at, "at")
    if rec.state is InterfaceLifecycle.RETIRED:
        return LifecycleUpdate(rec)
    return _move(
        rec,
        InterfaceLifecycle.RETIRED,
        LifecycleReason.RETIRED_BY_ADMIN,
        at,
        LifecycleSource.ADMIN,
        actor,
    )


def restore(rec: LifecycleRecord, at: datetime, actor: str) -> LifecycleUpdate:
    """Return a retired interface to service as INACTIVE until Stage 1 sees it up/up.

    Args:
        rec: Current record.
        at: When the action took effect.
        actor: The administrator.

    Returns:
        LifecycleUpdate: INACTIVE, or unchanged if it was not retired.

    Raises:
        ValueError: If ``at`` is naive.
    """
    require_aware(at, "at")
    if rec.state is not InterfaceLifecycle.RETIRED:
        return LifecycleUpdate(rec)
    return _move(
        rec,
        InterfaceLifecycle.INACTIVE,
        LifecycleReason.RESTORED_BY_ADMIN,
        at,
        LifecycleSource.ADMIN,
        actor,
    )
