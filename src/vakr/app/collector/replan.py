"""Re-planning: follow inventory and policy changes without a restart (6.2, SCHED-010).

Collectors learn about changes through the inventory change counter (6.2).
``Replanner.watch`` reads it every ``change_poll_s`` seconds, in a worker
thread; when it moves, or when a reload asks for it, ``replan`` reads the
devices and their plans, resolves poll targets, refreshes each device's
capacity inputs, and hands the result to the scheduler, whose re-plan keeps
unchanged devices on their slots so a change never makes a burst
(``vakr.scheduler.plan``), and to discovery. Baselines and plans of devices
that left inventory are dropped, so memory follows inventory.

A metadata database that is briefly unavailable only delays the next
re-plan; polling continues on the plans already loaded (6.2).

Dependencies:
    asyncio: Worker threads and the wake event.
    vakr.scheduler.targets: Poll targets from devices.
    vakr.app.collector: Plans, baselines, discovery, capacity.

Safety:
    Every inventory read is bounded by the inventory size and runs off the loop.

Example:
    >>> build_targets((), PollDefaults(), None)
    ()
"""

import asyncio
import contextlib
import logging
from collections.abc import Iterable
from dataclasses import replace

from vakr.app.collector.baselines import BaselineStore
from vakr.app.collector.capacity import load_capacity
from vakr.app.collector.discovery import DiscoveryRunner
from vakr.app.collector.inventory import InventorySource
from vakr.app.collector.plans import PlanBook
from vakr.collectors.snmp import PollPlan
from vakr.domain.errors import NotFoundError, StorageError
from vakr.domain.ids import DeviceId, InterfaceId
from vakr.domain.inventory import Device
from vakr.logic.capacity import CapacityInputs
from vakr.ports.clock import Clock
from vakr.ports.metadata import MetadataUnitOfWorkFactory
from vakr.scheduler.driver import Scheduler
from vakr.scheduler.targets import PollDefaults, PollTarget, targets_from_devices

_LOG = logging.getLogger(__name__)


def build_targets(
    devices: Iterable[Device], defaults: PollDefaults, override_s: int | None
) -> tuple[PollTarget, ...]:
    """Resolve devices into poll targets, applying the test-only interval override.

    Args:
        devices: Inventory, in its stable order.
        defaults: ``[polling]`` defaults.
        override_s: ``ServiceTimings.poll_interval_override_s``; ``None`` in production.

    Returns:
        tuple[PollTarget, ...]: Enabled and maintenance devices.

    Raises:
        None
    """
    targets = targets_from_devices(devices, defaults)
    if override_s is None:
        return targets
    return tuple(
        replace(t, configured_interval_s=override_s, effective_interval_s=override_s)
        for t in targets
    )


def read_inventory(
    inventory: InventorySource,
) -> tuple[int, tuple[Device, ...], dict[DeviceId, PollPlan | None]]:
    """Read the change counter, devices, and plans (blocking; worker thread only).

    Args:
        inventory: The source.

    Returns:
        tuple: The counter, the devices, and each device's plan or ``None``. A
        device removed between the two reads is left out, not an error.

    Raises:
        StorageError: If the metadata database is unavailable.
    """
    counter = inventory.change_counter()
    devices, plans = [], {}
    for device in inventory.devices():
        try:
            plans[device.id] = inventory.poll_plan(device.id)
        except NotFoundError:
            continue  # removed since devices() answered; the next re-plan agrees
        devices.append(device)
    return counter, tuple(devices), plans


def read_capacities(
    uow_factory: MetadataUnitOfWorkFactory, device_ids: Iterable[DeviceId]
) -> dict[DeviceId, dict[InterfaceId, CapacityInputs]]:
    """Read capacity inputs of several devices (blocking; worker thread only).

    Args:
        uow_factory: Units of work on the metadata database.
        device_ids: The devices.

    Returns:
        dict[DeviceId, dict[InterfaceId, CapacityInputs]]: Inputs per device.

    Raises:
        StorageError: If the metadata database is unavailable.
    """
    return {device_id: load_capacity(uow_factory, device_id) for device_id in device_ids}


