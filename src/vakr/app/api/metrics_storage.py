"""Database figures for the scrape, and the API's ``MetricsService`` (OBS-007, E.33).

The figures ``vakr check health`` already measures come from the same
checks, so a dashboard and a Nagios alert never disagree: rollup lag and
the age of native data waiting for a rollup (``vakr.health.rollup_check``),
and the native data the ranking projection misses
(``vakr.health.ranking_check``). Beside them: each database file's size
(main file and write-ahead log) and its schema version. A figure that
cannot be read is absent, never 0 (invariant 1).

Dependencies:
    vakr.adapters.sqlite: File names and schema status.
    vakr.health: The rollup and ranking checks' measurements.
    vakr.metrics: Families and the service.

Safety:
    Read-only; the checks open the telemetry database read-only in effect
    (they only query) and never write.

Example:
    >>> from vakr.paths import InstallPaths
    >>> from pathlib import Path
    >>> [f.name for f in storage_families(InstallPaths.under(Path("/nonexistent")))]
    []
"""

import logging
from functools import partial
from pathlib import Path

from vakr.adapters.sqlite.metadata.store import METADATA_FILE, metadata_migrations
from vakr.adapters.sqlite.telemetry.store import TELEMETRY_FILE, telemetry_migrations
from vakr.domain.errors import StorageError
from vakr.health.model import CheckResult
from vakr.health.ranking_check import check_rankings
from vakr.health.rollup_check import check_rollups
from vakr.metrics.catalog import build
from vakr.metrics.exposition import Family
from vakr.metrics.service import MetricsService
from vakr.metrics.snapshot import SNAPSHOT_FILE
from vakr.metrics.state import state_families
from vakr.paths import InstallPaths
from vakr.ports.metadata import MetadataUnitOfWorkFactory
from vakr.query import OverviewQueries

_LOG = logging.getLogger(__name__)
_PERF = {"rollup_lag_s": "vakr_rollup_lag_seconds",
         "rollup_waiting_age_s": "vakr_rollup_waiting_age_seconds",
         "ranking_unprojected_s": "vakr_rankings_unprojected_seconds"}  # fmt: skip


def _size(path: Path) -> int | None:
    """Return a file's size, or ``None`` when it does not exist or cannot be read.

    Args:
        path: The file.

    Returns:
        int | None: Bytes.

    Raises:
        None
    """
    try:
        return path.stat().st_size
    except OSError:
        return None


def _schema(name: str, path: Path) -> int | None:
    """Return a database's applied schema version.

    Args:
        name: ``metadata`` or ``telemetry``.
        path: The database file.

    Returns:
        int | None: The version, or ``None`` when absent or unreadable.

    Raises:
        None
    """
    if not path.exists():
        return None
    factory = metadata_migrations if name == "metadata" else telemetry_migrations
    try:
        return factory(path).status().version
    except StorageError, OSError:
        return None


def _perf(results: list[CheckResult]) -> list[Family]:
    """Turn the checks' performance data into gauges.

    Args:
        results: The rollup and ranking check results.

    Returns:
        list[Family]: The gauges the checks measured.

    Raises:
        None
    """
    found = {p.label: p.value for r in results for p in r.perfdata}
    return [build(name, [((), found.get(label))]) for label, name in _PERF.items()]


def storage_families(paths: InstallPaths) -> list[Family]:
    """Read the database figures (blocking; a worker thread).

    Args:
        paths: Install layout.

    Returns:
        list[Family]: Sizes, schema versions, rollup and projection lag; empty
        on a node without a state directory.

    Raises:
        None
    """
    if not paths.state_dir.exists():
        return []
    files = {"metadata": paths.state_dir / METADATA_FILE,
             "telemetry": paths.state_dir / TELEMETRY_FILE}  # fmt: skip
    sizes = [((db, kind), _size(f if kind == "main" else f.with_name(f.name + "-wal")))
             for db, f in files.items() for kind in ("main", "wal")]  # fmt: skip
    versions = [((db,), _schema(db, f)) for db, f in files.items()]
    try:
        perf = _perf([check_rollups(), check_rankings()])
    except Exception as exc:  # noqa: BLE001 - a check bug must not cost the whole scrape
        _LOG.debug("metrics: a storage check failed (%s)", type(exc).__name__)
        perf = []
    return [build("vakr_database_size_bytes", sizes),
            build("vakr_database_schema_version", versions), *perf]  # fmt: skip


def replication_families(paths: InstallPaths) -> list[Family]:
    """Read a peer's metadata pull record (MPOS-006); nothing on a node that never pulled.

    Args:
        paths: Install layout; the record is under the state directory.

    Returns:
        list[Family]: The age (only after a success) and the pull counts.

    Raises:
        None
    """
    from datetime import UTC, datetime

    from vakr.replication import status

    record = status.read_status(paths.state_dir / status.FILE)
    if record is None:
        return []
    age = record.success_age_s(datetime.now(UTC))
    return [build("vakr_replication_metadata_age_seconds", [((), age)]),
            build("vakr_replication_metadata_pulls_total",
                  [((k,), float(v)) for k, v in sorted(record.pulls.items())])]  # fmt: skip


def api_metrics(
    paths: InstallPaths, metadata: MetadataUnitOfWorkFactory, overview: OverviewQueries
) -> MetricsService:
    """Build the API's metrics service with every reader.

    Args:
        paths: Install layout; the snapshot is ``<run dir>/collector-metrics.json``.
        metadata: Opens metadata units of work.
        overview: The status query service.

    Returns:
        MetricsService: Ready for the context.

    Raises:
        None
    """
    readers = (partial(state_families, metadata, overview), partial(storage_families, paths),
               partial(replication_families, paths))  # fmt: skip
    return MetricsService(snapshot_path=paths.run_dir / SNAPSHOT_FILE, readers=readers)
