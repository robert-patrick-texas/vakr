"""Write-side telemetry ports: batches, rollups, collector health, retention (5.2).

``ObservationWriter.write_batch`` is the only way observations enter
storage. It is idempotent on ``observation_id`` (STORE-009): replaying a
spool segment after an uncertain commit inserts nothing twice, and the
result says how many were new. A batch is accepted or rejected as a whole
(STORE-010). The first write of an ID wins; a later observation with the
same ID is counted as a duplicate without comparing its content.

Dependencies:
    vakr.domain: Observations, rollup buckets, IDs, and windows.
    vakr.ports.telemetry.results: Result and capability types.

Safety:
    Retention removes data only by policy; lifecycle changes never delete
    history (RET-007).

Example:
    >>> "write_batch" in dir(ObservationWriter), "apply" in dir(RetentionManager)
    (True, True)
"""

from collections.abc import Sequence
from datetime import datetime
from typing import Protocol
from uuid import UUID

from vakr.domain.ids import BatchId, CollectorId
from vakr.domain.observations import CollectorHealthObservation, Observation
from vakr.domain.series import Metric, TimeWindow
from vakr.domain.state import RollupBucket
from vakr.ports.telemetry.results import RetentionPolicy, RetentionReport, WriteResult


class ObservationWriter(Protocol):
    """Durable, idempotent bulk writes of observations (STORE-003, STORE-009)."""

    def write_batch(self, batch_id: BatchId, observations: Sequence[Observation]) -> WriteResult:
        """Store a batch atomically, skipping observation IDs already stored.

        Args:
            batch_id: The batch's UUID, recorded for diagnostics.
            observations: Up to ``capabilities.max_batch_rows`` observations
                of any types; duplicates within the batch count once.

        Returns:
            WriteResult: Inserted and duplicate counts; returned only after
            the backend accepted the batch durably.

        Raises:
            ValidationError: If the batch is larger than ``max_batch_rows``;
                nothing is written.
        """
        ...


class RollupRepository(Protocol):
    """Fixed-width rollup buckets (10.5); usable only when ``capabilities.rollups``.

    Backends without rollups raise ``NotImplementedError`` from every method.
    """

    def write(self, buckets: Sequence[RollupBucket]) -> int:
        """Insert or replace buckets by identity (RET-009).

        Args:
            buckets: Buckets to store.

        Returns:
            int: Number of buckets written.

        Raises:
            NotImplementedError: If the backend has no rollups.
        """
        ...

    def read(
        self,
        subject_id: UUID,
        collector_id: CollectorId,
        metric: Metric,
        window: TimeWindow,
        *,
        limit: int,
    ) -> tuple[RollupBucket, ...]:
        """Return buckets whose start lies in the window, oldest first.

        Args:
            subject_id: Interface or device.
            collector_id: The collector.
            metric: The metric.
            window: Bounded window on ``bucket_start``.
            limit: At most this many buckets, 1 to ``MAX_SERIES_POINTS``.

        Returns:
            tuple[RollupBucket, ...]: The buckets.

        Raises:
            NotImplementedError: If the backend has no rollups.
            ValidationError: If ``limit`` is out of range.
        """
        ...

    def coverage_end(self) -> datetime | None:
        """Return the latest ``bucket_end`` of any stored bucket.

        Args:
            None

        Returns:
            datetime | None: The rollup frontier, or ``None`` if none stored.

        Raises:
            NotImplementedError: If the backend has no rollups.
        """
        ...


class CollectorHealthRepository(Protocol):
    """Latest collector health (OBS-001); history is ``read_raw(COLLECTOR_HEALTH, ...)``."""

    def latest(self, collector_id: CollectorId) -> CollectorHealthObservation | None:
        """Return the newest health observation of a collector.

        Args:
            collector_id: The collector.

        Returns:
            CollectorHealthObservation | None: The newest snapshot, or ``None``.

        Raises:
            None
        """
        ...


class RetentionManager(Protocol):
    """Expires telemetry by policy (RET-001..003, RET-007, Appendix E.10)."""

    @property
    def policy(self) -> RetentionPolicy:
        """The retention policy in force.

        Args:
            None

        Returns:
            RetentionPolicy: Native and rollup retention.

        Raises:
            None
        """
        ...

    def apply(self, now: datetime) -> RetentionReport:
        """Remove expired data; repeating with the same ``now`` removes nothing more.

        Native observations older than ``now - policy.native`` are removed;
        with rollups, never past ``coverage_end()`` (E.10, ROLL-T04). The
        cutoff becomes the native horizon. Rollup buckets ending at or
        before ``now - policy.rollup`` are removed.

        Args:
            now: Reference time (UTC).

        Returns:
            RetentionReport: Cutoffs and removed counts.

        Raises:
            ValidationError: If ``now`` is not UTC.
        """
        ...

    def native_horizon(self) -> datetime | None:
        """Return the time before which native data has been expired.

        Args:
            None

        Returns:
            datetime | None: The latest native cutoff applied, or ``None``.

        Raises:
            None
        """
        ...
