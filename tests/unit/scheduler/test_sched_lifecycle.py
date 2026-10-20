"""Shutdown, backpressure, event-loop lag, and health values (16.4, QUEUE-T01, OBS-001..004).

Dependencies:
    vakr.scheduler: The code under test.
    tests.unit.scheduler: Virtual time and scripted steps.

Example:
    $ uv run pytest tests/unit/scheduler/test_sched_lifecycle.py
"""

import asyncio
from datetime import timedelta

import pytest

from tests.unit.scheduler.sched_fakes import (
    IDS,
    Behaviour,
    ScriptedSteps,
    assert_every_slot_recorded,
    device_id,
    first_due_s,
    make_target,
    new_scheduler,
    run_for,
    settings,
)
from tests.unit.scheduler.sched_simloop import new_clock, run_virtual
from vakr.config.loader import EffectiveConfig
from vakr.domain.enums import CollectorState, PollExecution
from vakr.domain.ids import ObservationId, new_id
from vakr.domain.polls import CollectorHealthObservation
from vakr.domain.quality import Quality
from vakr.ports.clock import ManualClock
from vakr.scheduler.admission import MissReason
from vakr.scheduler.driver import Scheduler
from vakr.scheduler.outbox import Outbox
from vakr.scheduler.records import PollResult
from vakr.scheduler.settings import _get
from vakr.scheduler.steps import IcmpResult, PollContext, PollSteps, StepStatus

S = 1_000_000


def test_shutdown_records_waiting_cancelled_and_due_slots() -> None:
    """16.4: stop admitting, wait the grace period, cancel, and record everything.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    clock = new_clock()
    steps = ScriptedSteps(clock, behaviours={device_id(0): Behaviour(icmp_s=10_000)})
    scheduler = new_scheduler(
        clock, steps, [make_target(i) for i in range(20)], workers=1, poll_deadline_s=3600
    )
    began = clock.monotonic()
    records = run_for(clock, scheduler, 200)
    assert abs(clock.monotonic() - began - 215) < 0.01  # stop at 200 s plus the 15 s grace
    assert_every_slot_recorded(records)
    hung = next(r for r in records if r.poll.device_id == device_id(0) and r.miss is None)
    assert [s.status for s in hung.steps] == [StepStatus.CANCELLED]
    assert hung.poll.execution is PollExecution.CANCELLED  # shutdown, not the device
    assert not hung.poll.quality & Quality.COLLECTOR_DEGRADED  # the state says why
    draining = [r for r in records if r.miss is MissReason.DRAINING]
    assert draining
    assert all(r.poll.execution is PollExecution.NOT_ATTEMPTED for r in draining)
    stop_us = scheduler._dispatch.timeline.now_us() - 15 * S
    assert any(r.slot_us > stop_us for r in draining)  # slots that came due during the grace
    assert scheduler.snapshot().draining


def test_shutdown_lets_short_polls_finish_and_keeps_partial_evidence() -> None:
    """16.4: a poll that ends within the grace completes; a longer one keeps its steps.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    clock = new_clock()
    behaviours = {device_id(0): Behaviour(stage2_s=8), device_id(1): Behaviour(stage2_s=500)}
    scheduler = new_scheduler(clock, ScriptedSteps(clock, behaviours), [make_target(0)])
    scheduler.replan([make_target(0), make_target(1)])
    stop_at = max(first_due_s(scheduler, 0), first_due_s(scheduler, 1)) + 3
    began = clock.monotonic()
    records = run_for(clock, scheduler, stop_at)
    finished = {r.poll.device_id: r for r in records if r.miss is None}
    short, long_ = finished[device_id(0)], finished[device_id(1)]
    assert short.poll.execution is PollExecution.COMPLETE
    assert long_.poll.execution is PollExecution.CANCELLED
    assert [s.status for s in long_.steps] == [StepStatus.OK, StepStatus.OK, StepStatus.CANCELLED]
    assert [o[0] for o in long_.observations] == ["icmp", "s1"]  # ty: ignore[not-subscriptable]
    assert abs(clock.monotonic() - began - stop_at - 15) < 0.01
    assert scheduler.stats.lifetime.cancelled == 1


async def _starved(scheduler: Scheduler, box: Outbox) -> tuple[list[PollResult], dict]:
    """Let the outbox fill for 300 s, then consume until 600 s and stop.

    Args:
        scheduler: The scheduler.
        box: Its outbox.

    Returns:
        tuple[list[PollResult], dict]: Every record, and facts observed while full.

    Raises:
        None
    """
    records: list[PollResult] = []
    runner = asyncio.create_task(scheduler.run())
    await asyncio.sleep(300)
    snap = scheduler.snapshot()
    seen = {"depth": box.depth, "blocked": snap.producers_blocked, "state": snap.collector_state()}

    async def consume() -> None:
        """Move records out of the outbox forever.

        Args:
            None

        Returns:
            None

        Raises:
            None
        """
        while True:
            records.append(await box.get())

    consumer = asyncio.create_task(consume())
    await asyncio.sleep(300)
    scheduler.stop()
    await runner
    consumer.cancel()
    while (item := box.get_nowait()) is not None:
        records.append(item)
    return records, seen


