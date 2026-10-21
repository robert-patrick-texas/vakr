"""In-memory ``RetentionManager`` and the ``TelemetryBackend`` facade.

Retention follows Appendix E.10: with rollups, native observations expire
only behind the rollup frontier and the cutoff is aligned down to a bucket
boundary, so no rollup bucket ever overlaps native data that a series still
shows (DB-006, ROLL-T04). Without rollups (a Lite-style backend) native data
simply expires after the policy duration. Each pass also unlists ranking
projection buckets before the horizon or older than 25 hours (E.28).

Dependencies:
    vakr.adapters.memory.telemetry_store, telemetry_query, telemetry_state,
        telemetry_rollup_source, telemetry_history.
    vakr.domain.rollups: Bucket alignment.

Safety:
    Only expired telemetry is removed; metadata is untouched (RET-007).

Example:
    >>> backend = MemoryTelemetry(MemoryTelemetryStore())
    >>> backend.capabilities.rollups, backend.retention.native_horizon()
    (True, None)
"""

from datetime import datetime

from vakr.adapters.memory.telemetry_history import MemoryHistoryQuery
from vakr.adapters.memory.telemetry_projection import MemoryRankingProjection, prune_projection
from vakr.adapters.memory.telemetry_query import MemoryObservationQuery
from vakr.adapters.memory.telemetry_rollup_source import MemoryRollupSource
from vakr.adapters.memory.telemetry_state import MemoryCurrentStateQuery
from vakr.adapters.memory.telemetry_store import (
    MemoryCollectorHealthRepository,
    MemoryObservationWriter,
    MemoryRollupRepository,
    MemoryTelemetryStore,
)
from vakr.domain.rollups import bucket_start
from vakr.domain.timestamps import require_utc
from vakr.ports.telemetry.results import RetentionPolicy, RetentionReport, TelemetryCapabilities


class MemoryRetentionManager:
    """``RetentionManager`` over a ``MemoryTelemetryStore``."""

    def __init__(self, store: MemoryTelemetryStore) -> None:
        """Bind the manager.

        Args:
            store: The store.

        Returns:
            None

        Raises:
            None
        """
        self._store = store

    @property
    def policy(self) -> RetentionPolicy:
        """The retention policy in force.

        Args:
            None

        Returns:
            RetentionPolicy: The policy.

        Raises:
            None
        """
        return self._store.policy

    def native_horizon(self) -> datetime | None:
        """Return the latest native cutoff applied.

        Args:
            None

        Returns:
            datetime | None: The horizon, or ``None``.

        Raises:
            None
        """
        return self._store.horizon

    def apply(self, now: datetime) -> RetentionReport:
        """Expire native observations and rollup buckets by policy.

        Args:
            now: Reference time (UTC).

        Returns:
            RetentionReport: Cutoffs and counts.

        Raises:
            ValidationError: If ``now`` is not UTC.
        """
        require_utc(now, "now")
        store = self._store
        cutoff: datetime | None = now - store.policy.native
        rollup_cutoff = now - store.policy.rollup if store.capabilities.rollups else None
        with store.lock:
            if store.capabilities.rollups:
                frontier = max((b.bucket_end for b in store.buckets.values()), default=None)
                cutoff = None if frontier is None else bucket_start(min(cutoff, frontier))
            expired = [
                k
                for k, o in store.observations.items()
                if cutoff is not None and o.timestamp < cutoff
            ]
            for key in expired:
                del store.observations[key]
            if cutoff is not None and (store.horizon is None or cutoff > store.horizon):
                store.horizon = cutoff
            old = [
                k
                for k, b in store.buckets.items()
                if rollup_cutoff is not None and b.bucket_end <= rollup_cutoff
            ]
            for key in old:
                del store.buckets[key]
            prune_projection(store, now)
        return RetentionReport(now, cutoff, rollup_cutoff, len(expired), len(old))


class MemoryTelemetry:
    """Every telemetry port over one ``MemoryTelemetryStore``.

    Attributes:
        capabilities: What the store supports.
        writer: Batch writer.
        query: Series and raw reads.
        current: Current-state reads.
        rollups: Rollup storage.
        rollup_source: Native reads for the rollup job.
        collector_health: Latest collector health.
        retention: Retention.
        history: Downsampled series and ranking summaries (M4, ``HistoryBackend``).
        ranking_projection: Projected-bucket bookkeeping (E.28, ``ProjectionBackend``).
    """

    def __init__(self, store: MemoryTelemetryStore) -> None:
        """Build the ports over ``store``.

        Args:
            store: The store.

        Returns:
            None

        Raises:
            None
        """
        self.capabilities: TelemetryCapabilities = store.capabilities
        self.writer = MemoryObservationWriter(store)
        self.query = MemoryObservationQuery(store)
        self.current = MemoryCurrentStateQuery(store)
        self.rollups = MemoryRollupRepository(store)
        self.rollup_source = MemoryRollupSource(store)
        self.collector_health = MemoryCollectorHealthRepository(store)
        self.retention = MemoryRetentionManager(store)
        self.history = MemoryHistoryQuery(store)
        self.ranking_projection = MemoryRankingProjection(store)
