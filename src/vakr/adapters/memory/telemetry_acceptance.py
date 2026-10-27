"""In-memory ``AcceptanceQuery``: the reference semantics of the lean acceptance reads (E.35).

Every method filters the store's observations or rollup buckets to one
subject and collector, orders them as the port defines, and reduces them
to marks. The contract suite ``contract_acceptance`` holds the SQLite
adapter to exactly these answers.

Dependencies:
    vakr.adapters.memory.telemetry_store: The store.
    vakr.domain: Observations, metrics, timestamps.
    vakr.ports.telemetry.acceptance: The port's marks and rules.

Safety:
    Read-only; each call copies the store's values before filtering.

Example:
    >>> from vakr.adapters.memory.telemetry_store import MemoryTelemetryStore
    >>> MemoryAcceptanceQuery(MemoryTelemetryStore()).__class__.__name__
    'MemoryAcceptanceQuery'
"""

from datetime import datetime
from uuid import UUID

from vakr.adapters.memory.telemetry_store import MemoryTelemetryStore
from vakr.domain.enums import ObservationType
from vakr.domain.ids import CollectorId, DeviceId
from vakr.domain.observations import (
    DevicePoll,
    IcmpObservation,
    InterfaceObservation,
    observation_subject,
    observation_type,
)
from vakr.domain.quality import Quality
from vakr.domain.rollups import ROLLUP_BUCKET_SECONDS
from vakr.domain.series import Metric, TimeWindow, metric_value
from vakr.domain.timestamps import to_epoch_us
from vakr.ports.telemetry.acceptance import (
    MAX_MARKS,
    BucketMark,
    Marks,
    PollMark,
    SampleMark,
    check_request,
)
from vakr.ports.telemetry.rollup_source import ICMP_METRICS, INTERFACE_METRICS

_METRICS = {ObservationType.INTERFACE: INTERFACE_METRICS, ObservationType.ICMP: ICMP_METRICS}


def _presence(
    observation: IcmpObservation | InterfaceObservation, metrics: tuple[Metric, ...]
) -> int:
    """Return the presence bits of an observation's metrics.

    Args:
        observation: An interface or ICMP observation.
        metrics: The metric order.

    Returns:
        int: Bit ``i`` set when metric ``i`` has a value.

    Raises:
        None
    """
    bits = 0
    for i, metric in enumerate(metrics):
        if metric_value(observation, metric) is not None:
            bits |= 1 << i
    return bits


def _us(moment: datetime | None) -> int | None:
    """Return an optional datetime as epoch microseconds.

    Args:
        moment: A UTC datetime or ``None``.

    Returns:
        int | None: Microseconds, or ``None``.

    Raises:
        None
    """
    return None if moment is None else to_epoch_us(moment)


class MemoryAcceptanceQuery:
    """``AcceptanceQuery`` over a ``MemoryTelemetryStore``."""

    def __init__(self, store: MemoryTelemetryStore) -> None:
        """Bind the query.

        Args:
            store: The store.

        Returns:
            None

        Raises:
            None
        """
        self._store = store

    def _select(
        self, kind: ObservationType, subject: UUID, collector: UUID, window: TimeWindow
    ) -> list:
        """Return one subject's and collector's observations of a type in the window, unordered.

        Args:
            kind: Observation type.
            subject: Subject UUID.
            collector: Collector UUID.
            window: The window on the observation timestamp.

        Returns:
            list: The observations.

        Raises:
            None
        """
        return [
            o for o in list(self._store.observations.values())
            if observation_type(o) is kind and observation_subject(o) == subject
            and o.collector_id == collector and window.contains(o.timestamp)
        ]  # fmt: skip

    def poll_marks(
        self, device_id: DeviceId, collector_id: CollectorId, window: TimeWindow, *,
        limit: int = MAX_MARKS,
    ) -> Marks[PollMark]:  # fmt: skip
        """Return one device's poll records as marks; see ``AcceptanceQuery.poll_marks``.

        Args:
            device_id: The device.
            collector_id: The collector.
            window: Window on the scheduled time.
            limit: At most this many marks.

        Returns:
            Marks[PollMark]: Oldest first.

        Raises:
            ValidationError: If ``limit`` is out of range.
        """
        check_request(None, limit)
        polls: list[DevicePoll] = self._select(
            ObservationType.DEVICE_POLL, device_id, collector_id, window
        )
        polls.sort(key=lambda p: (p.scheduled_at, str(p.observation_id)))
        marks = [
            PollMark(to_epoch_us(p.scheduled_at), p.effective_interval_s, p.execution, p.quality,
                     _us(p.started_at), _us(p.completed_at))
            for p in polls[:limit]
        ]  # fmt: skip
        return Marks(tuple(marks), len(polls) > limit)

    def sample_marks(
        self, observation_type: ObservationType, subject_id: UUID, collector_id: CollectorId,
        window: TimeWindow, *, limit: int = MAX_MARKS, newest_first: bool = False,
    ) -> Marks[SampleMark]:  # fmt: skip
        """Return one subject's native observations as marks; see the port.

        Args:
            observation_type: ``INTERFACE`` or ``ICMP``.
            subject_id: The interface or device.
            collector_id: The collector.
            window: Window on the observation time.
            limit: At most this many marks.
            newest_first: Newest first.

        Returns:
            Marks[SampleMark]: The marks.

        Raises:
            ValidationError: If the type or ``limit`` is invalid.
        """
        check_request(observation_type, limit)
        found = self._select(observation_type, subject_id, collector_id, window)
        found.sort(key=lambda o: (o.timestamp, str(o.observation_id)), reverse=newest_first)
        metrics = _METRICS[observation_type]
        marks = [
            SampleMark(to_epoch_us(o.timestamp), o.quality, _presence(o, metrics))
            for o in found[:limit]
        ]  # fmt: skip
        return Marks(tuple(marks), len(found) > limit)

    def bucket_marks(
        self, observation_type: ObservationType, subject_id: UUID, collector_id: CollectorId,
        window: TimeWindow, *, limit: int = MAX_MARKS, newest_first: bool = False,
    ) -> Marks[BucketMark]:  # fmt: skip
        """Return one subject's 15-minute rollup buckets as marks; see the port.

        Args:
            observation_type: ``INTERFACE`` or ``ICMP``.
            subject_id: The interface or device.
            collector_id: The collector.
            window: Window on the bucket start.
            limit: At most this many marks.
            newest_first: Newest first.

        Returns:
            Marks[BucketMark]: The marks.

        Raises:
            NotImplementedError: If the store has no rollups.
            ValidationError: If the type or ``limit`` is invalid.
        """
        if not self._store.capabilities.rollups:
            raise NotImplementedError("this telemetry backend has no rollups")
        check_request(observation_type, limit)
        metrics = _METRICS[observation_type]
        merged: dict[int, tuple[int, int, Quality]] = {}
        for b in list(self._store.buckets.values()):
            fits = b.subject_id == subject_id and b.collector_id == collector_id
            if not fits or b.bucket_seconds != ROLLUP_BUCKET_SECONDS or b.metric not in metrics:
                continue
            if not window.contains(b.bucket_start):
                continue
            start = to_epoch_us(b.bucket_start)
            samples, bits, quality = merged.get(start, (0, 0, Quality.NONE))
            bit = 1 << metrics.index(b.metric) if b.valid_count > 0 else 0
            merged[start] = (max(samples, b.sample_count), bits | bit, quality | b.quality)
        starts = sorted(merged, reverse=newest_first)
        marks = [BucketMark(s, *merged[s]) for s in starts[:limit]]
        return Marks(tuple(marks), len(starts) > limit)
