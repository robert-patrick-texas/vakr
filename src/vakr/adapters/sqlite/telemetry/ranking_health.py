"""What the ``rankings`` health check measures in ``telemetry.db`` (E.28, OPS-001).

``vakr check health`` runs in its own process, so it reads the file, read
only and cheaply, the way the ``rollups`` check does: the projection's
manifest (``rank_unit``, at most 125 rows) and the oldest interface or
ICMP observation in the last 25 hours (one seek per series in at most two
day partitions, plus staging). From those it counts the complete buckets,
at or after that observation, that the projector has not projected.

Dependencies:
    vakr.adapters.sqlite.connection, errors: Read-only connections.
    vakr.adapters.sqlite.telemetry.*: Read helpers, edges, and the bucket width.
    vakr.ports.telemetry.ranking_projection: The window the projection keeps.

Safety:
    Read-only; touches the manifest, the catalog, staging, and one seek per series.

Example:
    >>> ProjectionVitals(listed=96, missing=0).missing_s
    0
"""

from contextlib import closing
from dataclasses import dataclass
from pathlib import Path

from vakr.adapters.sqlite.connection import OpenMode, open_connection
from vakr.adapters.sqlite.errors import translated
from vakr.adapters.sqlite.telemetry.rankings import BUCKET_US, listed
from vakr.adapters.sqlite.telemetry.reads import native_start
from vakr.adapters.sqlite.telemetry.rollup_scan import native_edge
from vakr.ports.telemetry.ranking_projection import PROJECTION_KEEP

_KEEP_US = int(PROJECTION_KEEP.total_seconds()) * 1_000_000


@dataclass(frozen=True, slots=True)
class ProjectionVitals:
    """Ranking projection progress as stored. Immutable.

    Attributes:
        listed: Projected buckets among the complete buckets checked.
        missing: Complete buckets holding or after native data that are not projected.
    """

    listed: int
    missing: int

    @property
    def missing_s(self) -> int:
        """Return the missing buckets as seconds of window read natively.

        Args:
            None

        Returns:
            int: ``missing`` times 900.

        Raises:
            None
        """
        return self.missing * BUCKET_US // 1_000_000


def measure_projection(path: Path, complete_end_us: int, now_us: int) -> ProjectionVitals:
    """Count projected and missing complete buckets of the last 25 hours.

    Args:
        path: The telemetry database, at the current schema.
        complete_end_us: End of the newest complete bucket (E.10), microseconds.
        now_us: The current time, microseconds.

    Returns:
        ProjectionVitals: The counts.

    Raises:
        StorageError: If the database cannot be opened or read.
    """
    keep = now_us - _KEEP_US
    start = max(complete_end_us - _KEEP_US, keep - keep % BUCKET_US + BUCKET_US)
    with closing(open_connection(path, mode=OpenMode.READ_ONLY)) as conn, translated():
        conn.execute("BEGIN")  # one snapshot for the manifest and the edge
        start = native_start(conn, start)
        oldest = None if start >= complete_end_us else native_edge(
            conn, start, complete_end_us, newest=False
        )  # fmt: skip
        if oldest is not None:
            start = max(start, oldest - oldest % BUCKET_US)
        count = len(listed(conn, BUCKET_US, (start, complete_end_us))) if oldest is not None else 0
        conn.execute("COMMIT")
    if oldest is None:
        return ProjectionVitals(0, 0)
    first = -(-start // BUCKET_US)
    whole = max(0, complete_end_us // BUCKET_US - first)
    return ProjectionVitals(count, whole - count)
