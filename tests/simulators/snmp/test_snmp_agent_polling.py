"""SNMP-T03..T05, SNMP-T07, SNMP-T08, SNMP-T10, SNMP-T12: polling against the fault agent.

Each scenario runs vakr's real PySNMP adapter, discovery, and Stage 1/2
collectors against the in-process agent, which records every request and
misbehaves on cue. The agent's clock is a ``ManualClock``, so counter
values are exact.

Dependencies:
    asyncio: Scenarios run with ``asyncio.run``.
    tests.simulators.snmp.simlab: The agent, communities, and factory.

Example:
    $ uv run pytest tests/simulators/snmp/test_snmp_agent_polling.py
"""

import asyncio
import time
from datetime import UTC, datetime

from tests.simulators.snmp.iosxe_dataset import router
from tests.simulators.snmp.simlab import FAST, changes, lab, samples
from vakr.collectors.snmp import InterfacePlan, PollPlan, plan_from_discovery, poll_device
from vakr.collectors.snmp.poll import snmp_state
from vakr.discovery import discover
from vakr.discovery.mib import IFX_COLUMNS, SYS_UPTIME
from vakr.domain.enums import CounterFamily, SnmpState
from vakr.domain.quality import Quality
from vakr.ports.clock import ManualClock

HC_IN = IFX_COLUMNS["ifHCInOctets"]
UP_UP = {1, 2, 5, 6, 8, 9, 10, 11, 12, 13}  # router layout in tests/simulators/snmp/ports.py


def clock() -> ManualClock:
    """Return a manual clock at a fixed instant.

    Args:
        None

    Returns:
        ManualClock: The clock.

    Raises:
        None
    """
    return ManualClock(datetime(2026, 10, 20, tzinfo=UTC), 5_000.0)


def test_snmp_t03_t04_exact_status_oids_then_counters_for_up_up_only() -> None:
    """SNMP-T03, SNMP-T04: Stage 1 GETs exact OIDs only; Stage 2 asks only up/up interfaces.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """

    async def scenario() -> None:
        """Run the scenario against the simulator.

        Args:
            None

        Returns:
            None

        Raises:
            None
        """
        c = clock()
        async with lab([router(1)], c) as sim:
            async with sim.factory.open(sim.target("rtr-sim-001"), FAST) as session:
                plan = plan_from_discovery(await discover(session, c))
                sim.devices["rtr-sim-001"].seen.clear()
                result = await poll_device(session, plan, c)
            seen = sim.devices["rtr-sim-001"].seen
        assert {kind for kind, _ in seen} == {"get"}  # no walk at minute level (SNMP-004)
        stage1_oids = seen[0][1]
        assert stage1_oids[0] == SYS_UPTIME
        assert len(stage1_oids) == 1 + 2 * 13
        counter_indexes = {oid[-1] for _, oids in seen[1:] for oid in oids}
        assert counter_indexes == UP_UP
        assert snmp_state(result) is SnmpState.RESPONDING
        assert len(samples(result)) == 10

    asyncio.run(scenario())


def test_snmp_t05_t07_hc_fallback_is_learned_and_not_requested_again() -> None:
    """SNMP-T05, SNMP-T07: a missing HC counter falls back to 32-bit and is remembered.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """

    async def scenario() -> None:
        """Run the scenario against the simulator.

        Args:
            None

        Returns:
            None

        Raises:
            None
        """
        c = clock()
        plan = PollPlan(tuple(InterfacePlan(i) for i in sorted(UP_UP)))  # all planned as HC
        async with lab([router(1)], c) as sim:
            device = sim.devices["rtr-sim-001"]
            async with sim.factory.open(sim.target("rtr-sim-001"), FAST) as session:
                first = await poll_device(session, plan, c)
                plan = plan.with_changes(changes(first))
                device.seen.clear()
                await poll_device(session, plan, c)
                asked = [oid for _, oids in device.seen for oid in oids]
                device.seen.clear()
                await poll_device(session, plan.reprobe(), c)
                reprobed = [oid for _, oids in device.seen for oid in oids]
        tunnel = next(s for s in samples(first) if s.if_index == 12)
        assert tunnel.family is CounterFamily.LEGACY32
        assert tunnel.in_octets is not None
        assert {(ch.if_index, ch.capability) for ch in changes(first)} >= {(12, "hc_octets")}
        assert (*HC_IN, 12) not in asked
        assert (*HC_IN, 12) in reprobed
        hc = next(s for s in samples(first) if s.if_index == 1)
        assert hc.family is CounterFamily.HC64

    asyncio.run(scenario())


