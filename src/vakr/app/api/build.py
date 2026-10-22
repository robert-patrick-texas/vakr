"""Build ``vakr-api`` from the installed databases, running its startup checks (16.3, SVC-005).

``build_parts`` checks what the API needs before it listens: valid
configuration (including ``[api]``), a writable state directory, the
metadata database at this release's schema (the API writes sessions,
audit records, and identity decisions), and the installation key
``/var/lib/vakr/auth.key``, created on first start if missing (E.12). Any
failure is a ``PreflightError`` with an operator sentence, and the process
exits 78. The telemetry database is optional: without it, or with one this
release cannot read, the API still serves inventory and current state with
every telemetry view marked unavailable (13.4, invariant 2).

This is the only module of the API that knows the concrete adapters
(SQLite); everything under ``vakr.api`` sees ports and query services.

Dependencies:
    vakr.app.collector.preflight: The shared configuration and schema checks.
    vakr.adapters.sqlite: The two stores.
    vakr.auth, vakr.query: The services.

Safety:
    Blocking: call in a worker thread. Telemetry is opened through
    ``ReadOnlyTelemetry``, so nothing here can write it. Error messages name
    files and checks, never their contents.

Example:
    >>> ApiParts.__name__
    'ApiParts'
"""

import socket
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import cast

from vakr.adapters.sqlite.metadata import sqlite_uow_factory
from vakr.adapters.sqlite.telemetry import (
    TELEMETRY_FILE,
    SqliteTelemetry,
    SqliteTelemetryStore,
    retention_policy,
)
from vakr.api.context import ApiContext
from vakr.api.settings import ApiSettings, api_settings_from_config
from vakr.app.api.metrics_storage import api_metrics
from vakr.app.api.readonly import ReadOnlyTelemetry
from vakr.app.collector import preflight
from vakr.app.collector.preflight import PreflightError
from vakr.auth import (
    KEY_FILE,
    AuthKeyError,
    AuthKeys,
    LocalGroups,
    Passwords,
    auth_settings_from_config,
    build_auth_services,
    load_or_create_keys,
    local_groups,
)
from vakr.config.loader import EffectiveConfig
from vakr.domain.errors import StorageError, ValidationError
from vakr.ingest.incidents import LossIncident, read_incidents
from vakr.ingest.settings import SpoolSettings
from vakr.operations import operation_service
from vakr.paths import InstallPaths
from vakr.ports.clock import Clock, SystemClock
from vakr.ports.migrations import MigrationError
from vakr.ports.telemetry import TelemetryBackend
from vakr.query import HistoryQueries, OverviewQueries, settings_from_config

type TelemetryOpener = Callable[[], TelemetryBackend]


@dataclass(slots=True)
class ApiParts:
    """Everything the API process runs on, opened and checked.

    Attributes:
        context: The shared services.
        config: The validated configuration.
        groups: GIDs of ``vakr-admin``, ``vakr``, and ``vakr-view`` (socket roles).
        closers: Release the stores, in order.
    """

    context: ApiContext
    config: EffectiveConfig
    groups: LocalGroups
    closers: list[Callable[[], None]]

    def close(self) -> None:
        """Close every store that was opened.

        Args:
            None

        Returns:
            None

        Raises:
            None
        """
        for closer in self.closers:
            closer()


def _settings(config: EffectiveConfig, paths: InstallPaths) -> ApiSettings:
    """Read ``[api]``, refusing an inconsistent section.

    Args:
        config: The validated configuration.
        paths: Install layout.

    Returns:
        ApiSettings: The settings.

    Raises:
        PreflightError: If a value is invalid.
    """
    try:
        return api_settings_from_config(config, paths)
    except ValueError as exc:
        raise PreflightError("config", f"{exc}; run `vakr config validate`") from None


