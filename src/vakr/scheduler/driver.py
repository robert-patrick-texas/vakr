"""The asyncio scheduler: plan, run, re-plan, and shut down gracefully (9.9, 16.4, SVC-003).

``Scheduler`` is what a collector service runs. It owns the dispatcher, the
event-loop lag monitor, and the statistics, and it exposes four operations:

* ``replan(targets)``: apply an inventory or policy change. Unchanged devices
  keep their phase and next slot; new and changed ones join their own grid,
  so a reload never bursts (``vakr.scheduler.plan``).
* ``run()``: dispatch until ``stop()``, then drain.
* ``stop()``: begin graceful shutdown (16.4): stop admitting, give active
  polls ``shutdown_grace`` to finish, cancel the rest, and record every one
  of them with whatever evidence their finished steps produced; then record
  waiting slots, and slots that came due during the drain, as not
  attempted. ``run()`` returns once every record is in the sink.
* ``snapshot()``: self-observability (OBS-001..004, ``vakr.scheduler.health``).

Monotonic time drives every wait and deadline; UTC is only for labels
(SCHED-011, TIME-001). Nothing here blocks the event loop.

Dependencies:
    asyncio: Tasks and waits.
    vakr.scheduler.dispatch, health, lag, outbox, stats: The parts.

Safety:
    The consumer must keep draining the sink until ``run()`` returns:
    records are never dropped, so a sink that stops accepting stalls the
    drain rather than losing data (invariant 8).

Example:
    >>> from datetime import UTC, datetime
    >>> from vakr.domain.ids import CollectorId, WorkerId, new_id
    >>> from vakr.ports.clock import ManualClock
    >>> from vakr.scheduler.phase import PhaseRule
    >>> from vakr.scheduler.settings import SchedulerSettings
    >>> from vakr.scheduler.steps import PollSteps
    >>> clock = ManualClock(datetime(2026, 10, 20, tzinfo=UTC))
    >>> ids = SchedulerIds(new_id(CollectorId), new_id(WorkerId))
    >>> steps = PollSteps(icmp=None, snmp=None)  # real steps come from the adapters
    >>> s = Scheduler(clock, SchedulerSettings(PhaseRule("vakr-east")), ids, steps)
    >>> s.snapshot().devices
    0
"""

import asyncio
from collections.abc import Callable, Iterable
from dataclasses import replace

from vakr.ports.clock import Clock
from vakr.scheduler.admission import Miss, MissReason
from vakr.scheduler.dispatch import Dispatcher
from vakr.scheduler.health import SchedulerSnapshot
from vakr.scheduler.lag import LoopLagMonitor
from vakr.scheduler.outbox import Outbox, PollSink
from vakr.scheduler.plan import ReplanSummary
from vakr.scheduler.records import Job, SchedulerIds, finished_result
from vakr.scheduler.settings import SchedulerSettings
from vakr.scheduler.stats import SchedulerStats
from vakr.scheduler.steps import PollSteps
from vakr.scheduler.targets import PollTarget

__all__ = ["Scheduler", "SchedulerIds"]


