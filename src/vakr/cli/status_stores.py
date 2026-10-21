"""Open the installed databases for the status views' local path (``vakr status``, ``vakr tui``).

This is the composition root for the query services: the only place the
status views touch an adapter. It is used with ``--local``, or when
``vakr-api`` is not running (``vakr.cli.status_source``, E.29 #10). It
never creates a database. A node where ``metadata.db`` does not exist yet
has "no data yet"; a node without ``telemetry.db``, or with one this
release cannot read, still shows its inventory, with every status
"Unknown — telemetry unavailable" (13.4).

The SQLite stores offer no read-only mode yet, so connections open
read-write, which never creates a missing file; the services issue only
reads, inside deferred transactions that take no write lock, so the
collector, the only telemetry writer, is never blocked.

Dependencies:
    vakr.adapters.sqlite.metadata, telemetry: The stores.
    vakr.config.loader: Collection defaults the services judge evidence by.
    vakr.query: ``StatusQueries``.

Safety:
    Reads ``/etc/vakr`` configuration but never ``/etc/vakr/secrets``.
    Error messages name files, never their contents.

Example:
    >>> NoDataYetError("no data yet").args
    ('no data yet',)
"""

import sys
from collections.abc import Callable
from dataclasses import dataclass

from vakr.adapters.sqlite.metadata import METADATA_FILE, SqliteMetadataStore, sqlite_uow_factory
from vakr.adapters.sqlite.telemetry import (
    TELEMETRY_FILE,
    SqliteTelemetryStore,
    sqlite_telemetry_factory,
)
from vakr.config.loader import ConfigError, EffectiveConfig, load_effective_config
from vakr.domain.errors import StorageError
from vakr.paths import InstallPaths
from vakr.ports.migrations import MigrationError
from vakr.ports.telemetry import TelemetryBackend
from vakr.query import QuerySettings, StatusQueries, settings_from_config


class NoDataYetError(Exception):
    """The node has no metadata database yet: nothing has been installed or migrated."""


@dataclass(frozen=True, slots=True)
class StatusStores:
    """Open stores and the service over them.

    Attributes:
        queries: The query service.
        config: The effective configuration it was built from.
        close: Releases the stores' connections.
    """

    queries: StatusQueries
    config: EffectiveConfig
    close: Callable[[], None]


def load_config(paths: InstallPaths, command: str) -> EffectiveConfig:
    """Load configuration leniently: a bad file must not hide current state.

    Args:
        paths: Installation layout.
        command: Command name for warnings, such as ``"vakr status"``.

    Returns:
        EffectiveConfig: The valid part of the configuration, or the
        packaged defaults when the files cannot be read at all.

    Raises:
        None
    """
    try:
        config = load_effective_config(paths.config_dir, strict=False)
    except (ConfigError, OSError) as exc:
        print(f"{command}: using packaged defaults: {exc}", file=sys.stderr)
        return EffectiveConfig({})
    if config.issues:
        print(f"{command}: configuration has problems; run vakr config validate", file=sys.stderr)
    return config


def _unreadable(detail: str) -> Callable[[], TelemetryBackend]:
    """Return a telemetry opener that reports why the store cannot be read.

    Args:
        detail: Operator explanation.

    Returns:
        Callable: Raises ``StorageError`` each time it is called.

    Raises:
        None
    """

    def fail() -> TelemetryBackend:
        """Refuse to open the store.

        Args:
            None

        Returns:
            TelemetryBackend: Never.

        Raises:
            StorageError: Always, with ``detail``.
        """
        raise StorageError(detail, retryable=False)

    return fail


def open_status(
    paths: InstallPaths, command: str, config: EffectiveConfig | None = None
) -> StatusStores:
    """Open both databases without creating either, and build the service.

    Args:
        paths: Installation layout (``VAKR_ROOT`` relocates it in tests).
        command: Command name for warnings.
        config: The configuration already loaded by the caller; loaded here when ``None``.

    Returns:
        StatusStores: The service and how to close it.

    Raises:
        NoDataYetError: If ``metadata.db`` does not exist.
        MigrationError: If the metadata schema is not current.
        StorageError: If the metadata database cannot be read.
    """
    config = config if config is not None else load_config(paths, command)
    try:
        settings = settings_from_config(config)
    except ValueError as exc:
        print(f"{command}: using packaged defaults: {exc}", file=sys.stderr)
        settings = QuerySettings()
    meta_path = paths.state_dir / METADATA_FILE
    if not meta_path.exists():
        raise NoDataYetError(
            f"no data yet: {meta_path} does not exist "
            "(install the package or run vakr db migrate, then start the collector)"
        )
    meta = SqliteMetadataStore(meta_path)
    tele_path = paths.state_dir / TELEMETRY_FILE
    closers: list[Callable[[], None]] = [meta.close]
    telemetry: Callable[[], TelemetryBackend] | None = None
    if tele_path.exists():
        try:
            store = SqliteTelemetryStore(tele_path)
        except (MigrationError, StorageError) as exc:
            telemetry = _unreadable(f"telemetry database unusable: {exc}")  # as vakr-api says
        else:
            telemetry = sqlite_telemetry_factory(store)
            closers.append(store.close)

    def close() -> None:
        """Close every store that was opened.

        Args:
            None

        Returns:
            None

        Raises:
            None
        """
        for closer in closers:
            closer()

    return StatusStores(StatusQueries(sqlite_uow_factory(meta), telemetry, settings), config, close)
