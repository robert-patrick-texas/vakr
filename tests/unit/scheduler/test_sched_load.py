"""Simulated-time load test: 100 devices at 60 s for 24 hours (TEST-004, 4.2, SCHED-T07).

The real ``Scheduler`` runs on the virtual-time loop with steps whose
latencies are drawn from seeded distributions modelled on IOS XE behaviour
at the MVP workload (27.2): five probes 200 ms apart, Stage 1 status reads
around 60 ms with rare retried timeouts, Stage 2 counter walks around
250 ms with a rare slow tail, three devices that are down, two whose SNMP
agent is dead, and one that sometimes hangs past the poll deadline. Each
step also spends 0.2-2 ms of event-loop CPU, so polls due together start
measurably late, as they would on a real host.

Pass conditions:

* healthy-device completion (slot to completion) p95 < 15 s and p99 < 30 s
  (TEST-004, 4.2);
* 99% of starts within 5 s of the slot (4.2);
* every slot of every device recorded exactly once: zero unrecorded misses
  (invariant 10, SCHED-012, SCHED-017);
* misses never carry observations or read as anything but unknown.

Records are aggregated as they arrive, so memory stays flat over 144,000
polls. The run takes about 20 s of real time.

Dependencies:
    random: Seeded per-device latency streams.
    vakr.scheduler: The code under test.

Example:
    $ uv run pytest tests/unit/scheduler/test_sched_load.py
"""

import asyncio
import itertools
import random
from collections import Counter
from dataclasses import dataclass, field
from datetime import timedelta

from tests.unit.scheduler.sched_fakes import IDS, device_id, make_target, settings
from tests.unit.scheduler.sched_simloop import new_clock, run_virtual
from vakr.domain.enums import PollExecution
from vakr.domain.ids import DeviceId
from vakr.domain.quality import Quality
from vakr.logic.stats import nearest_rank
from vakr.ports.clock import ManualClock
from vakr.scheduler.driver import Scheduler
from vakr.scheduler.outbox import Outbox
from vakr.scheduler.records import PollResult
from vakr.scheduler.steps import IcmpResult, PollContext, PollSteps, SnmpResult, StepStatus

DAY_S = 86_400
DOWN = {device_id(i) for i in (90, 91, 92)}
SNMP_DEAD = {device_id(i) for i in (93, 94)}
HANGS = device_id(95)
HEALTHY = {device_id(i) for i in range(100)} - DOWN - SNMP_DEAD - {HANGS}


class LoadSteps:
    """ICMP and SNMP steps with seeded, per-device latency distributions."""

    def __init__(self, clock: ManualClock) -> None:
        """Seed one random stream per device.

        Args:
            clock: Virtual clock, advanced synchronously to model CPU time.

        Returns:
            None

        Raises:
            None
        """
        self._clock = clock
        # Seeded simulation streams, not security randomness.
        self._rng = {device_id(i): random.Random(i) for i in range(100)}  # noqa: S311

    def _cpu(self, device: DeviceId) -> random.Random:
        """Spend 0.2-2 ms of event-loop CPU, as encoding and parsing would.

        Advancing the clock without yielding is what a real step does to the
        loop, so concurrent due slots start measurably late.

        Args:
            device: The polled device.

        Returns:
            random.Random: The device's random stream.

        Raises:
            None
        """
        rng = self._rng[device]
        self._clock.advance(timedelta(microseconds=round(rng.uniform(200, 2000))))
        return rng

    async def icmp(self, ctx: PollContext) -> IcmpResult:
        """Five probes 200 ms apart; a lost last probe waits out its 1 s timeout (E.9).

        Args:
            ctx: The poll.

        Returns:
            IcmpResult: Replies out of 5.

        Raises:
            None
        """
        device = ctx.target.device_id
        rng = self._cpu(device)
        if device in DOWN:
            await asyncio.sleep(1.8)
            return IcmpResult(0, 5)
        lost = rng.random() < 0.08  # CoPP drops a probe now and then (E.1)
        await asyncio.sleep(0.8 + rng.lognormvariate(-5.5, 0.6) + (1.0 if lost else 0.0))
        return IcmpResult(4 if lost else 5, 5)

    async def stage1(self, ctx: PollContext, fallback: bool) -> SnmpResult:  # noqa: ARG002
        """Status read: ~60 ms, rarely a 2 s timeout then a retry, dead agents time out.

        Args:
            ctx: The poll.
            fallback: Unused; these devices are ``icmp_gated``.

        Returns:
            SnmpResult: Status and up/up count.

        Raises:
            None
        """
        device = ctx.target.device_id
        if device in SNMP_DEAD:
            await asyncio.sleep(4.0)  # 2 s timeout x (1 + 1 retry), SNMP-011
            return SnmpResult(StepStatus.NO_RESPONSE)
        rng = self._cpu(device)
        retried = rng.random() < 0.003
        await asyncio.sleep(rng.lognormvariate(-2.8, 0.5) + (2.0 if retried else 0.0))
        return SnmpResult(
            StepStatus.OK, up_up=12, quality=Quality.RETRIED if retried else Quality.NONE
        )

    async def stage2(self, ctx: PollContext, stage1: SnmpResult) -> SnmpResult:  # noqa: ARG002
        """Counter walk: ~250 ms, a rare 8-20 s slow agent, and one device that hangs.

        Args:
            ctx: The poll.
            stage1: This poll's Stage 1 result.

        Returns:
            SnmpResult: OK.

        Raises:
            None
        """
        rng = self._cpu(ctx.target.device_id)
        if ctx.target.device_id == HANGS and rng.random() < 0.01:
            await asyncio.sleep(200.0)  # cut by the 90 s poll deadline
        slow = rng.random() < 0.002
        await asyncio.sleep(rng.uniform(8, 20) if slow else rng.lognormvariate(-1.4, 0.6))
        return SnmpResult(StepStatus.OK)


