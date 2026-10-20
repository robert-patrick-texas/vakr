"""Poll records and collector health: the context around every measurement (9.1, 10.4).

A ``DevicePoll`` is the logical unit of scheduled work. It ties a device's
ICMP, SNMP, and interface observations to one ``poll_id`` and records
whether the poll ran at all, so a collector that was too busy to poll is
recorded as *not attempted*, never as a device outage (invariant 2).
``CollectorHealthObservation`` describes the collector itself (OBS-003).

Dependencies:
    dataclasses: Frozen, slotted records with ``__post_init__`` invariants.
    vakr.domain.checks, timestamps: Shared invariant checks.

Safety:
    Health observations carry counts and versions, never configuration
    values or credentials.

Example:
    >>> PollExecution.NOT_ATTEMPTED in _NOT_STARTED
    True
"""

from dataclasses import dataclass
from datetime import datetime

from vakr.domain.checks import require_enum, require_int, require_name, require_order, require_uuid
from vakr.domain.enums import CollectorState, PollExecution, ReachabilityStrategy
from vakr.domain.errors import ValidationError
from vakr.domain.ids import CollectorId, DeviceId, ObservationId, PollId, WorkerId
from vakr.domain.quality import Quality
from vakr.domain.timestamps import require_optional_utc, require_utc

_NOT_STARTED = {PollExecution.SCHEDULED, PollExecution.NOT_ATTEMPTED, PollExecution.MISSED_OVERRUN}
_FINISHED = {
    PollExecution.COMPLETE, PollExecution.PARTIAL, PollExecution.FAILED, PollExecution.CANCELLED
}  # fmt: skip


@dataclass(frozen=True, slots=True)
class DevicePoll:
    """One scheduled execution of a device poll (9.1, SCHED-012, SCHED-017).

    The envelope timestamp of a poll is its scheduled time, because the
    schedule, not completion, anchors the next due time (invariant 10).

    Attributes:
        observation_id: UUID of this poll record, for deduplication.
        poll_id: UUID shared by every observation the poll produced.
        collector_id: The collector that owned the poll.
        device_id: The polled device.
        scheduled_at: Intended start time (UTC).
        configured_interval_s: Interval from configuration, in seconds.
        effective_interval_s: Interval actually in force, in seconds.
        reachability: ICMP/SNMP gating strategy in force.
        execution: How far the poll got and how it ended.
        quality: Coverage flags, such as ``NOT_ATTEMPTED``.
        worker_id: The worker that ran it, if it ran.
        started_at: When work began (UTC), if it began.
        completed_at: When work finished (UTC), if it finished.
    """

    observation_id: ObservationId
    poll_id: PollId
    collector_id: CollectorId
    device_id: DeviceId
    scheduled_at: datetime
    configured_interval_s: int
    effective_interval_s: int
    reachability: ReachabilityStrategy
    execution: PollExecution
    quality: Quality = Quality.NONE
    worker_id: WorkerId | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None

    @property
    def timestamp(self) -> datetime:
        """Return the scheduled time, which is the poll's envelope timestamp.

        Args:
            None

        Returns:
            datetime: ``scheduled_at``.

        Raises:
            None
        """
        return self.scheduled_at

    def __post_init__(self) -> None:
        """Validate the poll and the consistency of its times with its state.

        Args:
            None

        Returns:
            None

        Raises:
            ValidationError: If a field breaks its rule, or the start and
                completion times contradict ``execution``.
        """
        for name in ("observation_id", "poll_id", "collector_id", "device_id"):
            require_uuid(getattr(self, name), name)
        require_uuid(self.worker_id, "worker_id", optional=True)
        require_utc(self.scheduled_at, "scheduled_at")
        require_optional_utc(self.started_at, "started_at")
        require_optional_utc(self.completed_at, "completed_at")
        require_order(self.started_at, self.completed_at, "completed_at")
        require_int(self.configured_interval_s, "configured_interval_s", minimum=1)
        require_int(self.effective_interval_s, "effective_interval_s", minimum=1)
        require_enum(self.reachability, ReachabilityStrategy, "reachability")
        require_enum(self.execution, PollExecution, "execution")
        require_enum(self.quality, Quality, "quality")
        started, completed = self.started_at is not None, self.completed_at is not None
        expected = (
            (False, False) if self.execution in _NOT_STARTED
            else (True, True) if self.execution in _FINISHED
            else (True, False)
        )  # fmt: skip
        if (started, completed) != expected:
            raise ValidationError(
                f"a {self.execution} poll must "
                f"{'have' if expected[0] else 'not have'} started_at and "
                f"{'have' if expected[1] else 'not have'} completed_at",
                field="execution",
            )


