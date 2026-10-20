"""SNMP-T09, DISC-T04, UP-T*, RATE-003: partial data, restarts, and discontinuities.

These scenarios break the agent in ways a real network does and check that
vakr keeps what arrived, flags what did not, and never turns a reset into a
traffic spike: a broken varbind costs only itself, a failed IP-MIB table
leaves the interface inventory intact, an agent restart is detected from
``sysUpTime``, and a changed ``ifCounterDiscontinuityTime`` invalidates
only that interface's baseline.

Dependencies:
    asyncio: Scenarios run with ``asyncio.run``.
    tests.simulators.snmp.simlab: The agent, communities, and factory.
    vakr.logic: Rate derivation, as the pipeline will call it.

Example:
    $ uv run pytest tests/simulators/snmp/test_snmp_agent_resilience.py
"""

import asyncio
from datetime import UTC, datetime, timedelta

from tests.simulators.snmp.iosxe_dataset import router
from tests.simulators.snmp.simlab import FAST, lab, samples, status
from tests.simulators.snmp.snmprec import Record
from vakr.collectors.snmp import (
    InterfacePlan,
    PollPlan,
    SnmpPollResult,
    UptimeReading,
    poll_device,
    uptime_continuity,
)
from vakr.collectors.snmp.poll import snmp_state
from vakr.discovery import DiscoveryResult, SubtableStatus, discover
from vakr.discovery.mib import IFX_COLUMNS, IP_ADDRESS_ENTRY
from vakr.domain.enums import BaselineState, SnmpState, UptimeContinuity
from vakr.domain.quality import Quality
from vakr.logic.baseline import RateContext
from vakr.logic.rates import RateResult, compute_rates
from vakr.ports.clock import ManualClock

PLAN = PollPlan(tuple(InterfacePlan(i) for i in (1, 2, 5, 12)))
NAME = "rtr-sim-001"


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


def rates(first: SnmpPollResult, second: SnmpPollResult) -> dict[int, RateResult]:
    """Derive rates between two polls exactly as the pipeline will.

    Args:
        first: The earlier poll.
        second: The later poll.

    Returns:
        dict[int, RateResult]: Rates by ifIndex.

    Raises:
        None
    """
    a, b = first.stage1, second.stage1
    assert a.sys_uptime_ticks is not None
    assert b.sys_uptime_ticks is not None
    continuity = uptime_continuity(
        UptimeReading(a.sys_uptime_ticks, a.sampled_at),
        UptimeReading(b.sys_uptime_ticks, b.sampled_at),
    )[0]
    assert first.stage2 is not None
    assert second.stage2 is not None
    before = {s.if_index: s for s in samples(first)}
    found = {}
    for s in samples(second):
        p = before[s.if_index]
        ctx = RateContext(s.sampled_monotonic - p.sampled_monotonic, 60.0, continuity)
        found[s.if_index] = compute_rates(p.counter_sample(), s.counter_sample(), ctx)
    return found


def test_snmp_t09_one_broken_varbind_costs_only_itself() -> None:
    """SNMP-T09, SNMP-014: genErr on one counter keeps every other value and flags the gap.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """

    async def scenario() -> SnmpPollResult:
        """Run the scenario against the simulator.

        Args:
            None

        Returns:
            SnmpPollResult: What the assertions check.

        Raises:
            None
        """
        c = clock()
        async with lab([router(1)], c) as sim:
            broken = (*IFX_COLUMNS["ifHCOutOctets"], 5)
            sim.devices[NAME].faults.error_subtrees[broken] = 5
            async with sim.factory.open(sim.target(NAME), FAST) as session:
                return await poll_device(session, PLAN, c)

    result = asyncio.run(scenario())
    by_index = {s.if_index: s for s in samples(result)}
    assert snmp_state(result) is SnmpState.PARTIAL
    assert by_index[5].out_octets is None
    assert by_index[5].in_octets is not None
    assert by_index[5].missing == ("out_octets",)
    assert Quality.PARTIAL_RESPONSE in by_index[5].quality
    assert all(s.missing == () for i, s in by_index.items() if i != 5)
    assert result.error is not None
    assert result.error.code == "agent_error.genErr"


def test_disc_t04_failed_ip_mib_keeps_interface_inventory() -> None:
    """DISC-T04, DISC-008: an IP-MIB timeout leaves interfaces intact and addresses partial.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """

    async def scenario() -> DiscoveryResult:
        """Run the scenario against the simulator.

        Args:
            None

        Returns:
            DiscoveryResult: What the assertions check.

        Raises:
            None
        """
        c = clock()
        async with lab([router(1)], c) as sim:
            sim.devices[NAME].faults.drop_subtrees = ((*IP_ADDRESS_ENTRY,),)
            async with sim.factory.open(sim.target(NAME), FAST) as session:
                return await discover(session, c)

    found = asyncio.run(scenario())
    assert len(found.interfaces) == 13
    assert status(found, "ifTable") is SubtableStatus.COMPLETE
    assert status(found, "ipAddressTable") is SubtableStatus.FAILED
    assert status(found, "ipAddrTable") is SubtableStatus.COMPLETE  # IPv4 fallback
    assert not found.addresses_complete
    assert Quality.PARTIAL_RESPONSE in found.quality
    assert found.interfaces[0].addresses == ("192.0.2.0/31",)  # IPv6 unknown, not "none"