def test_snmp_t08_retry_then_timeout_budget() -> None:
    """SNMP-T08: one dropped request is retried; a silent agent is a TIMEOUT within budget.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """

    async def scenario() -> None:
        """Run the scenario against the simulator.

        Args:
            None

        Returns:
            None

        Raises:
            None
        """
        c = clock()
        plan = PollPlan((InterfacePlan(1),))
        async with lab([router(1)], c) as sim:
            device = sim.devices["rtr-sim-001"]
            device.faults.drop_next = 1
            async with sim.factory.open(sim.target("rtr-sim-001"), FAST) as session:
                retried = await poll_device(session, plan, c)
                device.faults.drop_all = True
                started = time.monotonic()
                silent = await poll_device(session, plan, c)
                waited = time.monotonic() - started
        assert snmp_state(retried) is SnmpState.RESPONDING
        assert Quality.RETRIED in retried.quality
        assert retried.stats.retries == 1
        assert snmp_state(silent) is SnmpState.TIMEOUT
        assert (silent.stats.requests, silent.stats.timeouts, silent.stats.responses) == (2, 2, 0)
        assert silent.stage2 is None
        assert 0.35 < waited < 1.5  # two 0.2 s attempts, PySNMP's 0.1 s timer

    asyncio.run(scenario())


def test_snmp_t10_too_big_reduces_requests_with_one_pdu_in_flight() -> None:
    """SNMP-T10, SNMP-012: tooBig halves requests; discovery and a poll never overlap PDUs.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """

    async def scenario() -> None:
        """Run the scenario against the simulator.

        Args:
            None

        Returns:
            None

        Raises:
            None
        """
        c = clock()
        async with lab([router(1)], c) as sim:
            device = sim.devices["rtr-sim-001"]
            device.faults.too_big_over = 8
            device.faults.bulk_too_big = True
            device.faults.delay_s = 0.002
            target = sim.target("rtr-sim-001")
            async with sim.factory.open(target, FAST) as a, sim.factory.open(target, FAST) as b:
                plan = PollPlan(tuple(InterfacePlan(i) for i in sorted(UP_UP)))
                found, polled = await asyncio.gather(discover(a, c), poll_device(b, plan, c))
        assert found.complete
        assert len(found.interfaces) == 13
        assert snmp_state(polled) is SnmpState.RESPONDING
        assert Quality.REQUEST_SIZE_REDUCED in polled.quality
        assert len(samples(polled)) == 10
        assert device.max_in_flight == 1

    asyncio.run(scenario())


def test_snmp_t12_slow_agent_does_not_block_the_event_loop() -> None:
    """SNMP-T12, SNMP-015: a slow agent leaves independent timers on time.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """

    async def scenario() -> float:
        """Run the scenario against the simulator.

        Args:
            None

        Returns:
            float: What the assertions check.

        Raises:
            None
        """
        c = clock()
        async with lab([router(1)], c) as sim:
            sim.devices["rtr-sim-001"].faults.delay_s = 0.15
            lag = 0.0
            done = asyncio.Event()

            async def ticker() -> None:
                """Measure how late a 10 ms timer fires while the poll runs.

                Args:
                    None

                Returns:
                    None

                Raises:
                    None
                """
                nonlocal lag
                while not done.is_set():
                    before = time.monotonic()
                    await asyncio.sleep(0.01)
                    lag = max(lag, time.monotonic() - before - 0.01)

            task = asyncio.create_task(ticker())
            async with sim.factory.open(sim.target("rtr-sim-001"), FAST) as session:
                await poll_device(session, PollPlan(tuple(InterfacePlan(i) for i in UP_UP)), c)
            done.set()
            await task
            return lag

    assert asyncio.run(scenario()) < 0.1