def test_full_outbox_applies_reported_backpressure_without_loss() -> None:
    """QUEUE-T01, 6.4: a full queue blocks producers, is reported, and loses nothing.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    clock = new_clock()
    steps, box = ScriptedSteps(clock), Outbox(4, clock)
    scheduler = Scheduler(clock, settings(), IDS, PollSteps(steps, steps), sink=box)
    scheduler.replan([make_target(i) for i in range(10)])
    records, seen = run_virtual(clock, _starved(scheduler, box))
    assert seen["depth"] == 4
    assert seen["blocked"] > 0
    assert seen["state"] is CollectorState.OVERLOADED
    assert box.high_water == 4
    assert box.full_waits > 0
    assert box.blocked_s > 0
    assert_every_slot_recorded(records)
    late = [r for r in records if r.miss in (MissReason.LATE_DISPATCH, MissReason.NO_WORKER)]
    assert late
    assert all(r.poll.quality & Quality.COLLECTOR_DEGRADED for r in late)


class _BlockingIcmp:
    """An ICMP step that blocks the event loop, as a stray synchronous call would."""

    def __init__(self, clock: ManualClock) -> None:
        """Bind the clock the blocking call advances.

        Args:
            clock: Virtual clock.

        Returns:
            None

        Raises:
            None
        """
        self._clock = clock

    async def icmp(self, ctx: PollContext) -> IcmpResult:  # noqa: ARG002 - step protocol
        """Block for 2.5 s without yielding, then report 5/5.

        Args:
            ctx: The poll.

        Returns:
            IcmpResult: Five replies.

        Raises:
            None
        """
        self._clock.advance(timedelta(seconds=2.5))
        return IcmpResult(5, 5)


def test_event_loop_lag_is_measured_and_degrades_the_collector() -> None:
    """OBS-004, OBS-007: a blocked loop shows as lag and DEGRADED, not as a target problem.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    clock = new_clock()
    steps = PollSteps(_BlockingIcmp(clock), ScriptedSteps(clock))
    scheduler = Scheduler(clock, settings(), IDS, steps)
    scheduler.replan([make_target(0)])
    run_for(clock, scheduler, 61)
    snap = scheduler.snapshot()
    assert snap.loop_lag_us is not None
    assert snap.loop_lag_us >= 1_500_000
    assert snap.collector_state() is CollectorState.DEGRADED


def test_snapshot_fills_a_valid_collector_health_observation() -> None:
    """OBS-001, OBS-004, 10.4: snapshot values fit the health record; periods reset.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    clock = new_clock()
    scheduler = new_scheduler(clock, ScriptedSteps(clock), [make_target(i) for i in range(5)])
    assert scheduler.snapshot().lateness is None  # nothing measured yet: unknown, not zero
    run_for(clock, scheduler, 180)
    snap = scheduler.snapshot(close_period=True)
    assert snap.lateness is not None
    assert snap.lateness.p99 <= 10
    assert snap.completion is not None
    assert snap.duration is not None
    assert snap.completion.p50 >= snap.duration.p50
    values = snap.health_values()
    obs = CollectorHealthObservation(
        new_id(ObservationId), IDS.collector_id, clock.now_utc(), snap.collector_state(),
        "vakr-test", **values,  # ty: ignore[invalid-argument-type]
    )  # fmt: skip
    assert obs.polls_completed == snap.period.completed > 0
    assert obs.active_devices == 5
    assert obs.queue_capacity == 1000
    assert obs.polls_missed == snap.period.missed
    again = scheduler.snapshot()
    assert again.period.scheduled == 0
    assert again.lifetime.scheduled == snap.lifetime.scheduled


async def _stop_while_full(scheduler: Scheduler, box: Outbox) -> list[PollResult]:
    """Fill the outbox, stop, and start consuming only after the grace period.

    Args:
        scheduler: The scheduler.
        box: Its outbox.

    Returns:
        list[PollResult]: Every record.

    Raises:
        None
    """
    records: list[PollResult] = []
    runner = asyncio.create_task(scheduler.run())
    await asyncio.sleep(120)
    scheduler.stop()
    await asyncio.sleep(20)  # past the 15 s grace: blocked hand-offs were cancelled
    while not runner.done():
        if (item := box.get_nowait()) is not None:
            records.append(item)
        await asyncio.sleep(0.1)
    while (item := box.get_nowait()) is not None:
        records.append(item)
    return records


def test_shutdown_re_emits_records_stuck_on_a_full_outbox_once() -> None:
    """16.4 with backpressure: a finished record cut off mid hand-off is kept, counted once.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    clock = new_clock()
    steps, box = ScriptedSteps(clock), Outbox(1, clock)
    scheduler = Scheduler(clock, settings(), IDS, PollSteps(steps, steps), sink=box)
    scheduler.replan([make_target(i) for i in range(3)])
    records = run_virtual(clock, _stop_while_full(scheduler, box))
    assert_every_slot_recorded(records)
    finished = [r for r in records if r.miss is None]
    assert len({r.poll.poll_id for r in finished}) == len(finished)
    assert all(r.poll.execution is PollExecution.COMPLETE for r in finished)
    assert scheduler.stats.lifetime.scheduled == len(records)


def test_outbox_and_settings_refuse_unbounded_or_unknown_inputs() -> None:
    """Every queue is bounded (6.4) and settings read only catalog keys (CFGM-001).

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    with pytest.raises(ValueError, match="capacity"):
        Outbox(0, new_clock())
    with pytest.raises(KeyError, match="nonexistent"):
        _get(EffectiveConfig({}), "scheduler.nonexistent")
