"""``vakr collector once DEVICE``: one diagnostic poll cycle, exactly as the collector polls (18.3).

When a device looks wrong in the views, an operator wants to see what the
collector would do with it right now: the same configuration, the same
inventory record and poll plan, the same ICMP gate, Stage 1 and Stage 2,
from this host. ``poll_once`` runs one poll through the real steps and the
scheduler's state machine (``run_poll``) and returns what happened; nothing
is written to the spool, the telemetry store, or inventory, and the spool
lock is not taken, so it is safe beside a running collector.

Rates need two samples, so a single cycle shows raw counters only; the
running collector derives rates against its own previous sample.

Dependencies:
    vakr.app.collector: Preflight checks, steps, inventory.
    vakr.scheduler.runner: The poll state machine.
    vakr.adapters: ICMP and SNMP.

Safety:
    Read-only towards storage; credentials stay in the SNMP adapter.

Example:
    >>> find_device((), "core1") is None
    True
"""

import asyncio
from collections.abc import Sequence
from dataclasses import dataclass
from uuid import UUID

from vakr.adapters.icmp.transport import SocketIcmpProber
from vakr.adapters.sqlite.metadata import sqlite_uow_factory
from vakr.app.collector import preflight
from vakr.app.collector.baselines import BaselineStore
from vakr.app.collector.build import pysnmp_factory
from vakr.app.collector.checkpoint import boot_id
from vakr.app.collector.icmp_step import CollectorIcmpStep
from vakr.app.collector.identity import COLLECTOR_OWNER, identity_path, load_identity
from vakr.app.collector.inventory import InventoryContext, open_inventory
from vakr.app.collector.plans import PlanBook
from vakr.app.collector.replan import build_targets
from vakr.app.collector.snmp_step import CollectorSnmpStep
from vakr.app.collector.tally import FaultTally
from vakr.collectors.icmp.cycle import IcmpCollector
from vakr.collectors.icmp.settings import settings_from_config as icmp_settings
from vakr.collectors.snmp import PollPlan
from vakr.collectors.snmp.config import snmp_config
from vakr.config.loader import EffectiveConfig
from vakr.credentials import FileCredentialProvider
from vakr.domain.errors import NotFoundError
from vakr.domain.ids import CollectorId, PollId, new_id
from vakr.domain.inventory import Collector, Device
from vakr.paths import InstallPaths
from vakr.ports.clock import Clock, SystemClock
from vakr.ports.metadata import MetadataUnitOfWorkFactory
from vakr.ports.snmp import SnmpSessionFactory
from vakr.scheduler.runner import PollProgress, run_poll
from vakr.scheduler.settings import settings_from_config as scheduler_settings
from vakr.scheduler.steps import PollContext, PollSteps, StepStatus
from vakr.scheduler.targets import PollDefaults, PollTarget


@dataclass(frozen=True, slots=True)
class OnceResult:
    """What one diagnostic cycle did.

    Attributes:
        target: The device and its resolved settings.
        plan: The poll plan used; ``None`` when the device is not discovered yet.
        progress: Steps and observations.
        elapsed_s: Duration of the cycle.
        collector: The collector name the cycle ran as.
    """

    target: PollTarget
    plan: PollPlan | None
    progress: PollProgress
    elapsed_s: float
    collector: str


def find_device(devices: Sequence[Device], query: str) -> Device | None:
    """Find a device by name or UUID.

    Args:
        devices: The inventory.
        query: A device name, or its UUID.

    Returns:
        Device | None: The device, or ``None``.

    Raises:
        None
    """
    try:
        wanted: UUID | None = UUID(query)
    except ValueError:
        wanted = None
    return next((d for d in devices if d.name == query or d.id == wanted), None)


def _collector(
    uow_factory: MetadataUnitOfWorkFactory, known: CollectorId | None, name: str
) -> Collector | None:
    """Look up this collector's identity without creating it.

    Args:
        uow_factory: Units of work on the metadata database.
        known: The UUID from the identity file, if any.
        name: The collector name.

    Returns:
        Collector | None: The record, if the service has registered it.

    Raises:
        StorageError: If the metadata database is unavailable.
    """
    with uow_factory() as uow:
        found = None if known is None else uow.collectors.get(known)
        return found or uow.collectors.get_by_name(name)


@dataclass(frozen=True, slots=True)
class _Loaded:
    """What the cycle needs from configuration and inventory.

    Attributes:
        config: The validated configuration.
        collector: The identity to poll as.
        target: The device.
        plan: Its plan, or ``None``.
        deadline_s: ``scheduler.poll_deadline``.
    """

    config: EffectiveConfig
    collector: Collector
    target: PollTarget
    plan: PollPlan | None
    deadline_s: float