@dataclass(frozen=True, slots=True)
class CollectorHealthObservation:
    """A lean snapshot of collector health (subset of 10.4; OBS-001, OBS-003).

    Counts cover the period since the previous health observation. Any
    metric the collector could not measure is ``None``.

    Attributes:
        observation_id: UUID of this observation.
        collector_id: The collector described.
        timestamp: When the snapshot was taken (UTC).
        state: Overall collector health.
        software_version: Collector software version string.
        quality: Evidence flags, such as ``CLOCK_UNSYNCED``.
        worker_id: The reporting worker, if the snapshot is per worker.
        clock_offset_us: Estimated clock offset from the reference, signed.
        event_loop_lag_us: Measured event-loop lag.
        queue_depth: Items waiting in the poll queue.
        queue_capacity: Bound of the poll queue.
        polls_completed: Polls finished in the period.
        polls_late: Polls that started late in the period.
        polls_missed: Polls not attempted or overrun in the period.
        spool_bytes: Bytes held in the durable spool.
        spool_used_pct: Spool fill level, 0 to 100.
        active_devices: Devices the collector is scheduling.
        active_interfaces: Interfaces the collector is sampling.
    """

    observation_id: ObservationId
    collector_id: CollectorId
    timestamp: datetime
    state: CollectorState
    software_version: str
    quality: Quality = Quality.NONE
    worker_id: WorkerId | None = None
    clock_offset_us: int | None = None
    event_loop_lag_us: int | None = None
    queue_depth: int | None = None
    queue_capacity: int | None = None
    polls_completed: int | None = None
    polls_late: int | None = None
    polls_missed: int | None = None
    spool_bytes: int | None = None
    spool_used_pct: float | None = None
    active_devices: int | None = None
    active_interfaces: int | None = None

    def __post_init__(self) -> None:
        """Validate the snapshot.

        Args:
            None

        Returns:
            None

        Raises:
            ValidationError: If a field breaks its rule.
        """
        require_uuid(self.observation_id, "observation_id")
        require_uuid(self.collector_id, "collector_id")
        require_uuid(self.worker_id, "worker_id", optional=True)
        require_utc(self.timestamp, "timestamp")
        require_enum(self.state, CollectorState, "state")
        require_name(self.software_version, "software_version")
        require_enum(self.quality, Quality, "quality")
        require_int(self.clock_offset_us, "clock_offset_us", optional=True)
        for name in (
            "event_loop_lag_us", "queue_depth", "queue_capacity", "polls_completed",
            "polls_late", "polls_missed", "spool_bytes", "active_devices", "active_interfaces",
        ):  # fmt: skip
            require_int(getattr(self, name), name, minimum=0, optional=True)
        if self.queue_depth is not None and self.queue_capacity is not None:
            require_int(self.queue_depth, "queue_depth", maximum=self.queue_capacity)
        pct = self.spool_used_pct
        if pct is not None and (not isinstance(pct, float) or not 0.0 <= pct <= 100.0):
            raise ValidationError(
                "spool_used_pct must be a float from 0 to 100", field="spool_used_pct"
            )
