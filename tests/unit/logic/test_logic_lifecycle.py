"""Tests for interface lifecycle transitions (LIFE-T01, E.6, LIFE-001..006).

LIFE-T01 walks one interface ACTIVE -> INACTIVE -> STALE and back, checking
the 10-day threshold, the effective time of each transition, and the
polling eligibility of each state. Further tables pin the E.6 entry
conditions and the rules for weak or late evidence.

Dependencies:
    pytest: Tables.
    vakr.logic.lifecycle, vakr.logic.lifecycle_model: Under test.

Example:
    $ uv run pytest tests/unit/logic/test_logic_lifecycle.py
"""

from collections.abc import Callable
from datetime import UTC, datetime, timedelta

import pytest

from vakr.domain.enums import IfAdminStatus, IfOperStatus, InterfaceLifecycle
from vakr.logic.lifecycle import apply_evidence, restore, retire, tick
from vakr.logic.lifecycle_model import (
    InterfaceEvidence,
    LifecycleReason,
    LifecycleRecord,
    LifecycleSource,
    PollingEligibility,
    StatusCadence,
    polling_eligibility,
)

T0 = datetime(2026, 1, 1, tzinfo=UTC)
DAY = timedelta(days=1)
L, R = InterfaceLifecycle, LifecycleReason
STAGE1, DISCOVERY = LifecycleSource.STAGE1, LifecycleSource.DISCOVERY
UP, DOWN = IfAdminStatus.UP, IfAdminStatus.DOWN
ACTIVE = LifecycleRecord(L.ACTIVE, R.UP_UP, T0)


def stage1(
    at: datetime, admin: IfAdminStatus | None, oper: IfOperStatus | None
) -> InterfaceEvidence:
    """Build a Stage 1 result.

    Args:
        at: When it was observed.
        admin: ``ifAdminStatus``.
        oper: ``ifOperStatus``.

    Returns:
        InterfaceEvidence: The evidence.

    Raises:
        None
    """
    return InterfaceEvidence(at, STAGE1, True, admin, oper)


def test_life_t01_active_inactive_stale_and_back() -> None:
    """LIFE-T01: thresholds, effective times, and polling eligibility (LIFE-001, -003, -004).

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    down_at = T0 + DAY
    u = apply_evidence(ACTIVE, stage1(down_at, UP, IfOperStatus.DOWN))
    assert (u.record.state, u.record.reason, u.record.inactive_since) == (
        L.INACTIVE,
        R.OPER_NOT_UP,
        down_at,
    )
    assert polling_eligibility(L.INACTIVE) == PollingEligibility(StatusCadence.EVERY_POLL, False)
    almost = tick(u.record, down_at + 10 * DAY - timedelta(seconds=1))
    assert (almost.record, almost.transitions) == (u.record, ())
    stale = tick(u.record, down_at + 12 * DAY)
    (t,) = stale.transitions
    assert (t.prior, t.new, t.reason, t.source) == (
        L.INACTIVE,
        L.STALE,
        R.STALE_AFTER,
        LifecycleSource.TIMER,
    )
    assert t.effective_at == down_at + 10 * DAY  # when the threshold was crossed, not when checked
    assert stale.record.reason is R.OPER_NOT_UP  # the cause of inactivity is kept
    assert polling_eligibility(L.STALE) == PollingEligibility(StatusCadence.DISCOVERY, False)
    back = apply_evidence(stale.record, stage1(down_at + 13 * DAY, UP, IfOperStatus.UP))
    (t,) = back.transitions
    assert (t.prior, t.new, t.reason, back.record.inactive_since) == (
        L.STALE,
        L.ACTIVE,
        R.UP_UP,
        None,
    )
    assert polling_eligibility(L.ACTIVE) == PollingEligibility(StatusCadence.EVERY_POLL, True)


def test_life_t01_evidence_after_threshold_records_stale_first() -> None:
    """LIFE-006: an up/up result after 12 days records STALE at day 10, then ACTIVE.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    inactive = LifecycleRecord(L.INACTIVE, R.ADMIN_DOWN, T0, T0)
    u = apply_evidence(inactive, stage1(T0 + 12 * DAY, UP, IfOperStatus.UP))
    assert [(t.new, t.effective_at) for t in u.transitions] == [
        (L.STALE, T0 + 10 * DAY),
        (L.ACTIVE, T0 + 12 * DAY),
    ]
    assert [t.prior for t in u.transitions] == [L.INACTIVE, L.STALE]


