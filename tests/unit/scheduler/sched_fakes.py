"""Test doubles for the scheduler: devices, scripted steps, and a run harness.

``ScriptedSteps`` implements both step protocols. Each device follows a
``Behaviour`` (latencies, reply count, SNMP outcomes, injected collector
errors), every step call is logged with its poll and virtual start and end
times, and Stage 2 inputs are kept so tests can check they came from the
same poll's Stage 1. ``run_for`` runs a ``Scheduler`` in virtual time for a
given span, applies timed actions (re-plans, clock steps), stops it, and
returns every record in emission order.

Dependencies:
    vakr.scheduler: The code under test.
    tests.unit.scheduler.sched_simloop: Virtual time.

Example:
    >>> make_target(1).device.name
    'rtr001'
"""

import asyncio
import itertools
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from uuid import UUID

from tests.unit.scheduler.sched_simloop import START, run_virtual
from vakr.domain.enums import AdminState, ManagedByKind, ReachabilityStrategy
from vakr.domain.ids import CollectorId, DeviceId, WorkerId
from vakr.domain.inventory import Device
from vakr.domain.ownership import ManagedBy
from vakr.ports.clock import ManualClock
from vakr.scheduler.driver import Scheduler, SchedulerIds
from vakr.scheduler.outbox import Outbox
from vakr.scheduler.phase import PhaseRule
from vakr.scheduler.records import PollResult
from vakr.scheduler.settings import SchedulerSettings
from vakr.scheduler.steps import IcmpResult, PollContext, PollSteps, SnmpResult, StepStatus
from vakr.scheduler.targets import PollTarget

IDS = SchedulerIds(
    CollectorId(UUID("0190a3c4-0000-7000-8000-00000000c011")),
    WorkerId(UUID("0190a3c4-0000-7000-8000-00000000f0f0")),
)
RULE = PhaseRule("vakr-east")


def device_id(i: int) -> DeviceId:
    """Return a deterministic device UUID.

    Args:
        i: Device number.

    Returns:
        DeviceId: ``0190a3c4-0000-7000-8000-<i in hex>``.

    Raises:
        None
    """
    return DeviceId(UUID(f"0190a3c4-0000-7000-8000-{i:012x}"))


def make_target(
    i: int,
    interval_s: int = 60,
    strategy: ReachabilityStrategy = ReachabilityStrategy.ICMP_GATED,
    admin: AdminState = AdminState.ENABLED,
) -> PollTarget:
    """Build a poll target for device ``i``.

    Args:
        i: Device number.
        interval_s: Effective (and configured) interval.
        strategy: Reachability strategy.
        admin: Administrative state.

    Returns:
        PollTarget: The target.

    Raises:
        None
    """
    device = Device(
        device_id(i), f"rtr{i:03d}", f"198.51.100.{i % 250 + 1}", ManagedBy(ManagedByKind.API),
        START, START, admin_state=admin,
    )  # fmt: skip
    return PollTarget(device, interval_s, interval_s, strategy)


def settings(**overrides: object) -> SchedulerSettings:
    """Return scheduler settings with E.25 defaults and overrides.

    Args:
        **overrides: Field overrides such as ``workers=2``.

    Returns:
        SchedulerSettings: The settings.

    Raises:
        None
    """
    return SchedulerSettings(RULE, **overrides)  # ty: ignore[invalid-argument-type]


@dataclass
class Behaviour:
    """How one device answers.

    Attributes:
        icmp_s: ICMP step latency.
        replies: ICMP replies out of 5.
        stage1_s: Stage 1 latency.
        stage1: Stage 1 status.
        up_up: Interfaces Stage 1 reports up/up.
        stage2_s: Stage 2 latency.
        stage2: Stage 2 status.
        raise_in: Step name that raises a collector-side error.
    """

    icmp_s: float = 1.0
    replies: int = 5
    stage1_s: float = 0.1
    stage1: StepStatus = StepStatus.OK
    up_up: int = 4
    stage2_s: float = 0.3
    stage2: StepStatus = StepStatus.OK
    raise_in: str = ""