class Replanner:
    """Keeps the scheduler, discovery, plans, and baselines in step with inventory.

    Attributes:
        defaults: ``[polling]`` defaults; replaced on reload.
        counter: The change counter of the last re-plan.
        replans: Re-plans done.
        shortest_interval_s: Shortest effective interval scheduled, if any.
    """

    def __init__(
        self,
        inventory: InventorySource,
        scheduler: Scheduler,
        discovery: DiscoveryRunner,
        plans: PlanBook,
        baselines: BaselineStore,
        uow_factory: MetadataUnitOfWorkFactory,
        clock: Clock,
        override_s: int | None,
    ) -> None:
        """Bind the parts a re-plan touches.

        Args:
            inventory: The source.
            scheduler: The scheduler.
            discovery: The discovery runner.
            plans: Working plans.
            baselines: Rate baselines.
            uow_factory: For capacity reads.
            clock: UTC time for plan ages.
            override_s: Test-only interval override.

        Returns:
            None

        Raises:
            None
        """
        self._inventory, self._scheduler, self._discovery = inventory, scheduler, discovery
        self._plans, self._baselines, self._uow = plans, baselines, uow_factory
        self._clock, self._override = clock, override_s
        self.defaults = PollDefaults()
        self.counter: int | None = None
        self.replans = 0
        self.shortest_interval_s: int | None = None
        self._requested = False
        self._wake = asyncio.Event()

    def request(self) -> None:
        """Ask for a re-plan at once, for example after a reload (CONF-003).

        Args:
            None

        Returns:
            None

        Raises:
            None
        """
        self._requested = True
        self._wake.set()

    async def replan(self) -> int:
        """Read inventory and apply it everywhere.

        Args:
            None

        Returns:
            int: Devices now scheduled.

        Raises:
            StorageError: If the metadata database is unavailable; nothing changes.
        """
        counter, devices, plans = await asyncio.to_thread(read_inventory, self._inventory)
        targets = build_targets(devices, self.defaults, self._override)
        now, scheduled = self._clock.now_utc(), [t.device_id for t in targets]
        for target in targets:
            if (plan := plans.get(target.device_id)) is not None:
                self._plans.adopt(target.device_id, plan, now)
        self._plans.retain(scheduled)
        planned = [d for d in scheduled if self._plans.has(d)]
        for device_id, capacity in (
            await asyncio.to_thread(read_capacities, self._uow, planned)
        ).items():
            self._plans.set_capacity(device_id, capacity)
        summary = self._scheduler.replan(targets)
        self._discovery.replan(targets)
        forgotten = self._baselines.prune(scheduled, self._plans.interface_ids())
        self.counter, self.replans = counter, self.replans + 1
        self.shortest_interval_s = min((t.effective_interval_s for t in targets), default=None)
        _LOG.info(
            "planned %d devices (%d without a poll plan yet): %d added, %d removed, "
            "%d rescheduled, %d kept; %d baselines dropped",
            len(targets),
            len(targets) - len(planned),
            summary.added,
            len(summary.removed),
            summary.rescheduled,
            summary.kept,
            forgotten,
            extra={"event_id": "collector.replanned"},
        )
        return len(targets)

    async def refresh_capacity(self, device_id: DeviceId) -> None:
        """Re-read one device's capacity inputs after discovery gave it a new plan.

        Args:
            device_id: The device.

        Returns:
            None

        Raises:
            None
        """
        try:
            capacity = await asyncio.to_thread(load_capacity, self._uow, device_id)
        except StorageError:
            return  # the next re-plan reads it again
        self._plans.set_capacity(device_id, capacity)

    async def watch(self, stop: asyncio.Event, interval_s: float) -> None:
        """Re-plan whenever the change counter moves, until ``stop`` is set.

        Args:
            stop: Set at shutdown.
            interval_s: How often to read the counter.

        Returns:
            None

        Raises:
            None
        """
        failing = False
        while not stop.is_set():
            self._wake.clear()
            try:
                counter = await asyncio.to_thread(self._inventory.change_counter)
                if self._requested or counter != self.counter:
                    self._requested = False
                    await self.replan()
                failing = False
            except Exception as exc:  # noqa: BLE001 - keep polling on the plans already loaded
                if not failing:
                    _LOG.warning(
                        "cannot read inventory (%s); polling continues with the last plan",
                        type(exc).__name__,
                        extra={"event_id": "collector.inventory_unavailable"},
                    )
                failing = True
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(_first(stop, self._wake), interval_s)


async def _first(*events: asyncio.Event) -> None:
    """Wait until any of the events is set.

    Args:
        events: The events.

    Returns:
        None

    Raises:
        None
    """
    waiters = [asyncio.ensure_future(e.wait()) for e in events]
    try:
        await asyncio.wait(waiters, return_when=asyncio.FIRST_COMPLETED)
    finally:
        for waiter in waiters:
            waiter.cancel()