def test_agent_restart_is_detected_and_gives_no_rate_spike() -> None:
    """UPTIME-002, RATE-003, RATE-004: a reload is RESET_DETECTED and rates are withheld.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """

    async def scenario() -> list[SnmpPollResult]:
        """Run the scenario against the simulator.

        Args:
            None

        Returns:
            list[SnmpPollResult]: What the assertions check.

        Raises:
            None
        """
        c = clock()
        polls = []
        async with lab([router(1)], c) as sim:
            for step in range(3):
                if step == 2:
                    sim.devices[NAME].restart()
                async with sim.factory.open(sim.target(NAME), FAST) as session:
                    polls.append(await poll_device(session, PLAN, c))
                c.advance(timedelta(seconds=60))
        return polls

    first, steady, rebooted = asyncio.run(scenario())
    normal = rates(first, steady)
    assert normal[1].baseline is BaselineState.VALID
    assert normal[1].in_octets.rate == 40_000_000.0  # the dataset's 40 Mb/s exactly
    after = rates(steady, rebooted)
    assert all(r.baseline is BaselineState.INVALIDATED for r in after.values())
    assert all(Quality.BASELINE_UPTIME_RESET in r.quality for r in after.values())
    assert all(r.in_octets.rate is None for r in after.values())
    a, b = steady.stage1, rebooted.stage1
    verdict = uptime_continuity(
        UptimeReading(a.sys_uptime_ticks or 0, a.sampled_at),
        UptimeReading(b.sys_uptime_ticks or 0, b.sampled_at),
    )
    assert verdict[0] is UptimeContinuity.RESET_DETECTED


def test_counter_discontinuity_invalidates_only_that_interface() -> None:
    """RATE-003, SNMP-009: a changed ifCounterDiscontinuityTime withholds that one rate.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """

    async def scenario() -> list[SnmpPollResult]:
        """Run the scenario against the simulator.

        Args:
            None

        Returns:
            list[SnmpPollResult]: What the assertions check.

        Raises:
            None
        """
        c = clock()
        polls = []
        async with lab([router(1)], c) as sim:
            for step in range(2):
                if step:
                    oid = (*IFX_COLUMNS["ifCounterDiscontinuityTime"], 2)
                    sim.devices[NAME].set(Record(oid, "67", "990000"))
                async with sim.factory.open(sim.target(NAME), FAST) as session:
                    polls.append(await poll_device(session, PLAN, c))
                c.advance(timedelta(seconds=60))
        return polls

    first, second = asyncio.run(scenario())
    found = rates(first, second)
    assert Quality.BASELINE_DISCONTINUITY in found[2].quality
    assert found[2].in_octets.rate is None
    assert found[1].baseline is BaselineState.VALID
    assert found[12].baseline is BaselineState.VALID  # the 32-bit-only interface


def test_looping_agent_and_silence_midway_are_bounded() -> None:
    """DISC-008, SNMP-018: a looping walk stops; an agent silent mid-Stage 2 is not chased.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """

    async def scenario() -> tuple[DiscoveryResult, SnmpPollResult, int]:
        """Run the scenario against the simulator.

        Args:
            None

        Returns:
            tuple[DiscoveryResult, SnmpPollResult, int]: What the assertions check.

        Raises:
            None
        """
        c = clock()
        async with lab([router(1)], c) as sim:
            device = sim.devices[NAME]
            device.faults.looping = True
            async with sim.factory.open(sim.target(NAME), FAST) as session:
                found = await discover(session, c)
                device.faults.looping = False
                device.faults.drop_subtrees = ((*IFX_COLUMNS["ifHCInOctets"],),)
                wide = PollPlan(tuple(InterfacePlan(i, discontinuity=False) for i in range(1, 14)))
                before = device.requests
                polled = await poll_device(session, wide, c)
                return found, polled, device.requests - before

    found, polled, requests = asyncio.run(scenario())
    assert status(found, "ifTable") is SubtableStatus.PARTIAL
    assert any(t.error and t.error.kind.value == "malformed" for t in found.subtables)
    got = samples(polled)
    assert len(got) == 10
    assert all(s.in_octets is None for s in got)
    # Stage 1, then two silent batches; each costs two attempts and one halved resend.
    assert requests <= 1 + 2 * (2 + 2)
