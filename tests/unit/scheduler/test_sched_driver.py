"""The scheduler in virtual time: overruns, capacity, clock steps, reload (SCHED-T04, -T05, -T10).

Each test runs the real ``Scheduler`` on ``VirtualTimeLoop`` with scripted
steps, so minutes of polling take milliseconds and every timing is exact.
The recurring check is ``assert_every_slot_recorded``: whatever happens,
each device has exactly one record per slot (invariant 10; "a missed poll
is always recorded").

Dependencies:
    vakr.scheduler: The code under test.
    tests.unit.scheduler: Virtual time and scripted steps.

Example:
    $ uv run pytest tests/unit/scheduler/test_sched_driver.py
"""

import itertools
from collections import Counter
from datetime import timedelta

import pytest

from tests.unit.scheduler.sched_fakes import (
    Behaviour,
    ScriptedSteps,
    assert_every_slot_recorded,
    device_id,
    make_target,
    new_scheduler,
    run_for,
    slots_by_device,
)
from tests.unit.scheduler.sched_simloop import new_clock
from vakr.domain.enums import CollectorState, PollExecution
from vakr.domain.quality import UNKNOWN_CONTEXT, Quality
from vakr.scheduler.admission import MissReason
from vakr.scheduler.records import PollResult
from vakr.scheduler.steps import StepStatus

S = 1_000_000


def _by_device(records: list[PollResult], i: int) -> list[PollResult]:
    """Return one device's records in slot order.

    Args:
        records: Scheduler records.
        i: Device number.

    Returns:
        list[PollResult]: That device's records.

    Raises:
        None
    """
    return sorted((r for r in records if r.poll.device_id == device_id(i)), key=lambda r: r.slot_us)


def test_slow_poll_records_missed_overrun_and_never_overlaps() -> None:
    """SCHED-T05, SCHED-012: a 70 s poll at 60 s makes the next slot MISSED_OVERRUN.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    clock = new_clock()
    steps = ScriptedSteps(clock, behaviours={device_id(1): Behaviour(icmp_s=70)})
    scheduler = new_scheduler(clock, steps, [make_target(0), make_target(1)])
    records = run_for(clock, scheduler, 400)
    assert_every_slot_recorded(records)
    slow = [r.poll.execution for r in _by_device(records, 1) if r.miss is not MissReason.DRAINING]
    assert slow[:4] == [PollExecution.COMPLETE, PollExecution.MISSED_OVERRUN] * 2
    spans = {}
    for call in (c for c in steps.calls if c.device == device_id(1)):
        lo, hi = spans.get(call.poll, (call.start, call.end))
        spans[call.poll] = (min(lo, call.start), max(hi, call.end or hi))
    ordered = sorted(spans.values())
    assert all(a[1] <= b[0] for a, b in itertools.pairwise(ordered))  # one session at a time
    overrun = next(r for r in records if r.poll.execution is PollExecution.MISSED_OVERRUN)
    assert overrun.poll.quality & UNKNOWN_CONTEXT
    assert not overrun.observations
    assert all(r.poll.execution is PollExecution.COMPLETE for r in _by_device(records, 0)[:-1])


def test_poll_deadline_cuts_a_hung_poll_and_keeps_cadence() -> None:
    """``poll_deadline``: a hung ICMP step ends at 90 s as FAILED/DEADLINE; one slot is missed.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    clock = new_clock()
    steps = ScriptedSteps(clock, default=Behaviour(icmp_s=10_000))
    scheduler = new_scheduler(clock, steps, [make_target(0)])
    records = run_for(clock, scheduler, 300)
    assert_every_slot_recorded(records)
    first, second, third = _by_device(records, 0)[:3]
    assert first.poll.execution is PollExecution.FAILED
    assert [s.status for s in first.steps] == [StepStatus.DEADLINE]
    assert abs((first.duration_us or 0) - 90 * S) <= 10  # virtual time rounds up to 1 us
    assert second.poll.execution is PollExecution.MISSED_OVERRUN
    assert (third.lateness_us or 0) <= 10  # cadence is anchored, not shifted by the hang
    assert scheduler.stats.lifetime.deadline >= 2