class Scheduler:
    """Runs the poll plan on the event loop.

    Attributes:
        sink: Where records go; an ``Outbox`` unless one was supplied.
        stats: Counters and latency windows.
    """

    def __init__(
        self, clock: Clock, settings: SchedulerSettings, ids: SchedulerIds,
        steps: PollSteps, sink: PollSink | None = None, admits: Callable[[], bool] | None = None,
    ) -> None:  # fmt: skip
        """Create a scheduler with an empty plan.

        Args:
            clock: Monotonic and UTC time; the loop's time must be the same
                monotonic clock (``SystemClock`` with the default loop).
            settings: Scheduler settings.
            ids: Collector and worker identities.
            steps: ICMP and SNMP step implementations.
            sink: Record sink; ``None`` creates an ``Outbox`` bounded by
                ``settings.queue_capacity``.
            admits: Whether storage takes new work, such as
                ``Spool.admits_new_work``; while it is False, due slots are
                recorded as not attempted (6.4). ``None`` always admits.

        Returns:
            None

        Raises:
            None
        """
        self._settings = settings
        self.sink: PollSink = sink if sink is not None else Outbox(settings.queue_capacity, clock)
        self.stats = SchedulerStats(settings.late_after_us)
        self._lag = LoopLagMonitor(clock)
        self._dispatch = Dispatcher(clock, settings, ids, steps, self.sink, self.stats)
        if admits is not None:
            self._dispatch.admits = admits

    def replan(self, targets: Iterable[PollTarget]) -> ReplanSummary:
        """Apply a new set of poll targets.

        Plan just before ``run()``: first slots are computed from now, and a
        slot more than ``max_start_delay`` old when dispatch begins is
        recorded as not attempted.

        Args:
            targets: Every device to schedule (enabled and maintenance).

        Returns:
            ReplanSummary: What changed.

        Raises:
            None
        """
        d = self._dispatch
        summary = d.plan.replan(targets, d.timeline.now_us())
        d.gate.forget(summary.removed)
        d.wake()
        return summary

    def stop(self) -> None:
        """Begin graceful shutdown; ``run()`` returns when it completes.

        Args:
            None

        Returns:
            None

        Raises:
            None
        """
        self._dispatch.stopping = True
        self._dispatch.wake()

    async def run(self) -> None:
        """Dispatch until ``stop()``, then drain (16.4 steps 1-5).

        Args:
            None

        Returns:
            None

        Raises:
            None
        """
        d = self._dispatch
        lag = asyncio.create_task(self._lag.run(), name="event-loop lag")
        try:
            while not d.stopping:
                await d.tick()
                if not d.stopping:
                    await d.pause()
            await self._drain()
        finally:
            lag.cancel()
            await asyncio.wait([lag])

    async def _drain(self) -> None:
        """Let active polls finish, cancel the rest, and record every occurrence.

        In-flight polls get the grace period first, because their results
        are the valuable ones; waiting slots and slots that came due during
        the drain are recorded as ``DRAINING`` afterwards.

        Args:
            None

        Returns:
            None

        Raises:
            None
        """
        d = self._dispatch
        waiting = d.gate.drain()
        jobs = dict(d.jobs)
        if jobs:
            _, late = await asyncio.wait(jobs, timeout=self._settings.shutdown_grace_s)
            for task in late:
                task.cancel()
            if late:
                await asyncio.wait(late)
            for task in late:
                await self._record_cancelled(jobs[task])
        for miss in waiting:
            await d.emit_miss(miss)
        now = d.timeline.now_us()
        while (slot := d.plan.pop_due(now)) is not None:
            await d.emit_miss(Miss(slot, MissReason.DRAINING))

    async def _record_cancelled(self, job: Job) -> None:
        """Emit the record of a poll that shutdown cancelled.

        A poll cancelled while its finished record waited for the sink keeps
        that record; any other gets one built from its progress.

        Args:
            job: The cancelled poll; it never completed its hand-off.

        Returns:
            None

        Raises:
            None
        """
        d = self._dispatch
        result = job.result or finished_result(job.started, job.progress, d.timeline.now_us())
        await d.emit(result)
        job.emitted = True

    def snapshot(self, *, close_period: bool = False) -> SchedulerSnapshot:
        """Return a self-observability snapshot (OBS-001..004).

        Args:
            close_period: True to end the counting period, as a health
                observation does; False to peek.

        Returns:
            SchedulerSnapshot: Current values.

        Raises:
            None
        """
        d, stats = self._dispatch, self.stats
        period = stats.take_period() if close_period else replace(stats.period)
        lag = self._lag.take_period_max() if close_period else self._lag.peek_period_max()
        box = self.sink if isinstance(self.sink, Outbox) else None
        return SchedulerSnapshot(
            devices=len(d.plan), workers=d.gate.capacity, busy_workers=d.gate.busy_workers,
            waiting=d.gate.waiting,
            queue_depth=None if box is None else box.depth,
            queue_capacity=None if box is None else box.capacity,
            queue_full_waits=0 if box is None else box.full_waits,
            queue_blocked_s=0.0 if box is None else box.blocked_s,
            producers_blocked=0 if box is None else box.producers_waiting,
            loop_lag_us=lag, clock_steps=d.timeline.clock_steps,
            wall_offset_us=d.timeline.wall_offset_us(),
            lateness=stats.lateness.percentiles(), duration=stats.duration.percentiles(),
            completion=stats.completion.percentiles(),
            period=period, lifetime=replace(stats.lifetime), draining=d.stopping,
        )  # fmt: skip