@pytest.mark.parametrize(
    ("record", "evidence", "state", "reason"),
    [
        pytest.param(
            ACTIVE,
            stage1(T0 + DAY, DOWN, IfOperStatus.DOWN),
            L.INACTIVE,
            R.ADMIN_DOWN,
            id="E.6 admin down",
        ),
        pytest.param(
            ACTIVE,
            stage1(T0 + DAY, UP, IfOperStatus.LOWER_LAYER_DOWN),
            L.INACTIVE,
            R.OPER_NOT_UP,
            id="E.6 lowerLayerDown",
        ),
        pytest.param(
            ACTIVE,
            stage1(T0 + DAY, UP, IfOperStatus.NOT_PRESENT),
            L.INACTIVE,
            R.OPER_NOT_UP,
            id="E.6 notPresent",
        ),
        pytest.param(
            ACTIVE,
            stage1(T0 + DAY, UP, IfOperStatus.DORMANT),
            L.INACTIVE,
            R.OPER_NOT_UP,
            id="E.6 dormant",
        ),
        pytest.param(
            ACTIVE,
            stage1(T0 + DAY, UP, IfOperStatus.TESTING),
            L.INACTIVE,
            R.OPER_NOT_UP,
            id="E.6 testing is not up/up",
        ),
        pytest.param(
            ACTIVE,
            InterfaceEvidence(T0 + DAY, DISCOVERY, present=False),
            L.INACTIVE,
            R.NOT_DISCOVERED,
            id="E.6 missing from discovery",
        ),
        pytest.param(
            ACTIVE,
            stage1(T0 + DAY, UP, IfOperStatus.UNKNOWN),
            L.ACTIVE,
            R.UP_UP,
            id="E.6 oper unknown is not evidence",
        ),
        pytest.param(
            ACTIVE,
            stage1(T0 + DAY, None, None),
            L.ACTIVE,
            R.UP_UP,
            id="E.6 missing status is not evidence",
        ),
        pytest.param(
            LifecycleRecord(L.INACTIVE, R.NOT_DISCOVERED, T0, T0),
            InterfaceEvidence(T0 + DAY, DISCOVERY),
            L.ACTIVE,
            R.RECONCILED,
            id="E.6 rediscovery reconciles a missing interface",
        ),
        pytest.param(
            LifecycleRecord(L.STALE, R.NOT_DISCOVERED, T0 + 10 * DAY, T0),
            InterfaceEvidence(T0 + 11 * DAY, DISCOVERY),
            L.ACTIVE,
            R.RECONCILED,
            id="E.6 rediscovery reconciles a stale missing interface",
        ),
        pytest.param(
            LifecycleRecord(L.INACTIVE, R.ADMIN_DOWN, T0, T0),
            InterfaceEvidence(T0 + DAY, DISCOVERY),
            L.INACTIVE,
            R.ADMIN_DOWN,
            id="E.6 rediscovery without status keeps a down interface down",
        ),
        pytest.param(
            LifecycleRecord(L.INACTIVE, R.NOT_DISCOVERED, T0, T0),
            InterfaceEvidence(T0 + DAY, DISCOVERY, True, UP, IfOperStatus.DOWN),
            L.INACTIVE,
            R.OPER_NOT_UP,
            id="E.6 reappears but down: cause updated",
        ),
        pytest.param(
            LifecycleRecord(L.INACTIVE, R.ADMIN_DOWN, T0, T0),
            InterfaceEvidence(T0 + DAY, DISCOVERY, True, UP, IfOperStatus.UP),
            L.ACTIVE,
            R.RECONCILED,
            id="E.6 discovery shows up/up",
        ),
        pytest.param(
            LifecycleRecord(L.STALE, R.ADMIN_DOWN, T0 + 10 * DAY, T0),
            stage1(T0 + 11 * DAY, DOWN, IfOperStatus.DOWN),
            L.STALE,
            R.ADMIN_DOWN,
            id="LIFE-004 still down: stays stale",
        ),
        pytest.param(
            LifecycleRecord(L.RETIRED, R.RETIRED_BY_ADMIN, T0),
            stage1(T0 + DAY, UP, IfOperStatus.UP),
            L.RETIRED,
            R.RETIRED_BY_ADMIN,
            id="E.6 retired changes only by admin action",
        ),
        pytest.param(
            LifecycleRecord(L.INACTIVE, R.ADMIN_DOWN, T0 + DAY, T0 + DAY),
            stage1(T0, UP, IfOperStatus.UP),
            L.INACTIVE,
            R.ADMIN_DOWN,
            id="LIFE-006 late evidence cannot rewind history",
        ),
    ],
)
def test_e6_entry_conditions(
    record: LifecycleRecord,
    evidence: InterfaceEvidence,
    state: InterfaceLifecycle,
    reason: LifecycleReason,
) -> None:
    """E.6: which evidence moves an interface where, and which proves nothing.

    Args:
        record: Current record.
        evidence: New evidence.
        state: Expected state.
        reason: Expected reason.

    Returns:
        None

    Raises:
        None
    """
    u = apply_evidence(record, evidence)
    assert (u.record.state, u.record.reason) == (state, reason)
    changed = state is not record.state
    assert len(u.transitions) == int(changed)
    if changed:
        (t,) = u.transitions
        assert (t.prior, t.new, t.effective_at, t.source) == (
            record.state,
            state,
            evidence.observed_at,
            evidence.source,
        )