def test_worker_exhaustion_is_not_attempted_and_overloaded() -> None:
    """SCHED-T10, SCHED-016/017, OBS-003: capacity rejections are unknown, not device-down.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    clock = new_clock()
    steps = ScriptedSteps(clock, default=Behaviour(icmp_s=40))
    scheduler = new_scheduler(clock, steps, [make_target(i) for i in range(10)], workers=2)
    records = run_for(clock, scheduler, 600, [(300, lambda s: s.snapshot(close_period=True))])
    assert_every_slot_recorded(records)
    rejected = [r for r in records if r.miss in (MissReason.NO_WORKER, MissReason.SUPERSEDED)]
    assert len(rejected) > 20
    for r in rejected:
        assert r.poll.execution is PollExecution.NOT_ATTEMPTED
        assert not r.observations
        assert r.poll.quality == Quality.NOT_ATTEMPTED | Quality.COLLECTOR_DEGRADED
    starts = sorted((c.start, 1) for c in steps.calls) + sorted(
        (c.end or 0, -1) for c in steps.calls
    )
    running = peak = 0
    for _, delta in sorted(starts, key=lambda p: (p[0], p[1])):
        running += delta
        peak = max(peak, running)
    assert peak <= 2  # never more polls in flight than workers
    snap = scheduler.snapshot()
    assert snap.period.capacity > 0
    assert snap.collector_state() is CollectorState.OVERLOADED


@pytest.mark.parametrize("step_s", [3600, -3600, 2])
def test_wall_clock_step_does_not_move_the_schedule(step_s: int) -> None:
    """SCHED-T04, SCHED-011/018, TIME-001: cadence follows monotonic time; labels follow UTC.

    Args:
        step_s: Wall-clock step applied at 150 s.

    Returns:
        None

    Raises:
        None
    """

    def run(step: int) -> tuple[list[PollResult], int]:
        """Run 10 devices for 10 minutes, stepping the wall clock at 150 s.

        Args:
            step: Step in seconds; 0 for the baseline.

        Returns:
            tuple[list[PollResult], int]: Records and detected clock steps.

        Raises:
            None
        """
        clock = new_clock()
        scheduler = new_scheduler(clock, ScriptedSteps(clock), [make_target(i) for i in range(10)])

        def jump(_: object) -> None:
            """Step the wall clock like ``ntpd -g`` or an operator's ``date -s``.

            Args:
                _: The scheduler (unused).

            Returns:
                None

            Raises:
                None
            """
            clock.set_utc(clock.now_utc() + timedelta(seconds=step))

        records = run_for(clock, scheduler, 600, [(150, jump)] if step else [])
        return records, scheduler.snapshot().clock_steps

    baseline, _ = run(0)
    stepped, steps = run(step_s)
    assert steps == 1
    assert slots_by_device(stepped) == slots_by_device(baseline)  # same polls at the same times
    assert_every_slot_recorded(stepped)
    shift = {
        (s.poll.scheduled_at - b.poll.scheduled_at).total_seconds()
        for b, s in zip(baseline, stepped, strict=True)
    }
    assert shift == {0, step_s}  # labels before the step unchanged, after it shifted once
    for r in stepped:
        if r.poll.started_at is not None:
            assert r.poll.completed_at is not None
            assert r.poll.completed_at >= r.poll.started_at


def test_reload_keeps_unchanged_devices_and_spreads_new_ones() -> None:
    """Reload: no burst, no gap, no duplicate for unchanged devices; new ones join their grid.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    clock = new_clock()
    scheduler = new_scheduler(clock, ScriptedSteps(clock), [make_target(i) for i in range(10)])
    after = [make_target(i) for i in range(1, 9)] + [make_target(9, interval_s=120)]
    after += [make_target(i) for i in range(100, 130)]
    records = run_for(clock, scheduler, 600, [(90, lambda s: s.replan(after))])
    by_device = slots_by_device(records)
    for i in range(1, 9):
        slots = sorted(by_device[device_id(i)])
        assert {b - a for a, b in itertools.pairwise(slots)} == {60 * S}
    spacing = sorted(by_device[device_id(9)])
    assert 120 * S in {b - a for a, b in itertools.pairwise(spacing)}
    assert max(by_device[device_id(0)]) < min(min(by_device[device_id(i)]) for i in range(100, 130))
    firsts = [min(by_device[device_id(i)]) for i in range(100, 130)]
    assert max(firsts) - min(firsts) < 60 * S
    assert max(Counter(f // S for f in firsts).values()) <= 4  # no burst


def test_step_errors_read_as_collector_trouble_and_polling_continues() -> None:
    """Invariant 2: a collector-side step error is COLLECTOR_DEGRADED, never a target fault.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    clock = new_clock()
    steps = ScriptedSteps(clock, behaviours={device_id(0): Behaviour(raise_in="icmp")})
    scheduler = new_scheduler(clock, steps, [make_target(0), make_target(1)])
    records = run_for(clock, scheduler, 200)
    broken = [r for r in _by_device(records, 0) if r.miss is None]
    assert len(broken) >= 3
    assert all(r.poll.execution is PollExecution.FAILED for r in broken)
    assert all(r.poll.quality & Quality.COLLECTOR_DEGRADED for r in broken)
    assert scheduler.snapshot().collector_state() is CollectorState.DEGRADED
