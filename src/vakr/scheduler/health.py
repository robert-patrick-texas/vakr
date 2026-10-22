"""Scheduler self-observability in the shape of ``CollectorHealthObservation`` (OBS-001..004, 10.4).

A ``SchedulerSnapshot`` is a point-in-time view: devices scheduled, workers
busy, slots waiting, output-queue depth and backpressure, event-loop lag,
detected clock steps, latency percentiles, and the counters of the period
just closed. ``health_values`` maps it onto ``CollectorHealthObservation``
fields, and ``collector_state`` summarises it (E.25):

* ``OVERLOADED`` when the period had capacity misses or producers are
  blocked on a full output queue right now;
* ``DEGRADED`` when event-loop lag exceeded ``LAG_DEGRADED_US``, a step
  failed on the collector side, or the scheduler is draining;
* ``HEALTHY`` otherwise.

These states describe the collector, never a target (OBS-003, ADAPT-006).

Dependencies:
    vakr.scheduler.stats: Counters and percentiles.
    vakr.domain.enums: ``CollectorState``.

Safety:
    Counts and timings only.

Example:
    >>> snap = SchedulerSnapshot(devices=3, workers=4, busy_workers=0, waiting=0)
    >>> snap.collector_state().value, snap.health_values()["active_devices"]
    ('healthy', 3)
"""

from dataclasses import dataclass, field

from vakr.domain.enums import CollectorState
from vakr.scheduler.stats import Counters, Percentiles

LAG_DEGRADED_US = 1_000_000  # E.25: a 1 s stall already pushes starts past half the +-5 s target


@dataclass(frozen=True, slots=True)
class SchedulerSnapshot:
    """A point-in-time view of the scheduler.

    Attributes:
        devices: Devices scheduled.
        workers: Worker pool size.
        busy_workers: Workers holding a poll or its unsent result.
        waiting: Due slots waiting for a worker.
        queue_depth: Output-queue depth, or ``None`` for a custom sink.
        queue_capacity: Output-queue bound, or ``None`` for a custom sink.
        queue_full_waits: Times a producer found the output queue full.
        queue_blocked_s: Total producer wait on a full queue, in seconds.
        producers_blocked: Producers waiting on a full queue now.
        loop_lag_us: Largest event-loop lag in the period, if measured.
        clock_steps: Wall-clock steps detected (SCHED-018).
        wall_offset_us: Wall clock minus the plan timeline, in microseconds.
        lateness: Start-minus-slot percentiles.
        duration: Completion-minus-start percentiles.
        completion: Completion-minus-slot percentiles.
        period: Counters of the period this snapshot closed (or is in).
        lifetime: Counters since start.
        draining: True once shutdown has begun.
        intervals_s: Shortest and longest effective interval planned (OBS-006).
    """

    devices: int
    workers: int
    busy_workers: int
    waiting: int
    queue_depth: int | None = None
    queue_capacity: int | None = None
    queue_full_waits: int = 0
    queue_blocked_s: float = 0.0
    producers_blocked: int = 0
    loop_lag_us: int | None = None
    clock_steps: int = 0
    wall_offset_us: int = 0
    lateness: Percentiles | None = None
    duration: Percentiles | None = None
    completion: Percentiles | None = None
    period: Counters = field(default_factory=Counters)
    lifetime: Counters = field(default_factory=Counters)
    draining: bool = False
    intervals_s: tuple[int, int] | None = None

    def collector_state(self) -> CollectorState:
        """Summarise collector health from this snapshot (E.25).

        Args:
            None

        Returns:
            CollectorState: ``OVERLOADED``, ``DEGRADED``, or ``HEALTHY``.

        Raises:
            None
        """
        if self.period.capacity or self.producers_blocked:
            return CollectorState.OVERLOADED
        lagging = (self.loop_lag_us or 0) > LAG_DEGRADED_US
        if lagging or self.period.step_errors or self.draining:
            return CollectorState.DEGRADED
        return CollectorState.HEALTHY

    def health_values(self) -> dict[str, int | None]:
        """Return the ``CollectorHealthObservation`` fields this snapshot fills.

        Args:
            None

        Returns:
            dict[str, int | None]: Keyword arguments for the observation;
            unmeasured values are ``None``, never zero.

        Raises:
            None
        """
        return {
            "event_loop_lag_us": self.loop_lag_us,
            "queue_depth": self.queue_depth,
            "queue_capacity": self.queue_capacity,
            "polls_completed": self.period.completed,
            "polls_late": self.period.late,
            "polls_missed": self.period.missed,
            "active_devices": self.devices,
        }
