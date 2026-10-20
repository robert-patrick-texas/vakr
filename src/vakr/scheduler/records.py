"""Poll records: what every scheduled occurrence becomes (9.1, SCHED-012, SCHED-017, invariant 2).

Every slot the plan produces ends as exactly one ``PollResult`` wrapping a
``DevicePoll``. A finished poll's execution state is derived from its steps:

* ``COMPLETE``: every step the strategy and gate called for answered fully.
  ICMP with 0 replies counts: it is a measurement of an unreachable device.
* ``PARTIAL``: some step produced evidence and a later one did not finish
  cleanly (partial answer, no response, error, deadline, or shutdown).
* ``FAILED``: no step produced evidence, for example ``snmp_only`` with a
  Stage 1 timeout.
* ``CANCELLED``: shutdown cut the poll short (16.4); the evidence its
  finished steps produced is kept, and it reads as unknown, never as down.

An occurrence that never started is ``MISSED_OVERRUN`` (the previous poll
still ran, SCHED-012) or ``NOT_ATTEMPTED`` (capacity, stall, or shutdown,
SCHED-017), always with the ``NOT_ATTEMPTED`` flag and no observations, so
it reads as unknown and never as a device outage. Capacity misses also carry
``COLLECTOR_DEGRADED``; so do polls hit by a collector-side step error,
because the collector, not the target, cut them short.

Dependencies:
    vakr.domain.polls: ``DevicePoll``.
    vakr.scheduler.admission, runner, steps: Misses, step records, statuses.

Safety:
    Records carry IDs, times, states, and flags; no configuration or secrets.

Example:
    >>> from vakr.scheduler.machine import Phase
    >>> execution_of([StepRecord(Phase.ICMP, StepStatus.OK, 0, 1)]).value
    'complete'
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta

from vakr.domain.enums import PollExecution
from vakr.domain.ids import CollectorId, ObservationId, PollId, WorkerId, new_id
from vakr.domain.polls import DevicePoll
from vakr.domain.quality import Quality
from vakr.scheduler.admission import CAPACITY_REASONS, Miss, MissReason
from vakr.scheduler.runner import PollProgress, StepRecord
from vakr.scheduler.steps import ANSWERED, PollContext, StepStatus
from vakr.scheduler.targets import PollTarget


@dataclass(frozen=True, slots=True)
class PollResult:
    """One scheduled occurrence, as handed to the output queue.

    Attributes:
        poll: The ``DevicePoll`` record.
        slot_us: Intended start on the plan timeline (monotonic-based).
        steps: Step records, empty when the poll never started.
        observations: Observations the steps produced, in step order.
        lateness_us: Start minus slot, for polls that started.
        duration_us: Completion minus start, for polls that finished.
        miss: Why the occurrence did not run, for misses.
    """

    poll: DevicePoll
    slot_us: int
    steps: tuple[StepRecord, ...] = ()
    observations: tuple[object, ...] = ()
    lateness_us: int | None = None
    duration_us: int | None = None
    miss: MissReason | None = None


@dataclass(frozen=True, slots=True)
class StartedPoll:
    """Facts fixed when a poll started.

    Attributes:
        ctx: The poll context handed to the steps.
        slot_us: The slot it serves.
        start_us: Start on the plan timeline.
        started_at: Wall-clock UTC start.
        worker_id: The worker running it.
    """

    ctx: PollContext
    slot_us: int
    start_us: int
    started_at: datetime
    worker_id: WorkerId


@dataclass(frozen=True, slots=True)
class SchedulerIds:
    """Identities stamped on every record.

    Attributes:
        collector_id: This collector (STATE-004).
        worker_id: This worker process; the MVP runs one per collector (6.2).
    """

    collector_id: CollectorId
    worker_id: WorkerId


@dataclass(slots=True)
class Job:
    """One poll in flight.

    Attributes:
        started: Facts fixed at start.
        progress: Steps and observations so far.
        result: The record, once built.
        emitted: True once the sink accepted the record.
    """

    started: StartedPoll
    progress: PollProgress
    result: PollResult | None = None
    emitted: bool = False


def execution_of(steps: Sequence[StepRecord]) -> PollExecution:
    """Derive a finished poll's execution state from its steps.

    Args:
        steps: The poll's step records.

    Returns:
        PollExecution: ``COMPLETE``, ``PARTIAL``, or ``FAILED``.

    Raises:
        None
    """
    ran = [step.status for step in steps if step.status is not StepStatus.SKIPPED]
    if ran and all(status is StepStatus.OK for status in ran):
        return PollExecution.COMPLETE
    if any(status in ANSWERED for status in ran):
        return PollExecution.PARTIAL
    return PollExecution.FAILED


def _base_quality(target: PollTarget) -> Quality:
    """Return flags every record of a target carries.

    Args:
        target: The device.

    Returns:
        Quality: ``MAINTENANCE`` for devices in maintenance (E.2 row 2).

    Raises:
        None
    """
    return Quality.MAINTENANCE if target.maintenance else Quality.NONE


def finished_result(started: StartedPoll, progress: PollProgress, end_us: int) -> PollResult:
    """Build the record of a poll that ran, however it ended.

    The completion time is the wall-clock start plus the monotonic duration,
    so a wall-clock step during the poll cannot make it end before it began.

    Args:
        started: Facts fixed at start.
        progress: Everything the poll produced.
        end_us: End on the plan timeline.

    Returns:
        PollResult: The record with steps and observations.

    Raises:
        None
    """
    ctx, steps = started.ctx, tuple(progress.steps)
    quality = progress.quality
    if ctx.target.maintenance:
        quality |= Quality.MAINTENANCE
    cancelled = any(step.status is StepStatus.CANCELLED for step in steps)
    execution = PollExecution.CANCELLED if cancelled else execution_of(steps)
    duration = max(0, end_us - started.start_us)
    poll = DevicePoll(
        new_id(ObservationId), ctx.poll_id, ctx.collector_id, ctx.target.device_id,
        ctx.scheduled_at, ctx.target.configured_interval_s, ctx.target.effective_interval_s,
        ctx.target.strategy, execution, quality, started.worker_id,
        started.started_at, started.started_at + timedelta(microseconds=duration),
    )  # fmt: skip
    lateness = started.start_us - started.slot_us
    return PollResult(
        poll, started.slot_us, steps, tuple(progress.observations), lateness, duration
    )


def missed_result(miss: Miss, collector_id: CollectorId, scheduled_at: datetime) -> PollResult:
    """Build the record of an occurrence that never started.

    Args:
        miss: The occurrence and why it did not run.
        collector_id: This collector.
        scheduled_at: The slot's UTC label.

    Returns:
        PollResult: A ``MISSED_OVERRUN`` or ``NOT_ATTEMPTED`` record.

    Raises:
        None
    """
    target = miss.slot.target
    overrun = miss.reason is MissReason.OVERRUN
    quality = Quality.NOT_ATTEMPTED | _base_quality(target)
    if miss.reason in CAPACITY_REASONS:
        quality |= Quality.COLLECTOR_DEGRADED
    poll = DevicePoll(
        new_id(ObservationId), new_id(PollId), collector_id, target.device_id, scheduled_at,
        target.configured_interval_s, target.effective_interval_s, target.strategy,
        PollExecution.MISSED_OVERRUN if overrun else PollExecution.NOT_ATTEMPTED, quality,
    )  # fmt: skip
    return PollResult(poll, miss.slot.due_us, miss=miss.reason)
