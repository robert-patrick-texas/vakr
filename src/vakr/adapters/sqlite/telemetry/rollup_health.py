"""What the ``rollups`` health check measures in ``telemetry.db`` (RET-009, E.10, OPS-001).

``vakr check health`` runs every minute in its own process, so it cannot
ask the collector how its rollup job is doing; it reads the file instead,
read-only and cheaply:

- the rollup frontier, from the partition catalog;
- the oldest and the newest interface or ICMP observation at or after the
  frontier and before the end of the newest complete bucket, one seek per
  series in at most one day partition each, plus staging.

Those three times say how far rollups are behind the native data that is
ready for them, and how old the oldest native data still waiting is.
Retention cannot expire that data until a rollup covers it (ROLL-T04).

Dependencies:
    vakr.adapters.sqlite.connection, errors: Read-only connections.
    vakr.adapters.sqlite.telemetry.rollups, rollup_scan: Frontier and edges.

Safety:
    Read-only; touches the catalog, staging, and one seek per series.

Example:
    >>> RollupVitals(None, None, None).waiting
    False
"""

from contextlib import closing
from dataclasses import dataclass
from pathlib import Path

from vakr.adapters.sqlite.connection import OpenMode, open_connection
from vakr.adapters.sqlite.errors import translated
from vakr.adapters.sqlite.telemetry.rollup_scan import native_edge
from vakr.adapters.sqlite.telemetry.rollups import coverage_end_us
from vakr.domain.timestamps import to_epoch_us


@dataclass(frozen=True, slots=True)
class RollupVitals:
    """Rollup progress as stored. Immutable.

    Attributes:
        frontier_us: End of the newest stored rollup bucket, or ``None``.
        oldest_us: Oldest native observation waiting for a rollup, or ``None``.
        newest_us: Newest native observation in a complete bucket waiting
            for a rollup, or ``None``.
    """

    frontier_us: int | None
    oldest_us: int | None
    newest_us: int | None

    @property
    def waiting(self) -> bool:
        """Tell whether any complete native data still waits for a rollup.

        Args:
            None

        Returns:
            bool: ``True`` when ``oldest_us`` is set.

        Raises:
            None
        """
        return self.oldest_us is not None


def measure_rollups(path: Path, complete_end_us: int) -> RollupVitals:
    """Read the rollup frontier and the native data waiting behind it.

    Args:
        path: The telemetry database, at the current schema.
        complete_end_us: End of the newest complete bucket (E.10), microseconds.

    Returns:
        RollupVitals: The measurements.

    Raises:
        StorageError: If the database cannot be opened or read.
    """
    with closing(open_connection(path, mode=OpenMode.READ_ONLY)) as conn, translated():
        conn.execute("BEGIN")  # one snapshot for the frontier and both edges
        frontier = coverage_end_us(conn)
        start = 0 if frontier is None else to_epoch_us(frontier)
        oldest = native_edge(conn, start, complete_end_us, newest=False)
        newest = None if oldest is None else native_edge(conn, start, complete_end_us, newest=True)
        conn.execute("COMMIT")
    return RollupVitals(None if frontier is None else start, oldest, newest)