@dataclass(frozen=True)
class Call:
    """One step invocation.

    Attributes:
        step: ``icmp``, ``stage1``, or ``stage2``.
        device: The device.
        poll: The poll.
        start: Virtual monotonic start.
        end: Virtual monotonic end, or ``None`` if it never finished.
        fallback: Stage 1 fallback flag.
    """

    step: str
    device: DeviceId
    poll: UUID
    start: float
    end: float | None
    fallback: bool = False


@dataclass
class ScriptedSteps:
    """Both step protocols, driven by per-device behaviours.

    Attributes:
        clock: Virtual clock, for call timing.
        behaviours: Per-device behaviour; others use ``default``.
        default: Behaviour of unlisted devices.
        calls: Every finished or cancelled step call, in end order.
        stage2_inputs: ``(poll, stage1 result)`` pairs seen by Stage 2.
        pick: Optional hook returning a behaviour per call (load tests).
    """

    clock: ManualClock
    behaviours: dict[DeviceId, Behaviour] = field(default_factory=dict)
    default: Behaviour = field(default_factory=Behaviour)
    calls: list[Call] = field(default_factory=list)
    stage2_inputs: list[tuple[UUID, SnmpResult]] = field(default_factory=list)
    pick: Callable[[DeviceId], Behaviour] | None = None

    def behaviour(self, device: DeviceId) -> Behaviour:
        """Return the behaviour for one call.

        Args:
            device: The device.

        Returns:
            Behaviour: Its behaviour.

        Raises:
            None
        """
        if self.pick is not None:
            return self.pick(device)
        return self.behaviours.get(device, self.default)

    async def _step(
        self, name: str, ctx: PollContext, delay: float, fallback: bool = False
    ) -> None:
        """Sleep for a step's latency, logging the call, and raise if scripted.

        Args:
            name: Step name.
            ctx: The poll.
            delay: Latency in seconds.
            fallback: Stage 1 fallback flag, for the log.

        Returns:
            None

        Raises:
            OSError: When the behaviour scripts a collector-side error here.
        """
        start, device = self.clock.monotonic(), ctx.target.device_id
        try:
            await asyncio.sleep(delay)
        finally:
            ended = self.clock.monotonic()
            self.calls.append(Call(name, device, ctx.poll_id, start, ended, fallback))
        if self.behaviours.get(device, self.default).raise_in == name:
            raise OSError("scripted collector-side failure")

    async def icmp(self, ctx: PollContext) -> IcmpResult:
        """Run the scripted ICMP step.

        Args:
            ctx: The poll.

        Returns:
            IcmpResult: The scripted reply count.

        Raises:
            OSError: When scripted.
        """
        b = self.behaviour(ctx.target.device_id)
        await self._step("icmp", ctx, b.icmp_s)
        return IcmpResult(b.replies, 5, observations=(("icmp", ctx.poll_id),))

    async def stage1(self, ctx: PollContext, fallback: bool) -> SnmpResult:
        """Run the scripted Stage 1.

        Args:
            ctx: The poll.
            fallback: Single low-cost attempt flag.

        Returns:
            SnmpResult: The scripted status and up/up count.

        Raises:
            OSError: When scripted.
        """
        b = self.behaviour(ctx.target.device_id)
        await self._step("stage1", ctx, b.stage1_s, fallback)
        up_up = b.up_up if b.stage1 is not StepStatus.NO_RESPONSE else 0
        return SnmpResult(b.stage1, up_up, detail=ctx.poll_id, observations=(("s1", ctx.poll_id),))

    async def stage2(self, ctx: PollContext, stage1: SnmpResult) -> SnmpResult:
        """Run the scripted Stage 2.

        Args:
            ctx: The poll.
            stage1: This poll's Stage 1 result.

        Returns:
            SnmpResult: The scripted status.

        Raises:
            OSError: When scripted.
        """
        self.stage2_inputs.append((ctx.poll_id, stage1))
        b = self.behaviour(ctx.target.device_id)
        await self._step("stage2", ctx, b.stage2_s)
        return SnmpResult(b.stage2, observations=(("s2", ctx.poll_id),))


