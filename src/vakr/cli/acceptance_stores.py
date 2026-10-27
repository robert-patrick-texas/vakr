"""Open the installed databases for ``vakr acceptance``: the reports' composition root (E.35).

This is the only place the acceptance reports touch an adapter. It opens
``metadata.db`` and ``telemetry.db`` in ``/var/lib/vakr`` without creating
either (as ``vakr status --local`` does), reads each collector's
telemetry-loss incidents from its spool directory, and names this node's
collector, so a peer's silence at the end of the window can be told apart
as replication lag. Run as root, it first becomes the ``vakr`` service
user. The SQLite stores have no read-only mode yet; the reports issue only
reads, in deferred transactions that never block the collector.

Dependencies:
    vakr.adapters.sqlite: The stores.
    vakr.config, vakr.ingest: Configuration and the incident log.
    vakr.query.acceptance: The report service.

Safety:
    Reads ``/etc/vakr`` configuration but never ``/etc/vakr/secrets``.
    Error messages name files, never their contents.

Example:
    >>> loss_ranges([]) == ()
    True
"""

import socket
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

from vakr.adapters.sqlite.metadata import METADATA_FILE, SqliteMetadataStore, sqlite_uow_factory
from vakr.adapters.sqlite.telemetry import (
    TELEMETRY_FILE,
    SqliteTelemetryStore,
    sqlite_telemetry_factory,
)
from vakr.cli.service_user import drop_to_service_user
from vakr.cli.status_stores import NoDataYetError, load_config
from vakr.domain.errors import StorageError, ValidationError
from vakr.domain.ids import CollectorId
from vakr.ingest.incidents import LossIncident, read_incidents
from vakr.ingest.settings import SpoolSettings
from vakr.paths import InstallPaths
from vakr.query.acceptance import AcceptanceQueries
from vakr.query.acceptance_evidence import LossRange
from vakr.query.settings import settings_from_config


@dataclass(frozen=True, slots=True)
class AcceptanceStores:
    """The report service and how to release its stores.

    Attributes:
        queries: The service.
        close: Closes both stores.
    """

    queries: AcceptanceQueries
    close: Callable[[], None]


def loss_ranges(incidents: Sequence[LossIncident]) -> tuple[LossRange, ...]:
    """Reduce telemetry-loss incidents to what the gap report needs.

    Args:
        incidents: Incidents from one collector's log.

    Returns:
        tuple[LossRange, ...]: Kind, range, classes, and detail of each.

    Raises:
        None
    """
    return tuple(LossRange(i.kind.value, i.start_us, i.end_us, i.classes, i.detail)
                 for i in incidents)  # fmt: skip


def _loss_reader(spool_root: Path | None) -> Callable[[CollectorId], tuple[LossRange, ...]]:
    """Return a reader of one collector's loss incidents from ``<spool root>/<UUID>``.

    Args:
        spool_root: The spool root, or ``None`` when the configuration names none usable.

    Returns:
        Callable: Collector UUID to its incidents; none when the log is unreadable.

    Raises:
        None
    """

    def read(collector: CollectorId) -> tuple[LossRange, ...]:
        """Read one collector's incidents.

        Args:
            collector: The collector.

        Returns:
            tuple[LossRange, ...]: Its incidents, oldest first.

        Raises:
            None
        """
        if spool_root is None:
            return ()
        return loss_ranges(read_incidents(spool_root / str(collector)))

    return read


def open_acceptance(paths: InstallPaths, command: str) -> AcceptanceStores:
    """Become the service user, open both databases, and build the report service.

    Args:
        paths: Installation layout (``VAKR_ROOT`` relocates it in tests).
        command: Command name for warnings.

    Returns:
        AcceptanceStores: The service and its closer.

    Raises:
        NoDataYetError: If either database does not exist.
        MigrationError: If a schema is not current.
        StorageError: If a database cannot be read, or the user switch fails.
    """
    if (problem := drop_to_service_user()) is not None:
        raise StorageError(problem, retryable=False)
    config = load_config(paths, command)
    settings = settings_from_config(config, socket.gethostname())
    meta_path, tele_path = paths.state_dir / METADATA_FILE, paths.state_dir / TELEMETRY_FILE
    for path in (meta_path, tele_path):
        if not path.exists():
            raise NoDataYetError(f"no data yet: {path} does not exist")
    try:
        spool_root: Path | None = SpoolSettings.from_config(config, paths).root
    except ValidationError:
        spool_root = None
    meta = SqliteMetadataStore(meta_path)
    try:
        tele = SqliteTelemetryStore(tele_path)
    except BaseException:
        meta.close()
        raise

    def close() -> None:
        """Close both stores.

        Args:
            None

        Returns:
            None

        Raises:
            None
        """
        tele.close()
        meta.close()

    queries = AcceptanceQueries(
        sqlite_uow_factory(meta), sqlite_telemetry_factory(tele),
        losses=_loss_reader(spool_root), local_collector=settings.local_collector or None,
        strategy=settings.strategy,
    )  # fmt: skip
    return AcceptanceStores(queries, close)
