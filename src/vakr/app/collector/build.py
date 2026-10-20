"""Build the collector from configuration and the install layout, running the preflight (16.3).

``build_parts`` runs every preflight check in order, each blocking step in
a worker thread, and assembles what the service needs: the validated
settings, both stores, the credential provider, this collector's identity
(registered in the metadata database by name, so it is stable across
restarts, MPOL-006), the opened spool, whose directory lock makes a second
collector with the same identity fail at once (16.3: collector UUID
uniqueness), the restored rate baselines, the protocol adapters, and the
inventory source. If any step fails, everything already opened is closed
and ``PreflightError`` reaches the caller before any discovery or poll.

Dependencies:
    vakr.app.collector.preflight: The checks.
    vakr.adapters.icmp, vakr.adapters.snmp: The protocol adapters.
    vakr.ingest: The spool.

Safety:
    Nothing here polls. The credential provider is strict unless the
    caller passes ``development=True`` (developer workstations only).

Example:
    >>> CollectorParts.__name__
    'CollectorParts'
"""

import asyncio
import contextlib
from dataclasses import dataclass, field
from pathlib import Path

from vakr.adapters.icmp.transport import SocketIcmpProber
from vakr.adapters.sqlite.metadata import SqliteMetadataStore, sqlite_uow_factory
from vakr.adapters.sqlite.telemetry import SqliteTelemetry, SqliteTelemetryStore
from vakr.app.collector import preflight
from vakr.app.collector.baselines import BaselineStore
from vakr.app.collector.checkpoint import boot_id, checkpoint_path, load_baselines
from vakr.app.collector.identity import IdentityConflictError, resolve_identity
from vakr.app.collector.inventory import InventoryContext, InventorySource, open_inventory
from vakr.app.collector.preflight import PreflightError
from vakr.collectors.icmp.settings import IcmpSettings
from vakr.collectors.icmp.settings import settings_from_config as icmp_settings
from vakr.collectors.snmp.config import SnmpConfig, snmp_config
from vakr.config.loader import EffectiveConfig
from vakr.credentials import FileCredentialProvider
from vakr.domain.errors import StorageError, ValidationError
from vakr.domain.inventory import Collector
from vakr.ingest import Spool, SpoolLockedError, SpoolSettings
from vakr.paths import InstallPaths
from vakr.policy.errors import PolicyError
from vakr.ports.clock import Clock, SystemClock
from vakr.ports.metadata import MetadataUnitOfWorkFactory
from vakr.ports.snmp import SnmpSessionFactory
from vakr.scheduler.settings import SchedulerSettings
from vakr.scheduler.settings import settings_from_config as scheduler_settings


@dataclass(slots=True)
class CollectorParts:
    """Everything the collector service runs on, opened and checked.

    Attributes:
        paths: The install layout.
        config: The validated configuration.
        scheduler: ``[scheduler]`` and ``[polling]`` settings.
        icmp: ``[icmp]`` settings.
        snmp: ``[snmp]`` settings.
        metadata: The metadata store.
        uow_factory: Units of work on it.
        telemetry_store: The telemetry store.
        telemetry: Its ports and ``maintain``.
        provider: The credential provider.
        collector: This collector's identity record.
        spool: The opened spool.
        baselines: Restored rate baselines.
        checkpoint: Where they are checkpointed.
        prober: The ICMP adapter.
        snmp_factory: The SNMP adapter.
        inventory: The inventory source.
        warnings: Preflight warnings, for the log.
        boot_id: This kernel boot, which scopes monotonic time across restarts.
    """

    paths: InstallPaths
    config: EffectiveConfig
    scheduler: SchedulerSettings
    icmp: IcmpSettings
    snmp: SnmpConfig
    metadata: SqliteMetadataStore
    uow_factory: MetadataUnitOfWorkFactory
    telemetry_store: SqliteTelemetryStore
    telemetry: SqliteTelemetry
    provider: FileCredentialProvider
    collector: Collector
    spool: Spool
    baselines: BaselineStore
    checkpoint: Path
    prober: SocketIcmpProber
    snmp_factory: SnmpSessionFactory
    inventory: InventorySource
    warnings: list[str] = field(default_factory=list)
    boot_id: str = ""

    async def close(self) -> None:
        """Close adapters and stores (16.4 step 8); the spool is closed by the service.

        Args:
            None

        Returns:
            None

        Raises:
            None
        """
        await self.prober.aclose()
        self.provider.forget()
        await asyncio.to_thread(self.telemetry_store.close)
        await asyncio.to_thread(self.metadata.close)


def _settings(config: EffectiveConfig) -> tuple[SchedulerSettings, IcmpSettings, SnmpConfig]:
    """Build the per-subsystem settings.

    Args:
        config: The validated configuration.

    Returns:
        tuple[SchedulerSettings, IcmpSettings, SnmpConfig]: The settings.

    Raises:
        PreflightError: If a combination is invalid.
    """
    try:
        return scheduler_settings(config), icmp_settings(config), snmp_config(config)
    except (ValueError, ValidationError) as exc:
        raise PreflightError("config", f"{exc}; run `vakr config validate`") from None


