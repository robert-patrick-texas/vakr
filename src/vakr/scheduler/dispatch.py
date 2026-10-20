"""The dispatcher: admit due slots and run polls on the event loop (9.9, SCHED-011, SCHED-016).

Each ``tick`` reads the plan timeline (monotonic), gives up on slots that
waited too long, pops every due slot, offers it to admission control, and
starts as many waiting slots as there are free workers. ``pause`` then
sleeps until the next due slot or wait expiry, or until something wakes it
(a re-plan, a freed worker, shutdown). There are no busy loops and no
blocking calls: all waiting is asyncio sleeping (6.2).

A started poll runs as its own task under ``scheduler.poll_deadline``. When
its steps end, the device's session ends; the worker is released only after
the record is handed to the sink, so a full sink slows admission (6.4).

Dependencies:
    asyncio: Tasks, futures, timeouts.
    vakr.scheduler: Plan, admission, runner, records, timeline, stats.

Safety:
    Concurrency is bounded by ``scheduler.workers``; per-device overlap is
    impossible because admission tracks sessions (SCHED-012, SCHED-016).

Example:
    >>> import asyncio
    >>> loop = asyncio.new_event_loop()
    >>> future = loop.create_future()
    >>> _resolve(future); _resolve(future)  # waking twice is harmless
    >>> future.result() is None
    True
    >>> loop.close()
"""

import asyncio
from collections.abc import Callable

from vakr.domain.ids import PollId, new_id
from vakr.ports.clock import Clock
from vakr.scheduler.admission import Admission, Miss, MissReason
from vakr.scheduler.outbox import PollSink
from vakr.scheduler.plan import Plan, Slot
from vakr.scheduler.records import (
    Job,
    PollResult,
    SchedulerIds,
    StartedPoll,
    finished_result,
    missed_result,
)
from vakr.scheduler.runner import PollProgress, run_poll
from vakr.scheduler.settings import SchedulerSettings
from vakr.scheduler.stats import SchedulerStats
from vakr.scheduler.steps import PollContext, PollSteps, StepStatus
from vakr.scheduler.timeline import Timeline


def _resolve(future: asyncio.Future[None]) -> None:
    """Complete a pause future unless it is already done.

    Args:
        future: The dispatcher's pause future.

    Returns:
        None

    Raises:
        None
    """
    if not future.done():
        future.set_result(None)