def _telemetry(
    paths: InstallPaths, config: EffectiveConfig
) -> tuple[TelemetryOpener | None, str, list[Callable[[], None]]]:
    """Open the telemetry database read-only if it is usable.

    Args:
        paths: Install layout.
        config: For the retention policy (rollup horizon).

    Returns:
        tuple: The opener (``None`` without a database), ``ok`` or why not, and closers.

    Raises:
        None
    """
    path = paths.state_dir / TELEMETRY_FILE
    if not path.exists():
        return None, "no telemetry database yet; start the collector", []
    try:
        store = SqliteTelemetryStore(path, policy=retention_policy(config))
    except (MigrationError, StorageError) as exc:
        reason = f"telemetry database unusable: {exc}"

        def unreadable() -> TelemetryBackend:
            """Refuse to open the store.

            Args:
                None

            Returns:
                TelemetryBackend: Never.

            Raises:
                StorageError: Always.
            """
            raise StorageError(reason, retryable=False)

        return unreadable, reason, []

    def open_view() -> TelemetryBackend:
        """Return a read-only view of the shared store.

        Args:
            None

        Returns:
            TelemetryBackend: The view.

        Raises:
            None
        """
        return cast(TelemetryBackend, ReadOnlyTelemetry(SqliteTelemetry(store)))

    return open_view, "ok", [store.close]


def incident_reader(
    config: EffectiveConfig, paths: InstallPaths
) -> Callable[[], Sequence[LossIncident]]:
    """Read every collector spool's telemetry-loss incidents (overview feed, read-only).

    Args:
        config: For ``[spool]``.
        paths: Install layout.

    Returns:
        Callable: Returns the incidents; an unreadable spool contributes none.

    Raises:
        None
    """
    try:
        root = SpoolSettings.from_config(config, paths).root
    except ValidationError:
        return list

    def read() -> list[LossIncident]:
        """Collect incidents from each collector's spool directory.

        Args:
            None

        Returns:
            list[LossIncident]: Oldest first per collector.

        Raises:
            None
        """
        try:
            spools = sorted(p for p in root.iterdir() if p.is_dir())
        except OSError:
            return []
        return [i for spool in spools for i in read_incidents(spool)]

    return read


def _keys(state_dir: Path) -> AuthKeys:
    """Load or create the installation key.

    Args:
        state_dir: ``/var/lib/vakr``.

    Returns:
        AuthKeys: The derived keys.

    Raises:
        PreflightError: If the key file is unsafe, damaged, or cannot be written.
    """
    try:
        return load_or_create_keys(state_dir / KEY_FILE)
    except AuthKeyError as exc:
        raise PreflightError("auth_key", str(exc)) from None
    except OSError as exc:
        raise PreflightError("auth_key", f"cannot use {state_dir / KEY_FILE} "
                             f"({exc.strerror}); it must belong to vakr") from None  # fmt: skip


def build_parts(
    paths: InstallPaths, *, clock: Clock | None = None, passwords: Passwords | None = None
) -> ApiParts:
    """Run the startup checks and open everything the API needs (blocking).

    Args:
        paths: Install layout.
        clock: Time source; the system clock by default.
        passwords: Hashing; tests pass cheap parameters, production never does.

    Returns:
        ApiParts: Ready for the server.

    Raises:
        PreflightError: If any check fails; nothing stays open.
    """
    clock = clock or SystemClock()
    config = preflight.check_config(paths.config_dir)
    settings = _settings(config, paths)
    preflight.check_state_dir(paths.state_dir)
    metadata = preflight.open_metadata(paths.state_dir)
    closers: list[Callable[[], None]] = []
    try:
        telemetry, state, closers = _telemetry(paths, config)
        auth = build_auth_services(sqlite_uow_factory(metadata), clock=clock,
                                   keys=_keys(paths.state_dir),
                                   settings=auth_settings_from_config(config),
                                   passwords=passwords)  # fmt: skip
        auth.passwords.warm_up()
        uow = sqlite_uow_factory(metadata)
        query = settings_from_config(config, hostname=socket.gethostname())
        overview = OverviewQueries(uow, telemetry, query, clock,
                                   incidents=incident_reader(config, paths))  # fmt: skip
        history = HistoryQueries(uow, telemetry, query, clock)
        context = ApiContext(auth, overview, history, uow, clock, settings, clock.now_utc(),
                             state)  # fmt: skip
        context.operations = operation_service(uow, clock, config)
        context.metrics = api_metrics(paths, uow, overview)
        return ApiParts(context, config, local_groups(), [*closers, metadata.close])
    except BaseException:
        for closer in (*closers, metadata.close):
            closer()
        raise