type Action = tuple[float, Callable[[Scheduler], object]]


async def _harness(
    scheduler: Scheduler, span_s: float, actions: Sequence[Action], records: list[PollResult]
) -> None:
    """Run the scheduler and a consumer, apply actions, then stop and drain.

    Args:
        scheduler: The scheduler, already planned.
        span_s: Virtual seconds before ``stop()``.
        actions: ``(at_seconds, callback)`` pairs, applied in order.
        records: Receives every record.

    Returns:
        None

    Raises:
        None
    """
    box = scheduler.sink
    assert isinstance(box, Outbox)

    async def consume() -> None:
        """Move records from the outbox into ``records`` forever.

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
    runner = asyncio.create_task(scheduler.run())
    elapsed = 0.0
    for at, action in sorted(actions, key=lambda pair: pair[0]):
        await asyncio.sleep(at - elapsed)
        elapsed = at
        action(scheduler)
    await asyncio.sleep(span_s - elapsed)
    scheduler.stop()
    await runner
    while (item := box.get_nowait()) is not None:
        records.append(item)
    consumer.cancel()


def run_for(
    clock: ManualClock, scheduler: Scheduler, span_s: float, actions: Sequence[Action] = ()
) -> list[PollResult]:
    """Run a planned scheduler in virtual time and return its records.

    Args:
        clock: The virtual clock the scheduler uses.
        scheduler: The scheduler, already planned.
        span_s: Virtual seconds before ``stop()``.
        actions: ``(at_seconds, callback)`` pairs.

    Returns:
        list[PollResult]: Every record, in emission order.

    Raises:
        None
    """
    records: list[PollResult] = []
    run_virtual(clock, _harness(scheduler, span_s, actions, records))
    return records


def first_due_s(scheduler: Scheduler, i: int) -> float:
    """Return seconds from now until device ``i``'s next slot.

    Args:
        scheduler: A planned scheduler.
        i: Device number.

    Returns:
        float: Seconds on the plan timeline.

    Raises:
        None
    """
    d = scheduler._dispatch
    slot = d.plan.next_slot_us(device_id(i))
    assert slot is not None
    return (slot - d.timeline.now_us()) / 1e6


def slots_by_device(records: Sequence[PollResult]) -> dict[DeviceId, list[int]]:
    """Group record slot times by device, in emission order.

    Args:
        records: Scheduler records.

    Returns:
        dict[DeviceId, list[int]]: Slot times per device.

    Raises:
        None
    """
    out: dict[DeviceId, list[int]] = {}
    for r in records:
        out.setdefault(r.poll.device_id, []).append(r.slot_us)
    return out


def assert_every_slot_recorded(records: Sequence[PollResult], interval_s: int = 60) -> None:
    """Assert each device has one record per slot: no gap, no duplicate (invariant 10).

    Args:
        records: Scheduler records.
        interval_s: Expected spacing.

    Returns:
        None

    Raises:
        AssertionError: If any device skipped or repeated a slot.
    """
    for device, slots in slots_by_device(records).items():
        ordered = sorted(slots)
        assert len(set(ordered)) == len(ordered), f"duplicate slot for {device}"
        gaps = {b - a for a, b in itertools.pairwise(ordered)}
        assert gaps <= {interval_s * 1_000_000}, f"unrecorded slot for {device}: {gaps}"


def new_scheduler(
    clock: ManualClock, steps: ScriptedSteps, targets: Sequence[PollTarget], **overrides: object
) -> Scheduler:
    """Build and plan a scheduler over scripted steps.

    Args:
        clock: Virtual clock.
        steps: Scripted steps.
        targets: Devices to plan.
        **overrides: ``SchedulerSettings`` overrides.

    Returns:
        Scheduler: Planned, not running.

    Raises:
        None
    """
    scheduler = Scheduler(clock, settings(**overrides), IDS, PollSteps(steps, steps))
    scheduler.replan(targets)
    return scheduler
