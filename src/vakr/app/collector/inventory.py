"""What the collector needs from inventory, and the seam where the real service plugs in.

The collector polls what inventory says, with the poll plan inventory keeps
for each device (6.2: "collectors read their poll plans from the metadata
repository and receive change notifications through a lightweight metadata
change counter"). ``InventorySource`` is that contract, four synchronous
methods; the collector calls each one through ``asyncio.to_thread`` because
they read the metadata database.

``vakr.inventory.service.InventoryService`` implements it. This package does
not import it: ``INVENTORY_FACTORY`` (start) and ``INVENTORY_RELOAD`` (SIGHUP)
are the only seams, and until the merge wires them, the collector runs on
``EmptyInventory``, which has no devices, so a fresh install idles instead of
polling or failing. ``poll_plan`` may raise ``NotFoundError`` for a device
removed a moment ago; the collector then leaves that device out.

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
    """

    config: EffectiveConfig
    uow_factory: MetadataUnitOfWorkFactory
    collector: Collector
    clock: Clock


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


def _empty(context: InventoryContext) -> InventorySource:  # noqa: ARG001 - factory shape
    """Build the placeholder inventory.

    Args:
        context: Unused until the real service is wired.

    Returns:
        InventorySource: ``EmptyInventory``.

    Raises:
        None
    """
    return EmptyInventory()


INVENTORY_FACTORY: InventoryFactory = _empty
"""The merge seam: a factory that builds ``vakr.inventory.service.InventoryService``."""


def open_inventory(context: InventoryContext) -> InventorySource:
    """Build the inventory source the collector polls from.

    Args:
        context: Configuration, metadata access, and identity.

    Returns:
        InventorySource: The configured implementation.

    Raises:
        StorageError: If the implementation cannot reach the metadata database.
    """
    return INVENTORY_FACTORY(context)


type InventoryReload = Callable[[InventorySource, EffectiveConfig], None]


def _keep(inventory: InventorySource, config: EffectiveConfig) -> None:
    """Leave the placeholder inventory as it is.

    Args:
        inventory: The running source.
        config: The new configuration.

    Returns:
        None

    Raises:
        None
    """


INVENTORY_RELOAD: InventoryReload = _keep
"""The merge seam for SIGHUP: hand a new policy to the source, for example
``InventoryService.replace_policy(policy, keywords)``, built from the configuration."""


def reload_inventory(inventory: InventorySource, config: EffectiveConfig) -> None:
    """Tell the inventory source about a reloaded configuration (blocking; worker thread).

    Args:
        inventory: The running source.
        config: The validated new configuration.

    Returns:
        None

    Raises:
        Exception: Whatever the source raises; the caller logs it and keeps running.
    """
    INVENTORY_RELOAD(inventory, config)
