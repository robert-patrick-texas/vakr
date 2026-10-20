"""Working plans, the spool pump, and health (SNMP-010, DISC-009, STORE-004, OBS-001).

Dependencies:
    vakr.app.collector: Plans, pipeline, health.
    vakr.ingest: A real spool in ``tmp_path``.

Example:
    $ uv run pytest tests/unit/app/collector/test_col_parts.py
"""

import asyncio
from datetime import timedelta
from pathlib import Path

import pytest

from tests.unit.app.collector.col_fakes import CID, T0, iface, make_device, make_target
from vakr.app.collector.health import health_observation, probe_state, worst
from vakr.app.collector.pipeline import SpoolPump, flatten
from vakr.app.collector.plans import EMPTY_PLAN, PlanBook
from vakr.collectors.snmp import CapabilityChange, InterfacePlan, PollPlan
from vakr.domain.enums import CollectorState, CounterFamily
from vakr.domain.ids import WorkerId, new_id
from vakr.ingest import SpoolFullError, SpoolHealth, SpoolSettings
from vakr.ingest.spool import Spool
from vakr.logic.capacity import NO_CAPACITY, CapacityInputs
from vakr.ports.clock import SystemClock
from vakr.scheduler.admission import Miss, MissReason
from vakr.scheduler.health import SchedulerSnapshot
from vakr.scheduler.outbox import Outbox
from vakr.scheduler.plan import Slot
from vakr.scheduler.records import PollResult, missed_result
from vakr.scheduler.stats import Counters

DEVICE = make_device(1)


def test_plans_keep_learned_capabilities_until_reprobe() -> None:
    """SNMP-010, DISC-009: a learned limitation sticks until the re-probe interval passes.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    book = PlanBook()
    assert book.plan(DEVICE.id) is EMPTY_PLAN
    assert not book.has(DEVICE.id)
    base = PollPlan((InterfacePlan(3, iface(3)), InterfacePlan(4)))
    book.adopt(DEVICE.id, base, T0)
    book.learn(DEVICE.id, [CapabilityChange(3, "hc_octets", False)])
    assert book.plan(DEVICE.id).interfaces[0].family is CounterFamily.LEGACY32
    book.adopt(DEVICE.id, base, T0 + timedelta(hours=1))  # same plan from inventory
    assert book.plan(DEVICE.id).interfaces[0].family is CounterFamily.LEGACY32
    assert book.reprobe_expired(T0 + timedelta(days=6), timedelta(days=7)) == 0
    assert book.reprobe_expired(T0 + timedelta(days=7), timedelta(days=7)) == 1
    assert book.plan(DEVICE.id).interfaces[0].family is CounterFamily.HC64
    assert book.interface_ids() == {iface(3)}  # the unresolved one has no UUID
    capacity = CapacityInputs(if_speed_bps=10**9)
    book.set_capacity(DEVICE.id, {iface(3): capacity})
    assert book.capacity(DEVICE.id, iface(3)) == capacity
    assert book.capacity(DEVICE.id, iface(4)) is NO_CAPACITY
    book.retain([])
    assert len(book) == 0
    assert book.capacity(DEVICE.id, iface(3)) is NO_CAPACITY


def missed(i: int) -> PollResult:
    """Build a not-attempted record of device ``i``.

    Args:
        i: Device number.

    Returns:
        PollResult: A ``DRAINING`` miss.

    Raises:
        None
    """
    slot = Slot(make_target(make_device(i)), 0)
    return missed_result(Miss(slot, MissReason.DRAINING), CID, T0)


def test_pump_appends_in_bounded_chunks_and_drains(tmp_path: Path) -> None:
    """STORE-004, 6.4: records reach the fsynced spool in chunks within the queue bound.

    Args:
        tmp_path: Spool root.

    Returns:
        None

    Raises:
        None
    """

    async def scenario() -> None:
        """Pump 150 records through a 100-observation spool queue.

        Args:
            None

        Returns:
            None

        Raises:
            None
        """
        settings = SpoolSettings.defaults(tmp_path).with_values(queue_capacity=100)
        spool, outbox = Spool(settings, CID), Outbox(1000, SystemClock())
        await spool.open()
        pump = SpoolPump(outbox, spool)
        task = asyncio.create_task(pump.run())
        for i in range(120):
            await outbox.put(missed(i))
        while outbox.depth:
            await asyncio.sleep(0.05)
        pump.stop()
        await task
        for i in range(30):
            await outbox.put(missed(i))
        assert await pump.drain() == 30
        assert pump.appended == 150
        assert spool.appended == 150
        await spool.close()

    asyncio.run(scenario())


def test_pump_survives_a_refused_append(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """STORE-007: a refused append is counted (the spool records the loss) and the pump goes on.

    Args:
        tmp_path: Spool root.
        monkeypatch: Makes the spool refuse.

    Returns:
        None

    Raises:
        None
    """

    async def refuse(*_args: object, **_kwargs: object) -> None:
        """Refuse like a full disk.

        Args:
            _args: Ignored.
            _kwargs: Ignored.

        Returns:
            None

        Raises:
            SpoolFullError: Always.
        """
        raise SpoolFullError("spool full")

    async def scenario() -> None:
        """Drain two records into a refusing spool.

        Args:
            None

        Returns:
            None

        Raises:
            None
        """
        spool, outbox = Spool(SpoolSettings.defaults(tmp_path), CID), Outbox(10, SystemClock())
        monkeypatch.setattr(spool, "append", refuse)
        pump = SpoolPump(outbox, spool)
        await outbox.put(missed(1))
        await outbox.put(missed(2))
        await pump.drain()
        assert (pump.appended, pump.refused) == (0, 2)

    asyncio.run(scenario())


def test_flatten_keeps_the_poll_and_drops_non_observations() -> None:
    """A step bug never blocks the spool: the poll record still goes in.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    record = missed(1)
    junk = PollResult(record.poll, 0, observations=("not an observation", record.poll))
    assert flatten(junk) == [record.poll, record.poll]


def test_health_takes_the_worst_opinion() -> None:
    """E.25 #7, E.24, OBS-003: scheduler, spool, and step faults each degrade the collector.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    assert worst([]) is CollectorState.HEALTHY
    busy = SchedulerSnapshot(1, 4, 1, 0, queue_depth=3, queue_capacity=1000,
                             period=Counters(completed=5, capacity=1))  # fmt: skip
    assert busy.collector_state() is CollectorState.OVERLOADED
    assert probe_state(busy) is CollectorState.OVERLOADED
    lagging = SchedulerSnapshot(1, 4, 0, 0, loop_lag_us=2_000_000)
    assert probe_state(lagging) is CollectorState.DEGRADED
    calm = SchedulerSnapshot(1, 4, 0, 0, queue_depth=0, queue_capacity=1000)
    spool = SpoolHealth.empty(str(CID), 0, 4096)
    obs = health_observation(CID, new_id(WorkerId), T0, "0.0.0", calm, spool, 2, 7)
    assert obs.state is CollectorState.DEGRADED  # two collector-side step faults
    assert (obs.active_devices, obs.active_interfaces, obs.queue_capacity) == (1, 7, 1000)
    clean = health_observation(CID, new_id(WorkerId), T0, "0.0.0", calm, spool, 0, 7)
    assert clean.state is CollectorState.HEALTHY
    assert clean.spool_used_pct == 0.0
