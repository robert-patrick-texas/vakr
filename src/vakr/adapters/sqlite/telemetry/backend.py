"""``SqliteTelemetry``: every telemetry port over one store, plus maintenance (5.2, ADR-021).

``SqliteTelemetry`` is the ``TelemetryBackend`` the services and the
contract suites use. Beyond the ports it offers ``maintain(now)``, which
the M3/M4 service loop calls every ``tuning.MOVE_INTERVAL`` (15 minutes,
E.21) from the writer thread: it drains the staging tables into their day
partitions and truncates the WAL. The ports themselves are unchanged.

Dependencies:
    vakr.adapters.sqlite.telemetry.*: The port implementations and the store.
    vakr.ports.telemetry: Capabilities and the factory type.

Safety:
    Synchronous: call it from a worker or writer thread, never from the
    event loop (5.3).

Example:
    >>> SqliteTelemetry.__name__, CAPABILITIES.time_partitioned
    ('SqliteTelemetry', True)
"""

from datetime import datetime

from vakr.adapters.sqlite.telemetry.acceptance import SqliteAcceptanceQuery
from vakr.adapters.sqlite.telemetry.history import SqliteHistoryQuery
from vakr.adapters.sqlite.telemetry.maintenance import MaintenanceReport, maintain
from vakr.adapters.sqlite.telemetry.ranking_projection import SqliteRankingProjection
from vakr.adapters.sqlite.telemetry.retention import SqliteRetentionManager
from vakr.adapters.sqlite.telemetry.rollup_source import SqliteRollupSource
from vakr.adapters.sqlite.telemetry.rollups import SqliteRollupRepository
from vakr.adapters.sqlite.telemetry.series import SqliteObservationQuery
from vakr.adapters.sqlite.telemetry.state import (
    SqliteCollectorHealthRepository,
    SqliteCurrentStateQuery,
)
from vakr.adapters.sqlite.telemetry.store import SqliteTelemetryStore
from vakr.adapters.sqlite.telemetry.tuning import MAX_BATCH_ROWS
from vakr.adapters.sqlite.telemetry.writer import SqliteObservationWriter
from vakr.ports.telemetry import TelemetryBackend, TelemetryBackendFactory
from vakr.ports.telemetry.results import TelemetryCapabilities

CAPABILITIES = TelemetryCapabilities(
    rollups=True, max_batch_rows=MAX_BATCH_ROWS, time_partitioned=True
)
"""What the Compact SQLite telemetry store supports."""


class SqliteTelemetry:
    """Every telemetry port over one ``SqliteTelemetryStore``.

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
        ranking_projection: Exact per-bucket ranking partials (E.28, ``ProjectionBackend``).
        acceptance: Lean reads for the acceptance reports (E.35, ``AcceptanceBackend``).
    """

    def __init__(self, store: SqliteTelemetryStore) -> None:
        """Build the ports over ``store``.

        Args:
            store: The open store.

        Returns:
            None

        Raises:
            None
        """
        self._store = store
        self.capabilities: TelemetryCapabilities = CAPABILITIES
        self.writer = SqliteObservationWriter(store)
        self.query = SqliteObservationQuery(store)
        self.current = SqliteCurrentStateQuery(store)
        self.rollups = SqliteRollupRepository(store)
        self.rollup_source = SqliteRollupSource(store)
        self.collector_health = SqliteCollectorHealthRepository(store)
        self.retention = SqliteRetentionManager(store)
        self.history = SqliteHistoryQuery(store)
        self.ranking_projection = SqliteRankingProjection(store)
        self.acceptance = SqliteAcceptanceQuery(store)

    def maintain(self, now: datetime) -> MaintenanceReport:
        """Move staged rows older than ``now - MOVE_LAG`` and truncate the WAL.

        Args:
            now: Reference time (UTC).

        Returns:
            MaintenanceReport: Rows moved, the new boundary, and the checkpoint result.

        Raises:
            ValidationError: If ``now`` is not UTC.
            StorageError: If the database is unavailable.
        """
        return maintain(self._store, now)


def sqlite_telemetry_factory(store: SqliteTelemetryStore) -> TelemetryBackendFactory:
    """Return a factory of backend views that share ``store``.

    Args:
        store: The open store.

    Returns:
        TelemetryBackendFactory: Each call returns a new view.

    Raises:
        None
    """

    def open_view() -> TelemetryBackend:
        """Return a view of the shared store.

        Args:
            None

        Returns:
            TelemetryBackend: The ports.

        Raises:
            None
        """
        return SqliteTelemetry(store)

    return open_view
