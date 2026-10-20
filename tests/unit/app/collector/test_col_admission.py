"""Spool backpressure reaches the scheduler: EXHAUSTED pauses polling (6.4, SPOOL-T07, SCHED-T10).

The collector hands ``Spool.admits_new_work`` to the scheduler. While it is
False, every slot that comes due is recorded as ``NOT_ATTEMPTED`` with
``COLLECTOR_DEGRADED`` and reason ``STORAGE_FULL``: unknown, never a device
outage (SCHED-017, OBS-003). Polling resumes on the next slot once the spool
admits work again, and cadence is untouched.

Dependencies:
    tests.unit.scheduler: Virtual time and scripted steps.

Example:
    $ uv run pytest tests/unit/app/collector/test_col_admission.py
"""

from tests.unit.scheduler.sched_fakes import (
    IDS,
    ScriptedSteps,
    assert_every_slot_recorded,
    make_target,
    run_for,
    settings,
)
from tests.unit.scheduler.sched_simloop import new_clock
from vakr.domain.enums import PollExecution
from vakr.domain.quality import Quality
from vakr.scheduler.admission import CAPACITY_REASONS, MissReason
from vakr.scheduler.driver import Scheduler
from vakr.scheduler.steps import PollSteps


class Gate:
    """A switchable ``admits_new_work``."""

    def __init__(self) -> None:
        """Start admitting.

        Args:
            None

        Returns:
            None

        Raises:
            None
        """
        self.open = True

    def __call__(self) -> bool:
        """Report whether new work is admitted.

        Args:
            None

        Returns:
            bool: The current setting.

        Raises:
            None
        """
        return self.open

    def close(self, scheduler: Scheduler) -> None:
        """Stop admitting, as an EXHAUSTED spool does.

        Args:
            scheduler: The scheduler (the harness passes it; unused).

        Returns:
            None

        Raises:
            None
        """
        del scheduler
        self.open = False

    def reopen(self, scheduler: Scheduler) -> None:
        """Admit again.

        Args:
            scheduler: The scheduler (the harness passes it; unused).

        Returns:
            None

        Raises:
            None
        """
        del scheduler
        self.open = True


def test_exhausted_spool_records_slots_as_not_attempted_and_resumes() -> None:
    """SPOOL-T07, SCHED-T10, 6.4: no polls while EXHAUSTED; misses are unknown, not down.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    clock, gate = new_clock(), Gate()
    steps = ScriptedSteps(clock)
    scheduler = Scheduler(clock, settings(), IDS, PollSteps(steps, steps), admits=gate)
    scheduler.replan([make_target(0)])
    records = run_for(clock, scheduler, 420, [(130, gate.close), (310, gate.reopen)])
    assert_every_slot_recorded(records)
    full = [r for r in records if r.miss is MissReason.STORAGE_FULL]
    assert len(full) == 3  # the slots due while the spool was full
    for record in full:
        assert record.poll.execution is PollExecution.NOT_ATTEMPTED
        assert record.poll.quality & Quality.NOT_ATTEMPTED
        assert record.poll.quality & Quality.COLLECTOR_DEGRADED
        assert not record.observations
    assert MissReason.STORAGE_FULL in CAPACITY_REASONS
    polled = [r for r in records if r.miss is None]
    assert len(polled) >= 3  # before, and again after the spool admitted work
    assert scheduler.stats.lifetime.capacity == 3
