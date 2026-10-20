"""Re-planning, telemetry upkeep, and capacity reads (6.2, E.21, RATE-008, RATE-009).

Dependencies:
    tests.contract.contract_samples: Device and interface records.
    vakr.app.collector: Replanner, upkeep, capacity.

Example:
    $ uv run pytest tests/unit/app/collector/test_col_upkeep.py
"""

import asyncio
from datetime import datetime
from pathlib import Path
from typing import Any

from tests.contract import contract_samples as samples
from tests.integration.collector.collector_lab import LabInventory
from tests.unit.app.collector.col_fakes import T0, make_device
from tests.unit.scheduler.sched_fakes import IDS, ScriptedSteps, settings
from tests.unit.scheduler.sched_simloop import new_clock
from vakr.adapters.sqlite.metadata import (
    METADATA_FILE,
    SqliteMetadataStore,
    metadata_migrations,
    sqlite_uow_factory,
)
from vakr.app.collector.baselines import BaselineStore
from vakr.app.collector.capacity import load_capacity
from vakr.app.collector.discovery import DiscoveryRunner
from vakr.app.collector.maintenance import NoRollups, TelemetryUpkeep
from vakr.app.collector.plans import PlanBook
from vakr.app.collector.replan import Replanner, build_targets, read_inventory
from vakr.app.collector.timings import ServiceTimings
from vakr.collectors.snmp import InterfacePlan, PollPlan
from vakr.domain.enums import AdminState
from vakr.domain.errors import NotFoundError, StorageError
from vakr.domain.ids import DeviceId
from vakr.domain.interfaces import InterfaceAnnotation
from vakr.domain.inventory import Device
from vakr.ports.clock import SystemClock
from vakr.ports.metadata import MetadataUnitOfWorkFactory
from vakr.scheduler.driver import Scheduler
from vakr.scheduler.steps import PollSteps
from vakr.scheduler.targets import PollDefaults


def metadata(tmp_path: Path) -> tuple[SqliteMetadataStore, MetadataUnitOfWorkFactory]:
    """Open a fresh, migrated metadata database.

    Args:
        tmp_path: Test directory.

    Returns:
        tuple: The store and its unit-of-work factory.

    Raises:
        None
    """
    path = tmp_path / METADATA_FILE
    metadata_migrations(path).migrate()
    store = SqliteMetadataStore(path)
    return store, sqlite_uow_factory(store)


def test_capacity_comes_from_interface_records(tmp_path: Path) -> None:
    """RATE-008: override, contracted rate, and speeds are read per interface.

    Args:
        tmp_path: Test directory.

    Returns:
        None

    Raises:
        None
    """
    store, uow = metadata(tmp_path)
    device = samples.device()
    note = InterfaceAnnotation(contracted_bps=50_000_000, capacity_override_bps=100_000_000)
    record = samples.interface(device.id, if_speed=10**9, if_high_speed=1000, annotation=note)
    with uow() as tx:
        tx.devices.add(device)
        tx.interfaces.add(record)
        tx.commit()
    found = load_capacity(uow, device.id)
    store.close()
    inputs = found[record.id]
    assert inputs.override_bps == 100_000_000
    assert inputs.contracted_bps == 50_000_000
    assert (inputs.if_speed_bps, inputs.if_high_speed_mbps) == (10**9, 1000)


