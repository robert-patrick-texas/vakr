"""Discovery inside the collector: due times, recording, retries, bounds (DISC-T01, DISC-T02).

Dependencies:
    tests.simulators.snmp.simlab: The in-process agent.
    vakr.app.collector: The runner and its due-time rules.

Example:
    $ uv run pytest tests/unit/app/collector/test_col_discovery.py
"""

import asyncio
import contextlib
from collections.abc import AsyncGenerator
from datetime import datetime, timedelta

import pytest

from tests.integration.collector.collector_lab import LabInventory
from tests.simulators.snmp.iosxe_dataset import router
from tests.simulators.snmp.simlab import FAST, lab
from tests.unit.app.collector.col_fakes import make_device, make_target
from vakr.app.collector.discovery import DiscoveryRunner
from vakr.app.collector.discovery_due import (
    clamp_discovery_interval,
    discoverable,
    discovery_interval_s,
    first_delay_s,
    next_slot_s,
    rediscovery_at_s,
    retry_delay_s,
)
from vakr.app.collector.plans import PlanBook
from vakr.app.collector.timings import ServiceTimings
from vakr.collectors.snmp import PollPlan
from vakr.collectors.snmp.config import SnmpConfig
from vakr.discovery import DiscoveryResult
from vakr.domain.enums import ReachabilityStrategy
from vakr.domain.errors import NotFoundError
from vakr.domain.ids import DeviceId
from vakr.domain.inventory import Device
from vakr.ports.clock import SystemClock
from vakr.ports.snmp import SnmpSession, SnmpSessionFactory, SnmpSettings, SnmpTarget

DAY = 86_400


def test_disc_t02_rediscovery_sits_on_a_stable_spread_grid() -> None:
    """DISC-T02, DISC-002: 24 h default, per-device override, stable across restarts.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    device = make_device(1)
    assert discovery_interval_s(device, DAY) == DAY
    assert discovery_interval_s(make_device(2, discovery_interval_s=3600), DAY) == 3600
    assert clamp_discovery_interval(1) == 30
    assert clamp_discovery_interval(10**9) == 30 * DAY
    now = 1_800_000_000.0
    slot = rediscovery_at_s(now, DAY, "vakr-east", device.id)
    assert now < slot <= now + DAY
    assert rediscovery_at_s(now + 600, DAY, "vakr-east", device.id) == slot  # a restart
    assert rediscovery_at_s(slot, DAY, "vakr-east", device.id) == slot + DAY
    others = {rediscovery_at_s(now, DAY, "vakr-east", make_device(i).id) for i in range(3, 13)}
    assert len(others) == 10  # spread, not one burst
    assert next_slot_s(1000.0, 60, 15) == 1035


def test_failed_discoveries_back_off_exponentially_and_boundedly() -> None:
    """E.27: 60 s after the first failure, doubling, never more than 1 h.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    delays = [retry_delay_s(n, 60.0, 3600.0) for n in range(1, 9)]
    assert delays == [60.0, 120.0, 240.0, 480.0, 960.0, 1920.0, 3600.0, 3600.0]
    assert retry_delay_s(10_000, 60.0, 3600.0) == 3600.0  # a long outage cannot overflow
    with pytest.raises(ValueError, match="retry"):
        ServiceTimings(discovery_retry_min_s=120, discovery_retry_max_s=60)


