"""Run one device poll through the state machine (9.2, SCHED-009, invariant 4).

``run_poll`` asks ``vakr.scheduler.machine`` for each next phase and calls
the matching step. Progress is written to a ``PollProgress`` the driver
owns, so when a poll is cut short by its deadline or by shutdown, every
step that already finished, and every observation it produced, is still
there to record: a later failure never discards earlier evidence
(invariant 4, 9.2 "A stage failure shall not discard successful
earlier-stage evidence").

A step that raises is recorded as ``ERROR`` with the exception *type* only
(messages can quote addresses or worse), and the poll gains
``COLLECTOR_DEGRADED``, so collector trouble reads as unknown, not as a
device fault (invariant 2). Cancellation is not caught here: the driver
turns it into a ``DEADLINE`` or ``CANCELLED`` step.

Dependencies:
    vakr.scheduler.machine, steps: The table and the step contract.

Safety:
    Holds no resources; each step owns and closes its own sockets.

Example:
    >>> p = PollProgress()
    >>> p.begin(Phase.ICMP, 0); p.end(StepStatus.OK, 1_500_000)
    >>> [(s.phase.value, s.status.value) for s in p.steps]
    [('icmp', 'ok')]
"""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from vakr.domain.quality import Quality
from vakr.scheduler.machine import SKIPPED_BY, Event, Phase, icmp_event, stage1_event, transition
from vakr.scheduler.steps import PollContext, PollSteps, SnmpResult, StepStatus

type NowUs = Callable[[], int]


@dataclass(frozen=True, slots=True)
class StepRecord:
    """How one step of a poll went.

    Attributes:
        phase: The step.
        status: How it ended.
        started_us: Start on the plan timeline.
        ended_us: End on the plan timeline.
        error: Exception type name for ``ERROR``, else empty.
    """

    phase: Phase
    status: StepStatus
    started_us: int
    ended_us: int
    error: str = ""


@dataclass(slots=True)
class PollProgress:
    """Everything a poll has produced so far.

    Attributes:
        steps: Finished steps, in order.
        observations: Observations from every finished step.
        quality: Flags gathered from the steps.
        current: The step in flight, if any.
        current_since_us: When the step in flight began.
    """

    steps: list[StepRecord] = field(default_factory=list)
    observations: list[object] = field(default_factory=list)
    quality: Quality = Quality.NONE
    current: Phase | None = None
    current_since_us: int = 0

    def begin(self, phase: Phase, now_us: int) -> None:
        """Mark a step as in flight.

        Args:
            phase: The step.
            now_us: Plan-timeline time.

        Returns:
            None

        Raises:
            None
        """
        self.current, self.current_since_us = phase, now_us

    def end(self, status: StepStatus, now_us: int, error: str = "") -> None:
        """Finish the step in flight.

        Args:
            status: How it ended.
            now_us: Plan-timeline time.
            error: Exception type name for ``ERROR``.

        Returns:
            None

        Raises:
            None
        """
        if self.current is not None:
            record = StepRecord(self.current, status, self.current_since_us, now_us, error)
            self.steps.append(record)
            self.current = None

    def skip(self, phase: Phase, now_us: int) -> None:
        """Record a step the strategy or gate left out.

        Args:
            phase: The skipped step.
            now_us: Plan-timeline time.

        Returns:
            None

        Raises:
            None
        """
        self.steps.append(StepRecord(phase, StepStatus.SKIPPED, now_us, now_us))

    def keep(self, observations: tuple[object, ...], quality: Quality) -> None:
        """Keep a step's observations and flags.

        Args:
            observations: What the step produced.
            quality: Its flags.

        Returns:
            None

        Raises:
            None
        """
        self.observations.extend(observations)
        if quality:  # Flag arithmetic is slow and most steps add none
            self.quality |= quality


async def _guarded[T](call: Awaitable[T], progress: PollProgress, now_us: NowUs) -> T | None:
    """Await a step, recording a collector-side exception as ``ERROR``.

    Args:
        call: The step coroutine.
        progress: The poll's progress.
        now_us: Plan-timeline clock.

    Returns:
        T | None: The step result, or ``None`` after an error.

    Raises:
        None
    """
    try:
        return await call
    except Exception as exc:  # noqa: BLE001 - any step bug is collector trouble, never target state
        progress.end(StepStatus.ERROR, now_us(), type(exc).__name__)
        progress.quality |= Quality.COLLECTOR_DEGRADED
        return None


async def _icmp_phase(
    ctx: PollContext, steps: PollSteps, progress: PollProgress, now_us: NowUs
) -> Event:
    """Run the ICMP step and report the gating event.

    Args:
        ctx: The poll.
        steps: Step implementations.
        progress: The poll's progress.
        now_us: Plan-timeline clock.

    Returns:
        Event: A ``GATE_*`` event, or ``ICMP_FAILED`` after a collector error.

    Raises:
        None
    """
    icmp = await _guarded(steps.icmp.icmp(ctx), progress, now_us)
    if icmp is None:
        return Event.ICMP_FAILED
    progress.keep(icmp.observations, icmp.quality)
    progress.end(StepStatus.OK, now_us())  # 0/5 replies is a measurement, not a failure
    return icmp_event(ctx.target.strategy, icmp.replies)


async def _snmp_phase(
    phase: Phase, ctx: PollContext, steps: PollSteps, progress: PollProgress, now_us: NowUs,
    stage1: SnmpResult | None,
) -> tuple[Event, SnmpResult | None]:  # fmt: skip
    """Run Stage 1, the fallback attempt, or (given a Stage 1 result) Stage 2.

    Args:
        phase: STAGE1, FALLBACK, or STAGE2.
        ctx: The poll.
        steps: Step implementations.
        progress: The poll's progress.
        now_us: Plan-timeline clock.
        stage1: This poll's Stage 1 result when running Stage 2, else ``None``.

    Returns:
        tuple[Event, SnmpResult | None]: The event, and the Stage 1 result
        to carry into Stage 2.

    Raises:
        None
    """
    if stage1 is None:
        call = steps.snmp.stage1(ctx, fallback=phase is Phase.FALLBACK)
    else:
        call = steps.snmp.stage2(ctx, stage1)  # requests built from *this* Stage 1 (ADR-005)
    result = await _guarded(call, progress, now_us)
    if result is not None:
        progress.keep(result.observations, result.quality)
        progress.end(result.status, now_us())
    if stage1 is not None:
        return Event.FINISHED, stage1
    if result is None:
        return Event.NO_ANSWER, None
    return stage1_event(result.answered, result.up_up), result


async def run_poll(
    ctx: PollContext, steps: PollSteps, progress: PollProgress, now_us: NowUs
) -> None:
    """Drive one poll from START to DONE.

    Args:
        ctx: The poll.
        steps: Step implementations.
        progress: Written as each step finishes.
        now_us: Plan-timeline clock.

    Returns:
        None

    Raises:
        asyncio.CancelledError: When the driver cancels the poll; progress
            keeps everything finished so far.
    """
    strategy = ctx.target.strategy
    phase = transition(strategy, Phase.START, Event.BEGIN)
    stage1: SnmpResult | None = None
    while phase is not Phase.DONE:
        progress.begin(phase, now_us())
        if phase is Phase.ICMP:
            event = await _icmp_phase(ctx, steps, progress, now_us)
        else:
            carried = stage1 if phase is Phase.STAGE2 else None
            event, stage1 = await _snmp_phase(phase, ctx, steps, progress, now_us, carried)
        if (skipped := SKIPPED_BY.get((strategy, phase, event))) is not None:
            progress.skip(skipped, now_us())
        phase = transition(strategy, phase, event)