def test_build_targets_skips_disabled_and_applies_the_test_override() -> None:
    """9.2, E.18: disabled devices are not polled; the override exists only in code.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    devices = [make_device(1), make_device(2, admin_state=AdminState.DISABLED)]
    assert [t.effective_interval_s for t in build_targets(devices, PollDefaults(), None)] == [60]
    fast = build_targets(devices, PollDefaults(), 2)
    assert [(t.configured_interval_s, t.effective_interval_s) for t in fast] == [(2, 2)]


class FlakyInventory(LabInventory):
    """A lab inventory whose database can be made unavailable."""

    def __init__(self, devices: list[Device]) -> None:
        """Hold the devices.

        Args:
            devices: The inventory.

        Returns:
            None

        Raises:
            None
        """
        super().__init__(devices)
        self.down = False

    def change_counter(self) -> int:
        """Return the counter, or fail like an unavailable database.

        Args:
            None

        Returns:
            int: The counter.

        Raises:
            StorageError: While ``down``.
        """
        if self.down:
            raise StorageError("metadata database is locked", retryable=True)
        return super().change_counter()


def test_watcher_replans_on_change_and_rides_out_inventory_trouble(tmp_path: Path) -> None:
    """6.2: the change counter drives re-plans; an unavailable database keeps the last plan.

    Args:
        tmp_path: Test directory.

    Returns:
        None

    Raises:
        None
    """
    store, uow = metadata(tmp_path)
    a, b = make_device(1), make_device(2)
    inventory = FlakyInventory([a])
    inventory.plans[a.id] = PollPlan((InterfacePlan(1),))

    async def scenario() -> Replanner:
        """Watch while inventory changes and fails.

        Args:
            None

        Returns:
            Replanner: The watcher after the run.

        Raises:
            None
        """
        clock = SystemClock()
        steps = ScriptedSteps(new_clock())
        scheduler = Scheduler(clock, settings(), IDS, PollSteps(steps, steps))
        plans, timings = PlanBook(), ServiceTimings()
        discovery = DiscoveryRunner(inventory, None, plans, clock, timings, "lab")  # ty: ignore[invalid-argument-type]
        replanner = Replanner(inventory, scheduler, discovery, plans, BaselineStore(), uow,
                              clock, None)  # fmt: skip
        stop = asyncio.Event()
        task = asyncio.create_task(replanner.watch(stop, 0.05))
        await asyncio.sleep(0.2)
        assert scheduler.snapshot().devices == 1
        assert plans.has(a.id)
        inventory.items, inventory.counter = (a, b), 1
        await asyncio.sleep(0.2)
        assert scheduler.snapshot().devices == 2
        assert not plans.has(b.id)
        inventory.down = True
        await asyncio.sleep(0.2)
        assert scheduler.snapshot().devices == 2  # polling continues on the last plan
        inventory.down = False
        replanner.request()
        await asyncio.sleep(0.2)
        await replanner.refresh_capacity(a.id)
        stop.set()
        await task
        return replanner

    replanner = asyncio.run(scenario())
    store.close()
    assert replanner.replans >= 3


class FakeTelemetry:
    """Records upkeep calls; can fail like an unavailable database."""

    def __init__(self) -> None:
        """Start with no calls.

        Args:
            None

        Returns:
            None

        Raises:
            None
        """
        self.moves: list[datetime] = []
        self.fail = False
        self.retention = self

    def maintain(self, now: datetime) -> Any:
        """Record a staging move.

        Args:
            now: Reference time.

        Returns:
            Any: Nothing useful.

        Raises:
            StorageError: When ``fail`` is set.
        """
        if self.fail:
            raise StorageError("database is locked", retryable=True)
        self.moves.append(now)

    def apply(self, now: datetime) -> str:
        """Record a retention pass.

        Args:
            now: Reference time.

        Returns:
            str: A report stand-in.

        Raises:
            None
        """
        return f"retention at {now:%H:%M}"


def test_upkeep_moves_staging_and_runs_retention_and_survives_failures() -> None:
    """E.21: first passes at start, then on their cadences; a failure is logged, not fatal.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    telemetry = FakeTelemetry()
    timings = ServiceTimings(maintenance_interval_s=0.1, retention_interval_s=0.15)

    async def scenario() -> TelemetryUpkeep:
        """Run upkeep briefly, failing part of the time.

        Args:
            None

        Returns:
            TelemetryUpkeep: The upkeep after the run.

        Raises:
            None
        """
        upkeep = TelemetryUpkeep(telemetry, SystemClock(), timings, NoRollups())  # ty: ignore[invalid-argument-type]
        task = asyncio.create_task(upkeep.run())
        await asyncio.sleep(0.25)
        telemetry.fail = True
        await asyncio.sleep(0.25)
        upkeep.stop()
        await task
        return upkeep

    upkeep = asyncio.run(scenario())
    assert upkeep.passes["move"] >= 2
    assert upkeep.passes["retention"] >= 2
    assert upkeep.failures["move"] >= 1
    assert NoRollups().run(T0) == 0


class VanishingInventory(LabInventory):
    """A lab inventory in which one device disappears between the two reads."""

    def poll_plan(self, device_id: DeviceId) -> PollPlan | None:
        """Answer like inventory after the device's removal.

        Args:
            device_id: The device.

        Returns:
            PollPlan | None: The plan of the remaining devices.

        Raises:
            NotFoundError: For the second device.
        """
        if device_id == self.items[1].id:
            raise NotFoundError("device", device_id)
        return super().poll_plan(device_id)


def test_a_device_removed_between_reads_is_left_out() -> None:
    """``poll_plan`` raising NotFoundError skips that device; it is not a collector fault.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    a, b = make_device(1), make_device(2)
    inventory = VanishingInventory([a, b])
    counter, devices, plans = read_inventory(inventory)
    assert counter == 0
    assert devices == (a,)
    assert plans == {a.id: None}