def test_first_discovery_is_spread_and_only_for_snmp_devices() -> None:
    """DISC-001, ICMP-014: first discoveries spread over the window; icmp_only is skipped.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    delays = {first_delay_s("vakr-east", make_device(i).id, 60.0) for i in range(20)}
    assert all(0 <= d < 60 for d in delays)
    assert len(delays) > 10
    assert first_delay_s("vakr-east", make_device(1).id, 0) == 0.0
    assert discoverable(make_target(make_device(1)))
    assert not discoverable(make_target(make_device(1), ReachabilityStrategy.ICMP_ONLY))
    assert not discoverable(make_target(make_device(1, credential_ref=None)))


class CountingFactory:
    """Wraps a session factory and records how many sessions were open at once."""

    def __init__(self, inner: SnmpSessionFactory) -> None:
        """Wrap the factory.

        Args:
            inner: The real factory.

        Returns:
            None

        Raises:
            None
        """
        self._inner, self.open_now, self.peak = inner, 0, 0

    @contextlib.asynccontextmanager
    async def open(self, target: SnmpTarget, settings: SnmpSettings) -> AsyncGenerator[SnmpSession]:
        """Open a session through the real factory, counting it.

        Args:
            target: The agent.
            settings: The budget.

        Returns:
            AsyncGenerator[SnmpSession]: Yields the session.

        Raises:
            SnmpSessionError: As the real factory.
        """
        async with self._inner.open(target, settings) as session:
            self.open_now += 1
            self.peak = max(self.peak, self.open_now)
            try:
                yield session
            finally:
                self.open_now -= 1


class EmptyAgentInventory(LabInventory):
    """Records a device whose agent reports no interfaces, and keeps no plan for it.

    Attributes:
        removed: Devices that disappear from inventory during discovery.
    """

    def __init__(self, devices: list[Device], removed: set[DeviceId]) -> None:
        """Hold the devices.

        Args:
            devices: The inventory.
            removed: Devices whose recording fails with ``NotFoundError``.

        Returns:
            None

        Raises:
            None
        """
        super().__init__(devices)
        self.removed = removed
        self.empty: set[DeviceId] = set()

    def record_discovery(
        self, device_id: DeviceId, result: DiscoveryResult, now: datetime
    ) -> PollPlan:
        """Record like the lab inventory, except for the removed and empty devices.

        Args:
            device_id: The device.
            result: The discovery result.
            now: When it finished.

        Returns:
            PollPlan: The plan; empty for a device in ``empty``.

        Raises:
            NotFoundError: For a device in ``removed``.
        """
        if device_id in self.removed:
            raise NotFoundError("device", device_id)
        if device_id in self.empty:
            self.recorded.append(result)
            return PollPlan(())  # and poll_plan() keeps answering None
        return super().record_discovery(device_id, result, now)


def test_discovery_is_bounded_and_never_loops_on_a_device() -> None:
    """DISC-T01, DISC-002, E.27: success waits an interval (plan or not), failure backs off.

    Devices: ``a`` answers; ``b`` is silent; ``c`` answers with no plan kept
    by inventory; ``d`` is removed from inventory while it is discovered.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """

    async def scenario() -> None:
        """Discover four simulated routers.

        Args:
            None

        Returns:
            None

        Raises:
            None
        """
        specs = [router(n) for n in range(1, 5)]
        async with lab(specs) as sim:
            a, b, c, d = (make_device(n, poll_address="127.0.0.1", credential_ref=s.name)
                          for n, s in enumerate(specs, start=1))  # fmt: skip
            sim.devices[specs[1].name].faults.drop_all = True
            inventory = EmptyAgentInventory([a, b, c, d], removed={d.id})
            inventory.empty.add(c.id)
            plans, factory = PlanBook(), CountingFactory(sim.factory)
            timings = ServiceTimings(
                discovery_concurrency=1, discovery_spread_s=0, discovery_retry_min_s=600
            )
            runner = DiscoveryRunner(inventory, factory, plans, SystemClock(), timings, "lab")
            runner.config = SnmpConfig(sim.port, FAST, timedelta(days=1), timedelta(days=7))
            changed: list[object] = []

            async def on_plan(device_id: object) -> None:
                """Record a plan change.

                Args:
                    device_id: The device.

                Returns:
                    None

                Raises:
                    None
                """
                changed.append(device_id)

            runner.on_plan = on_plan
            targets = [make_target(x) for x in (a, b, c, d)]
            runner.replan(targets)
            task = asyncio.create_task(runner.run())
            for _ in range(100):
                if len(inventory.recorded) + runner.failed >= 3 and runner.due_in(d.id) is None:
                    break
                await asyncio.sleep(0.1)
            await asyncio.sleep(0.3)  # a looping runner would discover again here
            runner.replan(targets[:3])  # the re-plan after d's removal; c still has no plan
            await runner.stop()
            await task
            assert (runner.completed, runner.failed) == (2, 1)  # d is neither
            assert len(inventory.recorded) == 2  # a and c, once each
            assert changed == [a.id, c.id]
            assert factory.peak == 1  # discovery_concurrency
            for device, low, high in ((a, DAY - 60, DAY), (c, DAY - 60, DAY), (b, 500, 600)):
                left = runner.due_in(device.id)
                assert left is not None
                assert low < left <= high, device.name
            assert runner.due_in(d.id) is None

    asyncio.run(scenario())


def test_timings_refuse_spinning_cadences() -> None:
    """E.27 bounds: a zero cadence or concurrency would spin or never run.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    with pytest.raises(ValueError, match="positive"):
        ServiceTimings(change_poll_s=0)
    with pytest.raises(ValueError, match="concurrency"):
        ServiceTimings(discovery_concurrency=0)
    with pytest.raises(ValueError, match="override"):
        ServiceTimings(poll_interval_override_s=0)
