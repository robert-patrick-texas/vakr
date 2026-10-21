"""In-memory telemetry storage: observations, rollup buckets, and the native horizon.

``MemoryTelemetryStore`` holds everything one telemetry backend would: native
observations keyed by observation UUID (the deduplication key, STORE-009),
rollup buckets keyed by their identity, the native horizon set by
retention, and which 15-minute buckets the ranking projection lists
(E.28: storing an interface sample unlists its bucket). The writer,
rollup, and collector-health ports here operate on it.

Dependencies:
    threading: Serialises writes.
    vakr.domain, vakr.ports.telemetry: Observations and result types.

Safety:
    A batch is validated completely before any observation is stored, so a
    rejected batch writes nothing (STORE-010).

Example:
    >>> store = MemoryTelemetryStore(rollups=False)
    >>> store.capabilities.rollups, store.policy.native.days
    (False, 30)
"""

import threading
from collections.abc import Sequence
from datetime import datetime, timedelta
from uuid import UUID

from vakr.domain.checks import require_int
from vakr.domain.errors import ValidationError
from vakr.domain.ids import BatchId, CollectorId
from vakr.domain.observations import (
    CollectorHealthObservation,
    InterfaceObservation,
    Observation,
    observation_type,
)
from vakr.domain.projections import latest
from vakr.domain.rollups import bucket_start
from vakr.domain.series import MAX_SERIES_POINTS, Metric, TimeWindow
from vakr.domain.state import RollupBucket
from vakr.ports.telemetry.results import RetentionPolicy, TelemetryCapabilities, WriteResult

COMPACT_RETENTION = RetentionPolicy(native=timedelta(days=30), rollup=timedelta(days=400))
"""Compact profile retention: 30 days native, rollups through day 400 (5.1, E.10)."""

type BucketKey = tuple[UUID, UUID, Metric, datetime, int]


class MemoryTelemetryStore:
    """All telemetry of one in-memory backend.

    Attributes:
        lock: Serialises writes and retention.
        capabilities: What this backend supports.
        policy: Retention policy.
        observations: Observation UUID to observation.
        buckets: Bucket identity to bucket.
        horizon: Latest native cutoff applied, or ``None``.
        projected: Starts of the buckets the ranking projection lists (E.28).
    """

    def __init__(
        self,
        *,
        rollups: bool = True,
        max_batch_rows: int = 50_000,
        policy: RetentionPolicy = COMPACT_RETENTION,
    ) -> None:
        """Create an empty store.

        Args:
            rollups: Whether to serve rollups (``False`` imitates a backend
                that has none yet).
            max_batch_rows: Largest accepted batch (STORE-011 upper target).
            policy: Retention policy.

        Returns:
            None

        Raises:
            ValidationError: If ``max_batch_rows`` is not positive.
        """
        self.lock = threading.Lock()
        self.capabilities = TelemetryCapabilities(rollups=rollups, max_batch_rows=max_batch_rows)
        self.policy = policy
        self.observations: dict[UUID, Observation] = {}
        self.buckets: dict[BucketKey, RollupBucket] = {}
        self.horizon: datetime | None = None
        self.projected: set[datetime] = set()


class MemoryObservationWriter:
    """``ObservationWriter`` over a ``MemoryTelemetryStore``."""

    def __init__(self, store: MemoryTelemetryStore) -> None:
        """Bind the writer.

        Args:
            store: The store.

        Returns:
            None

        Raises:
            None
        """
        self._store = store

    def write_batch(self, batch_id: BatchId, observations: Sequence[Observation]) -> WriteResult:
        """Store a batch, skipping known observation IDs.

        Args:
            batch_id: The batch UUID.
            observations: The observations.

        Returns:
            WriteResult: Inserted and duplicate counts.

        Raises:
            ValidationError: If the batch is too large or holds a non-observation.
        """
        if len(observations) > self._store.capabilities.max_batch_rows:
            raise ValidationError(
                f"batch of {len(observations)} exceeds max_batch_rows "
                f"{self._store.capabilities.max_batch_rows}"
            )
        for observation in observations:
            observation_type(observation)
        inserted = 0
        with self._store.lock:
            for observation in observations:
                if observation.observation_id not in self._store.observations:
                    self._store.observations[observation.observation_id] = observation
                    inserted += 1
                    if isinstance(observation, InterfaceObservation):
                        self._store.projected.discard(bucket_start(observation.timestamp))
        return WriteResult(batch_id, inserted, len(observations) - inserted)