def test_retire_and_restore_are_admin_actions() -> None:
    """E.6, LIFE-006: retirement records the actor; restore waits for Stage 1.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    retired = retire(ACTIVE, T0 + DAY, "alice")
    (t,) = retired.transitions
    assert (t.new, t.reason, t.source, t.actor) == (
        L.RETIRED,
        R.RETIRED_BY_ADMIN,
        LifecycleSource.ADMIN,
        "alice",
    )
    assert polling_eligibility(L.RETIRED) == PollingEligibility(StatusCadence.NONE, False)
    assert retire(retired.record, T0 + 2 * DAY, "bob").transitions == ()
    restored = restore(retired.record, T0 + 3 * DAY, "bob")
    assert (restored.record.state, restored.record.inactive_since) == (L.INACTIVE, T0 + 3 * DAY)
    assert restored.transitions[0].actor == "bob"
    assert restore(ACTIVE, T0, "bob").transitions == ()


@pytest.mark.parametrize(
    "call",
    [
        pytest.param(lambda: tick(ACTIVE, T0.replace(tzinfo=None)), id="naive now"),
        pytest.param(lambda: tick(ACTIVE, T0, timedelta(0)), id="zero stale_after"),
        pytest.param(lambda: retire(ACTIVE, T0.replace(tzinfo=None), "a"), id="naive retire"),
        pytest.param(lambda: restore(ACTIVE, T0.replace(tzinfo=None), "a"), id="naive restore"),
    ],
)
def test_invalid_inputs(call: Callable[[], object]) -> None:
    """Naive times and a non-positive threshold are refused.

    Args:
        call: Zero-argument callable that must raise.

    Returns:
        None

    Raises:
        None
    """
    with pytest.raises(ValueError, match=r"timezone-aware|positive"):
        call()