class Dispatcher:
    """Admission and execution of due slots.

    Attributes:
        timeline: The plan timeline.
        plan: Scheduled devices.
        gate: Admission control.
        jobs: Polls in flight, by task.
        stopping: True once shutdown began; no new polls start.
        admits: False while storage refuses new work (6.4); due slots are then missed.
    """

    def __init__(
        self, clock: Clock, settings: SchedulerSettings, ids: SchedulerIds, steps: PollSteps,
        sink: PollSink, stats: SchedulerStats,
    ) -> None:  # fmt: skip
        """Create the dispatcher.

        Args:
            clock: Monotonic and UTC time.
            settings: Scheduler settings.
            ids: Collector and worker identities.
            steps: Step implementations.
            sink: Where records go.
            stats: Counters to update on every record.

        Returns:
            None

        Raises:
            None
        """
        self._clock, self._settings, self._ids = clock, settings, ids
        self._steps, self._sink, self._stats = steps, sink, stats
        self.timeline = Timeline(clock)
        self.plan = Plan(settings.rule)
        self.gate = Admission(settings.workers)
        self.jobs: dict[asyncio.Task[None], Job] = {}
        self.stopping = False
        self.admits: Callable[[], bool] = lambda: True
        self._waker: asyncio.Future[None] | None = None

    def wake(self) -> None:
        """End the current pause early.

        Args:
            None

        Returns:
            None

        Raises:
            None
        """
        if self._waker is not None:
            _resolve(self._waker)

    async def emit(self, result: PollResult) -> None:
        """Hand a record to the sink, waiting while it is full, then count it.

        Counting after the hand-off means a record whose hand-off was cut
        short by shutdown, and is emitted again by the drain, counts once.

        Args:
            result: The record.

        Returns:
            None

        Raises:
            None
        """
        await self._sink.put(result)
        self._stats.record(result)

    async def emit_miss(self, miss: Miss) -> None:
        """Record an occurrence that will not be polled.

        Args:
            miss: The occurrence and reason.

        Returns:
            None

        Raises:
            None
        """
        label = self.timeline.label(miss.slot.due_us)
        await self.emit(missed_result(miss, self._ids.collector_id, label))

    async def tick(self) -> None:
        """Expire, admit, and start everything that is due now.

        Args:
            None

        Returns:
            None

        Raises:
            None
        """
        self.timeline.check_step()
        now, max_delay = self.timeline.now_us(), self._settings.max_start_delay_us
        for miss in self.gate.expire(now, max_delay):
            await self.emit_miss(miss)
        while (slot := self.plan.pop_due(now)) is not None:
            if not self.admits():
                await self.emit_miss(Miss(slot, MissReason.STORAGE_FULL))
            elif (miss := self.gate.offer(slot, now, max_delay)) is not None:
                await self.emit_miss(miss)
        while not self.stopping and (ready := self.gate.start_next()) is not None:
            self._launch(ready)

    async def pause(self) -> None:
        """Sleep until the next due slot or wait expiry, or until woken.

        Args:
            None

        Returns:
            None

        Raises:
            None
        """
        candidates = (
            self.plan.next_due_us(),
            self.gate.next_expiry_us(self._settings.max_start_delay_us),
        )
        wake_us = min((t for t in candidates if t is not None), default=None)
        loop = asyncio.get_running_loop()
        self._waker = future = loop.create_future()
        timer = None
        if wake_us is not None:
            timer = loop.call_later(self.timeline.delay_s(wake_us), _resolve, future)
        try:
            await future
        finally:
            self._waker = None
            if timer is not None:
                timer.cancel()

    def _launch(self, slot: Slot) -> None:
        """Start one admitted slot as a task.

        Args:
            slot: The slot.

        Returns:
            None

        Raises:
            None
        """
        ctx = PollContext(
            new_id(PollId), self._ids.collector_id, slot.target, self.timeline.label(slot.due_us)
        )
        start_us = self.timeline.now_us()
        started = StartedPoll(
            ctx, slot.due_us, start_us, self._clock.now_utc(), self._ids.worker_id
        )
        job = Job(started, PollProgress())
        task = asyncio.create_task(self._execute(job))
        self.jobs[task] = job
        task.add_done_callback(self._forget)

    def _forget(self, task: asyncio.Task[None]) -> None:
        """Drop a finished task from the in-flight table.

        Args:
            task: The finished task.

        Returns:
            None

        Raises:
            None
        """
        self.jobs.pop(task, None)

    async def _execute(self, job: Job) -> None:
        """Run a poll, record it, and free its worker.

        Args:
            job: The poll.

        Returns:
            None

        Raises:
            asyncio.CancelledError: When shutdown cancels the poll; the
                driver records it from ``job.progress``.
        """
        device_id = job.started.ctx.target.device_id
        try:
            await self._run_steps(job)
            self.gate.end_session(device_id)
            job.result = finished_result(job.started, job.progress, self.timeline.now_us())
            await self.emit(job.result)
            job.emitted = True
        finally:
            self.gate.end_session(device_id)
            self.gate.release_worker()
            if self.gate.waiting:
                self.wake()

    async def _run_steps(self, job: Job) -> None:
        """Run the state machine under the poll deadline.

        Args:
            job: The poll.

        Returns:
            None

        Raises:
            asyncio.CancelledError: When shutdown cancels the poll.
        """
        progress = job.progress
        try:
            async with asyncio.timeout(self._settings.poll_deadline_s):
                await run_poll(job.started.ctx, self._steps, progress, self.timeline.now_us)
        except TimeoutError:
            progress.end(StepStatus.DEADLINE, self.timeline.now_us())
        except asyncio.CancelledError:
            progress.end(StepStatus.CANCELLED, self.timeline.now_us())
            raise
