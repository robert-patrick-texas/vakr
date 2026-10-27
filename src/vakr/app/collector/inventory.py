"""What the collector needs from inventory, and the seam where the real service plugs in.

The collector polls what inventory says, with the poll plan inventory keeps
for each device (6.2: "collectors read their poll plans from the metadata
repository and receive change notifications through a lightweight metadata
change counter"). ``InventorySource`` is that contract, four synchronous
methods; the collector calls each one through ``asyncio.to_thread`` because
they read the metadata database.

``vakr.inventory.service.InventoryService`` implements it, built by
``vakr.app.collector.inventory_service``. ``INVENTORY_FACTORY`` (start) and
``INVENTORY_RELOAD`` (SIGHUP) are the seams tests replace with fakes;
``EmptyInventory`` remains for tests. A fresh install has no devices, so the
collector idles instead of polling or failing. ``poll_plan`` may raise
``NotFoundError`` for a device removed a moment ago; the collector then
leaves that device out.

Dependencies:
    typing.Protocol: The structural contract.
    vakr.collectors.snmp: ``PollPlan`` and ``plan_from_discovery``.
    vakr.discovery: ``DiscoveryResult``.

Safety:
    Devices carry credential references, never secrets.

Example:
    >>> inventory = EmptyInventory()
    >>> inventory.devices(), inventory.change_counter()
    ((), 0)
"""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Protocol

from vakr.collectors.snmp import PollPlan, plan_from_discovery
from vakr.config.loader import EffectiveConfig
from vakr.discovery import DiscoveryResult
from vakr.domain.ids import DeviceId
from vakr.domain.inventory import Collector, Device
from vakr.ports.clock import Clock
from vakr.ports.metadata import MetadataUnitOfWorkFactory


class InventorySource(Protocol):
    """The devices one collector polls and their poll plans (DISC-001, 6.2)."""

    def devices(self) -> tuple[Device, ...]:
        """Return the devices this collector polls, in a stable order.

        Args:
            None

        Returns:
            tuple[Device, ...]: Every device, including disabled ones, which
            the scheduler then leaves out (9.2).

        Raises:
            StorageError: If the metadata database is unavailable.
        """
        ...

    def change_counter(self) -> int:
        """Return a counter that moves whenever inventory or policy changes.

        Args:
            None

        Returns:
            int: The counter; the collector re-plans when it changes.

        Raises:
            StorageError: If the metadata database is unavailable.
        """
        ...

    def poll_plan(self, device_id: DeviceId) -> PollPlan | None:
        """Return a device's poll plan.

        Args:
            device_id: The device.

        Returns:
            PollPlan | None: The plan, or ``None`` when the device needs
            discovery first (DISC-001).

        Raises:
            StorageError: If the metadata database is unavailable.
        """
        ...

    def record_discovery(
        self, device_id: DeviceId, result: DiscoveryResult, now: datetime
    ) -> PollPlan:
        """Store a discovery result, reconcile interfaces, and return the new plan.

        Args:
            device_id: The device.
            result: What discovery found.
            now: When it finished (UTC).

        Returns:
            PollPlan: The plan to poll with from now on.

        Raises:
            StorageError: If the metadata database is unavailable.
        """
        ...


@dataclass(frozen=True, slots=True)
class InventoryContext:
    """What an ``InventorySource`` implementation may need to start.

    Attributes:
        config: The effective configuration.
        uow_factory: Units of work on the metadata database.
        collector: This collector's identity record.
        clock: UTC and monotonic time.
        config_dir: The configuration directory, which holds ``policies.toml``.
    """

    config: EffectiveConfig
    uow_factory: MetadataUnitOfWorkFactory
    collector: Collector
    clock: Clock
    config_dir: Path


class EmptyInventory:
    """An inventory with no devices: the collector idles until real inventory exists."""

    def devices(self) -> tuple[Device, ...]:
        """Return no devices.

        Args:
            None

        Returns:
            tuple[Device, ...]: Empty.

        Raises:
            None
        """
        return ()

    def change_counter(self) -> int:
        """Return a counter that never moves.

        Args:
            None

        Returns:
            int: 0.

        Raises:
            None
        """
        return 0

    def poll_plan(self, device_id: DeviceId) -> PollPlan | None:  # noqa: ARG002 - contract
        """Return no plan; there are no devices.

        Args:
            device_id: The device.

        Returns:
            PollPlan | None: Always ``None``.

        Raises:
            None
        """
        return None

    def record_discovery(
        self,
        device_id: DeviceId,  # noqa: ARG002 - contract
        result: DiscoveryResult,
        now: datetime,  # noqa: ARG002 - contract
    ) -> PollPlan:
        """Plan straight from the result, without storing anything.

        Args:
            device_id: The device.
            result: What discovery found.
            now: When it finished (UTC).

        Returns:
            PollPlan: Every discovered interface, without persistent IDs.

        Raises:
            None
        """
        return plan_from_discovery(result)


type InventoryFactory = Callable[[InventoryContext], InventorySource]


def _service(context: InventoryContext) -> InventorySource:
    """Build the real inventory service.

    Args:
        context: Configuration, metadata access, identity, and clock.

    Returns:
        InventorySource: ``vakr.inventory.service.InventoryService``.

    Raises:
        PolicyError: If ``policies.toml`` or the keyword settings are invalid.
    """
    # Imported here: the service module imports this one for the contract.
    from vakr.app.collector.inventory_service import open_service

    return open_service(context)


INVENTORY_FACTORY: InventoryFactory = _service
"""Builds the inventory source; tests replace it with a fake."""


def open_inventory(context: InventoryContext) -> InventorySource:
    """Build the inventory source the collector polls from.

    Args:
        context: Configuration, metadata access, and identity.

    Returns:
        InventorySource: The configured implementation.

    Raises:
        StorageError: If the implementation cannot reach the metadata database.
    """
    from vakr.app.collector.replication_loop import PeerInventory
    from vakr.config.node import node_settings

    source = INVENTORY_FACTORY(context)
    # On a peer identity belongs to the primary: discovery must not write (ADR-024).
    if node_settings(context.config).is_peer:
        wrapped: InventorySource = PeerInventory(source)
        return wrapped
    return source


type InventoryReload = Callable[[InventorySource, EffectiveConfig, Path], None]


def _replace_policy(inventory: InventorySource, config: EffectiveConfig, config_dir: Path) -> None:
    """Hand the reloaded rules and keywords to the running service.

    Args:
        inventory: The running source.
        config: The new configuration.
        config_dir: The configuration directory, which holds ``policies.toml``.

    Returns:
        None

    Raises:
        PolicyError: If the reloaded rules are invalid.
    """
    from vakr.app.collector.inventory_service import reload_service

    reload_service(inventory, config, config_dir)


INVENTORY_RELOAD: InventoryReload = _replace_policy
"""Applies a SIGHUP reload to the inventory source; tests replace it."""


def reload_inventory(inventory: InventorySource, config: EffectiveConfig, config_dir: Path) -> None:
    """Tell the inventory source about a reloaded configuration (blocking; worker thread).

    Args:
        inventory: The running source.
        config: The validated new configuration.
        config_dir: The configuration directory, which holds ``policies.toml``.

    Returns:
        None

    Raises:
        Exception: Whatever the source raises; the caller logs it and keeps running.
    """
    INVENTORY_RELOAD(inventory, config, config_dir)
