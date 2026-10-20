"""Admission and poll records: no overlap, bounded workers, recorded misses (SCHED-T05, -T10).

Every way an occurrence can fail to run is exercised at the unit level, and
each record is checked to read as unknown (``NOT_ATTEMPTED``) rather than
as a target failure (SCHED-017, OBS-003, invariant 2).

Dependencies:
    pytest: Parametrisation.
    vakr.scheduler.admission, records: The code under test.

Example:
    $ uv run pytest tests/unit/scheduler/test_sched_admission.py
"""

from datetime import UTC, datetime

import pytest

from tests.unit.scheduler.sched_fakes import IDS, make_target
from vakr.domain.enums import AdminState, PollExecution
from vakr.domain.ids import PollId, new_id
from vakr.domain.quality import UNKNOWN_CONTEXT, Quality
from vakr.scheduler.admission import Admission, Miss, MissReason
from vakr.scheduler.machine import Phase
from vakr.scheduler.plan import Slot
from vakr.scheduler.records import StartedPoll, execution_of, finished_result, missed_result
from vakr.scheduler.runner import PollProgress, StepRecord
from vakr.scheduler.steps import PollContext, StepStatus

S = 1_000_000
WHEN = datetime(2026, 10, 20, 12, tzinfo=UTC)
OK, PARTIAL, NO, SKIP, ERR, DEAD, CANC = (
    StepStatus.OK, StepStatus.PARTIAL, StepStatus.NO_RESPONSE, StepStatus.SKIPPED,
    StepStatus.ERROR, StepStatus.DEADLINE, StepStatus.CANCELLED,
)  # fmt: skip


def _slot(i: int, due_s: int) -> Slot:
    """Build a slot for device ``i``.

    Args:
        i: Device number.
        due_s: Due time in seconds.

    Returns:
        Slot: The slot.

    Raises:
        None
    """
    return Slot(make_target(i), due_s * S)


def test_workers_bound_concurrency_and_start_in_fifo_order() -> None:
    """SCHED-016: at most ``workers`` sessions; waiting slots start oldest first.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    gate = Admission(workers=2)
    for i in range(4):
        assert gate.offer(_slot(i, i), i * S, 30 * S) is None
    started = [gate.start_next(), gate.start_next(), gate.start_next()]
    assert [s.due_us for s in started if s] == [0, S]
    assert started[2] is None
    assert gate.free_workers == 0
    assert gate.waiting == 2
    first = started[0]
    assert first is not None
    gate.end_session(first.target.device_id)
    assert gate.start_next() is None  # the worker is held until the record is handed off
    gate.release_worker()
    nxt = gate.start_next()
    assert nxt is not None
    assert nxt.due_us == 2 * S
    assert gate.busy_workers == 2
    with pytest.raises(ValueError, match="at least one"):
        Admission(workers=0)


def test_slot_of_a_device_still_being_polled_is_an_overrun() -> None:
    """SCHED-T05, SCHED-012: no second session; the occurrence is MISSED_OVERRUN.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    gate = Admission(workers=4)
    gate.offer(_slot(1, 0), 0, 30 * S)
    assert gate.start_next() is not None
    miss = gate.offer(_slot(1, 60), 60 * S, 30 * S)
    assert miss == Miss(_slot(1, 60), MissReason.OVERRUN)
    record = missed_result(miss, IDS.collector_id, WHEN)
    assert record.poll.execution is PollExecution.MISSED_OVERRUN
    assert record.poll.quality == Quality.NOT_ATTEMPTED  # target-specific: not collector-degraded
    assert record.observations == ()
    assert record.poll.started_at is None