def _pick(devices: Sequence[Device], query: str, defaults: PollDefaults) -> PollTarget:
    """Resolve the requested device into a poll target.

    Args:
        devices: The inventory.
        query: Name or UUID.
        defaults: ``[polling]`` defaults.

    Returns:
        PollTarget: The device with its resolved interval and strategy.

    Raises:
        PreflightError: If there is no such device, or it is not polled.
    """
    device = find_device(devices, query)
    if device is None:
        raise preflight.PreflightError("device", f"no device {query!r} in this inventory")
    targets = build_targets([device], defaults, None)
    if not targets:
        raise preflight.PreflightError(
            "device", f"{device.name} is {device.admin_state.value}, so it is not polled"
        )
    return targets[0]


async def _load(paths: InstallPaths, query: str, clock: Clock) -> _Loaded:
    """Read the configuration, the device, and its plan.

    Args:
        paths: The install layout.
        query: Name or UUID of the device.
        clock: Time source.

    Returns:
        _Loaded: Identity, target, plan, and deadline.

    Raises:
        PreflightError: If the configuration, database, or device is unusable.
    """
    config = await asyncio.to_thread(preflight.check_config, paths.config_dir)
    sched = scheduler_settings(config)
    store = await asyncio.to_thread(preflight.open_metadata, paths.state_dir)
    try:
        uow = sqlite_uow_factory(store)
        name = sched.rule.collector_name
        try:
            known = load_identity(identity_path(paths.state_dir))
        except OSError, ValueError:
            known = None
        found = await asyncio.to_thread(_collector, uow, known, name)
        collector = found or Collector(new_id(CollectorId), name, name, COLLECTOR_OWNER)
        context = InventoryContext(config, uow, collector, clock)
        inventory = await asyncio.to_thread(open_inventory, context)
        target = _pick(await asyncio.to_thread(inventory.devices), query, sched.defaults)
        try:
            plan = await asyncio.to_thread(inventory.poll_plan, target.device_id)
        except NotFoundError:
            raise preflight.PreflightError("device", f"{query!r} was just removed") from None
    finally:
        await asyncio.to_thread(store.close)
    return _Loaded(config, collector, target, plan, sched.poll_deadline_s)


async def poll_once(
    paths: InstallPaths,
    query: str,
    *,
    development: bool = False,
    clock: Clock | None = None,
    snmp_factory: SnmpSessionFactory | None = None,
) -> OnceResult:
    """Run one poll cycle of one device through the collector's own steps.

    Args:
        paths: The install layout.
        query: Device name or UUID.
        development: Tolerate unsafe credential-file permissions.
        clock: Time source; the system clocks by default.
        snmp_factory: SNMP adapter; PySNMP over the credentials file by default.

    Returns:
        OnceResult: The cycle.

    Raises:
        PreflightError: If the configuration, database, or device is unusable.
    """
    clock = clock or SystemClock()
    loaded = await _load(paths, query, clock)
    icmp, snmp = icmp_settings(loaded.config), snmp_config(loaded.config)
    provider = await asyncio.to_thread(FileCredentialProvider, paths.secrets_dir,
                                       development=development)  # fmt: skip
    boot = await asyncio.to_thread(boot_id)
    snmp_factory = snmp_factory or await asyncio.to_thread(pysnmp_factory, provider, clock)
    plans, tally, target = PlanBook(), FaultTally(), loaded.target
    if loaded.plan is not None:
        plans.adopt(target.device_id, loaded.plan, clock.now_utc())
    progress, began = PollProgress(), clock.monotonic()
    async with SocketIcmpProber(clock=clock, max_in_flight=icmp.max_in_flight) as prober:
        steps = PollSteps(
            CollectorIcmpStep(IcmpCollector(prober, icmp), tally),
            CollectorSnmpStep(snmp_factory, snmp, plans, BaselineStore(), clock, tally, boot),
        )
        ctx = PollContext(new_id(PollId), loaded.collector.id, target, clock.now_utc())
        try:
            async with asyncio.timeout(loaded.deadline_s):
                await run_poll(ctx, steps, progress, lambda: round(clock.monotonic() * 1e6))
        except TimeoutError:
            progress.end(StepStatus.DEADLINE, round(clock.monotonic() * 1e6))
    provider.forget()
    elapsed = clock.monotonic() - began
    return OnceResult(target, loaded.plan, progress, elapsed, loaded.collector.name)