@dataclass
class Tally:
    """What the consumer learned from every record.

    Attributes:
        slots: Per-device slot times seen, for gap and duplicate checks.
        healthy_completion_us: Slot-to-completion of healthy devices' polls.
        lateness_us: Start minus slot of every started poll.
        executions: Records per execution state.
        bad_misses: Misses that carried observations or lacked NOT_ATTEMPTED.
    """

    slots: dict[DeviceId, list[int]] = field(default_factory=dict)
    healthy_completion_us: list[int] = field(default_factory=list)
    lateness_us: list[int] = field(default_factory=list)
    executions: Counter = field(default_factory=Counter)
    bad_misses: int = 0

    def add(self, r: PollResult) -> None:
        """Fold one record into the tally.

        Args:
            r: The record.

        Returns:
            None

        Raises:
            None
        """
        self.slots.setdefault(r.poll.device_id, []).append(r.slot_us)
        self.executions[r.poll.execution] += 1
        if r.miss is not None:
            self.bad_misses += bool(r.observations) or not r.poll.quality & Quality.NOT_ATTEMPTED
            return
        self.lateness_us.append(r.lateness_us or 0)
        if r.poll.device_id in HEALTHY:
            self.healthy_completion_us.append((r.lateness_us or 0) + (r.duration_us or 0))


async def _day(scheduler: Scheduler, tally: Tally) -> None:
    """Run the scheduler for 24 simulated hours with a batching consumer.

    Args:
        scheduler: The planned scheduler.
        tally: Receives every record.

    Returns:
        None

    Raises:
        None
    """
    box = scheduler.sink
    assert isinstance(box, Outbox)

    async def consume() -> None:
        """Drain the outbox every 5 s, as a spool writer's group commit would (E.8).

        Args:
            None

        Returns:
            None

        Raises:
            None
        """
        while True:
            await asyncio.sleep(5)
            while (item := box.get_nowait()) is not None:
                tally.add(item)

    consumer = asyncio.create_task(consume())
    runner = asyncio.create_task(scheduler.run())
    await asyncio.sleep(DAY_S)
    scheduler.stop()
    await runner
    consumer.cancel()
    while (item := box.get_nowait()) is not None:
        tally.add(item)


def test_hundred_devices_for_a_simulated_day() -> None:
    """TEST-004: completion p95 < 15 s, p99 < 30 s; zero unrecorded misses over 24 h.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    clock = new_clock()
    steps = LoadSteps(clock)
    scheduler = Scheduler(clock, settings(), IDS, PollSteps(steps, steps))
    scheduler.replan([make_target(i) for i in range(100)])
    tally = Tally()
    run_virtual(clock, _day(scheduler, tally))

    p95, p99 = (nearest_rank(tally.healthy_completion_us, p) for p in (95, 99))
    assert p95 < 15_000_000
    assert p99 < 30_000_000
    assert nearest_rank(tally.lateness_us, 99) <= 5_000_000  # 4.2: 99% within ~5 s
    assert len(tally.slots) == 100
    for device, slots in tally.slots.items():
        ordered = sorted(slots)
        assert len(ordered) == len(set(ordered)) >= DAY_S // 60, device  # one record per slot
        assert {b - a for a, b in itertools.pairwise(ordered)} == {60_000_000}, device
    assert tally.bad_misses == 0
    assert tally.executions[PollExecution.MISSED_OVERRUN] > 0  # the hanging device overran
    assert tally.executions[PollExecution.NOT_ATTEMPTED] < 200  # only shutdown and overrun fallout
    snap = scheduler.snapshot()
    assert snap.lifetime.capacity == 0
    assert snap.queue_depth == 0
    assert snap.lifetime.scheduled == sum(len(s) for s in tally.slots.values())