def test_waiting_too_long_superseded_and_late_dispatch_are_not_attempted() -> None:
    """SCHED-T10, SCHED-017: capacity misses read as unknown and flag the collector.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    gate = Admission(workers=1)
    gate.offer(_slot(1, 0), 0, 30 * S)
    gate.start_next()
    assert gate.offer(_slot(2, 0), 0, 30 * S) is None
    assert gate.next_expiry_us(30 * S) == 30 * S + 1
    assert gate.expire(30 * S, 30 * S) == []
    expired = gate.expire(30 * S + 1, 30 * S)
    assert expired == [Miss(_slot(2, 0), MissReason.NO_WORKER)]
    assert gate.offer(_slot(3, 0), 0, 30 * S) is None
    superseded = gate.offer(_slot(3, 60), 60 * S, 30 * S)
    assert superseded == Miss(_slot(3, 0), MissReason.SUPERSEDED)
    late = gate.offer(_slot(4, 0), 31 * S, 30 * S)
    assert late == Miss(_slot(4, 0), MissReason.LATE_DISPATCH)
    for miss in (*expired, superseded, late):
        poll = missed_result(miss, IDS.collector_id, WHEN).poll
        assert poll.execution is PollExecution.NOT_ATTEMPTED
        assert poll.quality == Quality.NOT_ATTEMPTED | Quality.COLLECTOR_DEGRADED
        assert poll.quality & UNKNOWN_CONTEXT  # E.2 row 3: Unknown, never Down


def test_forget_and_drain_empty_the_wait_queue() -> None:
    """Removed devices leave silently (9.2); shutdown records the rest as DRAINING.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    gate = Admission(workers=1)
    for i in range(3):
        gate.offer(_slot(i, i), i * S, 30 * S)
    gate.forget((make_target(1).device_id,))
    drained = gate.drain()
    assert [m.reason for m in drained] == [MissReason.DRAINING] * 2
    assert gate.waiting == 0
    assert gate.next_expiry_us(30 * S) is None
    poll = missed_result(drained[0], IDS.collector_id, WHEN).poll
    assert poll.quality == Quality.NOT_ATTEMPTED  # shutdown is not capacity trouble


@pytest.mark.parametrize(
    ("statuses", "expected"),
    [
        ((OK, SKIP), PollExecution.COMPLETE),  # icmp_gated 0-1 replies: SNMP skipped by design
        ((OK, OK, OK), PollExecution.COMPLETE),
        ((OK, OK, PARTIAL), PollExecution.PARTIAL),
        ((OK, NO, SKIP), PollExecution.PARTIAL),
        ((OK, OK, DEAD), PollExecution.PARTIAL),
        ((OK, CANC), PollExecution.PARTIAL),
        ((NO, SKIP), PollExecution.FAILED),  # snmp_only with a Stage 1 timeout
        ((ERR, SKIP), PollExecution.FAILED),
        ((DEAD,), PollExecution.FAILED),
        ((), PollExecution.FAILED),
    ],
)
def test_execution_state_follows_the_steps(
    statuses: tuple[StepStatus, ...], expected: PollExecution
) -> None:
    """Invariant 4: earlier evidence keeps a poll PARTIAL rather than FAILED.

    Args:
        statuses: Step statuses in order.
        expected: Execution state.

    Returns:
        None

    Raises:
        None
    """
    steps = [StepRecord(Phase.ICMP, status, 0, 1) for status in statuses]
    assert execution_of(steps) is expected


def test_finished_record_times_and_flags() -> None:
    """Completion is start plus monotonic duration; maintenance and cancellation are flagged.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    target = make_target(5, admin=AdminState.MAINTENANCE)
    ctx = PollContext(new_id(PollId), IDS.collector_id, target, WHEN)
    started = StartedPoll(ctx, 100 * S, 102 * S, WHEN, IDS.worker_id)
    progress = PollProgress()
    progress.begin(Phase.ICMP, 102 * S)
    progress.keep(("icmp",), Quality.PARTIAL_LOSS)
    progress.end(OK, 104 * S)
    progress.begin(Phase.STAGE1, 104 * S)
    progress.end(CANC, 105 * S)
    result = finished_result(started, progress, 105 * S)
    assert (result.lateness_us, result.duration_us) == (2 * S, 3 * S)
    assert result.poll.completed_at is not None
    assert (result.poll.completed_at - result.poll.started_at).total_seconds() == 3  # ty: ignore[unsupported-operator]
    assert result.poll.execution is PollExecution.CANCELLED  # its own state (16.4)
    expected = Quality.PARTIAL_LOSS | Quality.MAINTENANCE
    assert result.poll.quality == expected
    assert result.observations == ("icmp",)
    assert result.poll.poll_id == ctx.poll_id