class MemoryRollupRepository:
    """``RollupRepository`` over a ``MemoryTelemetryStore``."""

    def __init__(self, store: MemoryTelemetryStore) -> None:
        """Bind the repository.

        Args:
            store: The store.

        Returns:
            None

        Raises:
            None
        """
        self._store = store

    def _require_enabled(self) -> None:
        """Refuse use on a backend without rollups.

        Args:
            None

        Returns:
            None

        Raises:
            NotImplementedError: If rollups are disabled.
        """
        if not self._store.capabilities.rollups:
            raise NotImplementedError("this telemetry backend has no rollups")

    def write(self, buckets: Sequence[RollupBucket]) -> int:
        """Insert or replace buckets by identity.

        Args:
            buckets: Buckets.

        Returns:
            int: Buckets written.

        Raises:
            NotImplementedError: If rollups are disabled.
        """
        self._require_enabled()
        with self._store.lock:
            for b in buckets:
                key = (b.subject_id, b.collector_id, b.metric, b.bucket_start, b.bucket_seconds)
                self._store.buckets[key] = b
        return len(buckets)

    def read(
        self, subject_id: UUID, collector_id: CollectorId, metric: Metric, window: TimeWindow,
        *, limit: int,
    ) -> tuple[RollupBucket, ...]:  # fmt: skip
        """Return buckets starting in the window, oldest first.

        Args:
            subject_id: Interface or device.
            collector_id: The collector.
            metric: The metric.
            window: Window on ``bucket_start``.
            limit: Maximum buckets.

        Returns:
            tuple[RollupBucket, ...]: The buckets.

        Raises:
            NotImplementedError: If rollups are disabled.
            ValidationError: If ``limit`` is out of range.
        """
        self._require_enabled()
        require_int(limit, "limit", minimum=1, maximum=MAX_SERIES_POINTS)
        found = [
            b for b in self._store.buckets.values()
            if b.subject_id == subject_id and b.collector_id == collector_id
            and b.metric is metric and window.contains(b.bucket_start)
        ]  # fmt: skip
        found.sort(key=lambda b: (b.bucket_start, b.bucket_seconds))
        return tuple(found[:limit])

    def coverage_end(self) -> datetime | None:
        """Return the latest stored bucket end.

        Args:
            None

        Returns:
            datetime | None: The rollup frontier.

        Raises:
            NotImplementedError: If rollups are disabled.
        """
        self._require_enabled()
        return max((b.bucket_end for b in self._store.buckets.values()), default=None)


class MemoryCollectorHealthRepository:
    """``CollectorHealthRepository`` over a ``MemoryTelemetryStore``."""

    def __init__(self, store: MemoryTelemetryStore) -> None:
        """Bind the repository.

        Args:
            store: The store.

        Returns:
            None

        Raises:
            None
        """
        self._store = store

    def latest(self, collector_id: CollectorId) -> CollectorHealthObservation | None:
        """Return the newest health snapshot of a collector.

        Args:
            collector_id: The collector.

        Returns:
            CollectorHealthObservation | None: The snapshot, or ``None``.

        Raises:
            None
        """
        snapshots = [
            o for o in self._store.observations.values()
            if isinstance(o, CollectorHealthObservation) and o.collector_id == collector_id
        ]  # fmt: skip
        return latest(snapshots)