async def _open_spool(config: EffectiveConfig, paths: InstallPaths, collector: Collector) -> Spool:
    """Open the collector's spool, which also locks its identity to this process.

    Args:
        config: For ``[spool]``.
        paths: For the default spool root.
        collector: Whose spool to open.

    Returns:
        Spool: Open and recovered.

    Raises:
        PreflightError: If the settings are invalid, the spool is locked, or
            its directory cannot be prepared.
    """
    try:
        spool = Spool(SpoolSettings.from_config(config, paths), collector.id)
        await spool.open()
    except ValidationError as exc:
        raise PreflightError("spool", f"{exc}; run `vakr config validate`") from None
    except SpoolLockedError:
        raise PreflightError(
            "spool",
            f"another vakr collector holds the spool of collector "
            f"{collector.name} ({collector.id}); stop it first, or give this "
            "one its own scheduler.collector_name",
        ) from None
    except OSError as exc:
        raise PreflightError(
            "spool",
            f"cannot prepare the spool directory ({exc.strerror}); "
            "check that /var/lib/vakr belongs to vakr",
        ) from None
    return spool


async def _identity(
    uow_factory: MetadataUnitOfWorkFactory, config: EffectiveConfig, state_dir: Path, name: str
) -> Collector:
    """Resolve this collector's persisted identity and register it in metadata (MPOL-006).

    Args:
        uow_factory: Units of work on the metadata database.
        config: For ``node.position``.
        state_dir: Where the identity file lives.
        name: ``scheduler.collector_name``, resolved.

    Returns:
        Collector: The identity record.

    Raises:
        PreflightError: If the identity is damaged, conflicting, or cannot be stored.
    """
    position = str(config.get("node.position", "") or "") or name
    try:
        return await asyncio.to_thread(resolve_identity, uow_factory, state_dir, name, position)
    except IdentityConflictError as exc:
        raise PreflightError("identity", str(exc)) from None
    except (StorageError, ValidationError, ValueError, OSError) as exc:
        what = getattr(exc, "strerror", None) or exc
        raise PreflightError("identity", f"cannot register collector {name}: {what}") from None


async def build_parts(
    paths: InstallPaths,
    *,
    development: bool = False,
    clock: Clock | None = None,
    snmp_factory: SnmpSessionFactory | None = None,
) -> CollectorParts:
    """Run the startup preflight and open everything the collector needs (16.3).

    Args:
        paths: The install layout.
        development: Tolerate unsafe credential-file permissions, with warnings.
        clock: Time source; the system clocks by default.
        snmp_factory: SNMP adapter to use; PySNMP over the credential
            provider by default (tests pass one with an in-memory resolver).

    Returns:
        CollectorParts: Ready for ``CollectorService``.

    Raises:
        PreflightError: If any check fails; nothing stays open.
    """
    clock = clock or SystemClock()
    config = await asyncio.to_thread(preflight.check_config, paths.config_dir)
    sched, icmp, snmp = _settings(config)
    await asyncio.to_thread(preflight.check_state_dir, paths.state_dir)
    async with contextlib.AsyncExitStack() as undo:
        metadata = await asyncio.to_thread(preflight.open_metadata, paths.state_dir)
        undo.push_async_callback(asyncio.to_thread, metadata.close)
        store = await asyncio.to_thread(preflight.open_telemetry, paths.state_dir, config)
        undo.push_async_callback(asyncio.to_thread, store.close)
        creds = await asyncio.to_thread(preflight.open_credentials, paths.secrets_dir, development)
        provider, warnings = creds[0], [w for w in (creds[1],) if w]
        uow_factory = sqlite_uow_factory(metadata)
        collector = await _identity(uow_factory, config, paths.state_dir, sched.rule.collector_name)
        spool = await _open_spool(config, paths, collector)
        undo.push_async_callback(spool.close)
        warnings += filter(None, [await asyncio.to_thread(preflight.check_icmp_capability)])
        path = checkpoint_path(paths.state_dir, collector.id)
        baselines = await asyncio.to_thread(load_baselines, path, collector.id)
        context = InventoryContext(config, uow_factory, collector, clock, paths.config_dir)
        inventory = await _inventory(context)
        parts = CollectorParts(
            paths, config, sched, icmp, snmp, metadata, uow_factory, store,
            SqliteTelemetry(store), provider, collector, spool, baselines, path,
            SocketIcmpProber(clock=clock, max_in_flight=icmp.max_in_flight),
            snmp_factory or await asyncio.to_thread(pysnmp_factory, provider, clock), inventory,
            warnings, await asyncio.to_thread(boot_id),
        )  # fmt: skip
        undo.pop_all()
    return parts


def pysnmp_factory(provider: FileCredentialProvider, clock: Clock) -> SnmpSessionFactory:
    """Build the PySNMP adapter, importing it only when needed.

    Loading PySNMP takes a noticeable fraction of a second, so commands that
    never poll do not pay for it.

    Args:
        provider: Resolves credential names at session open.
        clock: Time source.

    Returns:
        SnmpSessionFactory: The adapter.

    Raises:
        None
    """
    from vakr.adapters.snmp import PysnmpSessionFactory

    return PysnmpSessionFactory(provider, clock=clock)


async def _inventory(context: InventoryContext) -> InventorySource:
    """Open the inventory source.

    Args:
        context: What it may need.

    Returns:
        InventorySource: The source.

    Raises:
        PreflightError: If it cannot reach the metadata database, or
            ``policies.toml`` or the keyword settings are invalid.
    """
    try:
        return await asyncio.to_thread(open_inventory, context)
    except StorageError as exc:
        raise PreflightError("inventory", f"cannot open inventory: {exc}") from None
    except PolicyError as exc:
        message = f"invalid collection rules: {exc}; run `vakr policy validate`"
        raise PreflightError("policies", message) from None
