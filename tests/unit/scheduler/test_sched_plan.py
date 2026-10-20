"""The poll plan: spread, anchored cadence, mixed intervals, re-planning (SCHED-T03, -T06, -T07).

These tests drive ``Plan`` directly with integer plan-timeline times, so
they need no event loop and every expected slot can be computed by hand.

Dependencies:
    pytest: Parametrisation.
    vakr.scheduler.plan, phase: The code under test.
    tests.unit.scheduler.sched_fakes: Deterministic targets.

Example:
    $ uv run pytest tests/unit/scheduler/test_sched_plan.py
"""

from collections import Counter

import pytest

from tests.unit.scheduler.sched_fakes import RULE, device_id, make_target
from vakr.domain.enums import ReachabilityStrategy
from vakr.scheduler.phase import OrderMode, PhaseRule
from vakr.scheduler.plan import Plan

S = 1_000_000
T0 = 1_792_497_600 * S  # 2026-10-20T12:00:00Z on the plan timeline


def _drain(plan: Plan, until_us: int) -> list[tuple[int, int]]:
    """Pop every slot due up to a time, as (device number, slot) pairs.

    Args:
        plan: The plan.
        until_us: Last time to pop at.

    Returns:
        list[tuple[int, int]]: Device number (last UUID group) and slot time.

    Raises:
        None
    """
    out = []
    while (slot := plan.pop_due(until_us)) is not None:
        out.append((slot.target.device_id.int & 0xFFFFFFFFFFFF, slot.due_us))
    return out


@pytest.mark.parametrize(
    "rule",
    [RULE, PhaseRule("vakr-west"), PhaseRule("nyc-poller-01", order=OrderMode.HASHED)],
    ids=["east-common", "west-common", "nyc-hashed"],
)
def test_hundred_devices_spread_across_the_interval(rule: PhaseRule) -> None:
    """SCHED-T07, SCHED-008: 100 devices at 60 s start spread out, not in a boundary herd.

    Args:
        rule: Collector phase and order.

    Returns:
        None

    Raises:
        None
    """
    offsets = [rule.offset_us(device_id(i), 60) for i in range(100)]
    per_6s = Counter(o // (6 * S) for o in offsets)
    per_1s = Counter(o // S for o in offsets)
    assert sorted(per_6s) == list(range(10))  # every tenth of the minute has work
    assert max(per_6s.values()) <= 20  # no tenth carries more than twice its share
    assert max(per_1s.values()) <= 7  # SCHED-007: collisions reduced, not excluded
    near_boundary = sum(1 for o in offsets if o < S or o >= 59 * S)
    assert near_boundary <= 7


def test_cadence_is_anchored_to_intended_slots() -> None:
    """SCHED-T03, SCHED-010: popping late does not move the next slot.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    plan = Plan(RULE)
    plan.replan([make_target(1)], T0)
    offset = RULE.offset_us(device_id(1), 60)
    first = plan.next_due_us()
    assert first is not None
    assert first % (60 * S) == offset
    assert T0 <= first < T0 + 60 * S
    late = plan.pop_due(first + 25 * S)  # the poll started 25 s late
    assert late is not None
    assert late.due_us == first
    assert plan.next_slot_us(device_id(1)) == first + 60 * S


def test_falling_behind_pops_every_missed_slot() -> None:
    """9.9: catching up yields each past occurrence, so each can be recorded.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    plan = Plan(RULE)
    plan.replan([make_target(1)], T0)
    first = plan.next_due_us()
    assert first is not None
    popped = _drain(plan, first + 185 * S)
    assert [due for _, due in popped] == [first + k * 60 * S for k in range(4)]


def test_mixed_intervals_interleave_in_one_queue() -> None:
    """SCHED-T06, SCHED-013: 60/120/300 s devices share one due-time order.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    plan = Plan(RULE)
    targets = [make_target(i, interval_s=(60, 120, 300)[i % 3]) for i in range(30)]
    plan.replan(targets, T0)
    popped = _drain(plan, T0 + 600 * S - 1)
    dues = [due for _, due in popped]
    assert dues == sorted(dues)
    counts = Counter(n for n, _ in popped)
    assert {counts[i] for i in range(0, 30, 3)} == {10}
    assert {counts[i] for i in range(1, 30, 3)} == {5}
    assert {counts[i] for i in range(2, 30, 3)} == {2}


def test_replan_keeps_unchanged_devices_in_place() -> None:
    """Reload: unchanged devices keep phase and next slot; others join their own grid.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    plan = Plan(RULE)
    plan.replan([make_target(i) for i in range(10)], T0)
    _drain(plan, T0 + 30 * S)
    before = {i: plan.next_slot_us(device_id(i)) for i in range(10)}
    now = T0 + 31 * S
    targets = [make_target(i, strategy=ReachabilityStrategy.SNMP_ONLY) for i in range(1, 9)]
    targets += [make_target(9, interval_s=300), make_target(42)]
    summary = plan.replan(targets, now)
    assert (summary.added, summary.rescheduled, summary.kept) == (1, 1, 8)
    assert summary.removed == (device_id(0),)
    assert all(plan.next_slot_us(device_id(i)) == before[i] for i in range(1, 9))
    moved = plan.next_slot_us(device_id(9))
    assert moved is not None
    assert moved >= max(now, before[9] or 0)
    assert moved % (300 * S) == RULE.offset_us(device_id(9), 300)
    new = plan.next_slot_us(device_id(42))
    assert new is not None
    assert now <= new < now + 60 * S
    assert plan.next_slot_us(device_id(0)) is None
    assert len(plan) == 10


def test_replan_of_new_devices_does_not_burst() -> None:
    """Reload: 100 devices added at once start over the next interval, not together.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    plan = Plan(RULE)
    plan.replan([make_target(i) for i in range(100)], T0 + 7 * S)
    firsts = [plan.next_slot_us(device_id(i)) or 0 for i in range(100)]
    assert all(T0 + 7 * S <= f < T0 + 67 * S for f in firsts)
    assert max(Counter(f // S for f in firsts).values()) <= 7


def test_repeated_replans_keep_the_heap_bounded() -> None:
    """Stale heap entries are compacted, so memory follows the device count.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    plan = Plan(RULE)
    for round_ in range(200):
        interval = 60 if round_ % 2 else 120
        plan.replan([make_target(i, interval_s=interval) for i in range(5)], T0)
    assert len(plan._heap) <= 2 * len(plan) + 64
    assert len(_drain(plan, T0 + 120 * S)) >= 5


def test_duplicate_targets_last_one_wins_and_empty_plan_is_idle() -> None:
    """A device listed twice is scheduled once, with the later settings.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    plan = Plan(RULE)
    assert plan.pop_due(T0) is None
    plan.replan([make_target(3, interval_s=60), make_target(3, interval_s=300)], T0)
    assert len(plan) == 1
    slot = plan.pop_due(T0 + 300 * S)
    assert slot is not None
    assert slot.target.effective_interval_s == 300
