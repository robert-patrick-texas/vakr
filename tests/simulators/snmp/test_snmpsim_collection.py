"""SNMP-T01, SNMP-T05, DISC-006: discovery and polling against the real snmpsim agent.

snmpsim is an independent SNMP agent implementation, so these tests check
vakr's client against something it did not write. The synthetic IOS XE
dataset is served from ``.snmprec`` files, with counters and ``sysUpTime``
advancing in real time through snmpsim's numeric variation.

Dependencies:
    asyncio: Scenarios run with ``asyncio.run``.
    tests.simulators.snmp: The session's snmpsim fleet and load helper.

Example:
    $ uv run pytest tests/simulators/snmp/test_snmpsim_collection.py
"""

import asyncio

from tests.simulators.snmp.loadkit import run_cycle
from tests.simulators.snmp.simkit import StaticResolver
from tests.simulators.snmp.simlab import samples, status
from tests.simulators.snmp.snmpsim_server import SnmpsimFleet
from vakr.adapters.snmp import PysnmpSessionFactory
from vakr.collectors.snmp import SnmpPollResult, plan_from_discovery, poll_device
from vakr.collectors.snmp.poll import snmp_state
from vakr.discovery import DiscoveryResult, SubtableStatus, discover
from vakr.domain.enums import CounterFamily, IfOperStatus, SnmpState
from vakr.ports.clock import SystemClock
from vakr.ports.snmp import SnmpSettings, SnmpTarget


def collect(fleet: SnmpsimFleet, name: str) -> tuple[DiscoveryResult, SnmpPollResult]:
    """Discover and poll one device of the fleet.

    Args:
        fleet: The running snmpsim fleet.
        name: The device.

    Returns:
        tuple[DiscoveryResult, SnmpPollResult]: Discovery and one poll.

    Raises:
        None
    """

    async def scenario() -> tuple[DiscoveryResult, SnmpPollResult]:
        """Run the scenario against the simulator.

        Args:
            None

        Returns:
            tuple[DiscoveryResult, SnmpPollResult]: What the assertions check.

        Raises:
            None
        """
        factory = PysnmpSessionFactory(StaticResolver(fleet.communities))
        target = SnmpTarget("127.0.0.1", name, port=fleet.port)
        async with factory.open(target, SnmpSettings()) as session:
            found = await discover(session, SystemClock())
            polled = await poll_device(session, plan_from_discovery(found), SystemClock())
        return found, polled

    return asyncio.run(scenario())


def test_snmp_t01_router_discovery_is_complete(snmpsim_fleet: SnmpsimFleet) -> None:
    """SNMP-T01, DISC-004..DISC-006: system, interface, and IP data with completeness.

    Args:
        snmpsim_fleet: The running fleet.

    Returns:
        None

    Raises:
        None
    """
    found, _ = collect(snmpsim_fleet, "rtr-sim-001")
    assert found.complete
    assert found.system.sys_name == "rtr-sim-001.example.net"
    assert found.system.sys_object_id == "1.3.6.1.4.1.9.1.2734"
    assert (found.system.sys_descr or "").startswith("Cisco IOS XE Software")
    assert found.system.sys_uptime_ticks is not None
    assert len(found.interfaces) == 13
    assert sum(i.up_up for i in found.interfaces) == 10
    by_index = {i.if_index: i for i in found.interfaces}
    assert by_index[1].addresses == ("192.0.2.0/31", "2001:db8:0:1::1/64", "2001:db8:1:1::1/64")
    assert by_index[2].addresses == ("198.51.100.101/24", "203.0.113.1/24")
    assert by_index[1].mac == "02:5e:01:01:00:01"
    assert by_index[1].if_alias == "ISP sim-carrier ckt SIM0001"
    assert by_index[7].oper_status is IfOperStatus.NOT_PRESENT
    assert by_index[12].hc_octets is False
    assert by_index[2].discontinuity is True
    assert status(found, "ipAddrTable") is SubtableStatus.SKIPPED


def test_snmp_t01_switch_and_legacy_ip_mib(snmpsim_fleet: SnmpsimFleet) -> None:
    """SNMP-T01, DISC-006: a 57-port switch, and ipAddrTable fallback on an older agent.

    Args:
        snmpsim_fleet: The running fleet.

    Returns:
        None

    Raises:
        None
    """
    switch, _ = collect(snmpsim_fleet, "sw-sim-001")
    assert switch.complete
    assert len(switch.interfaces) == 57
    assert sum(i.up_up for i in switch.interfaces) == 40
    svis = [i for i in switch.interfaces if i.if_type == 53]
    assert len({i.mac for i in svis}) == 1  # Catalyst SVIs share the chassis MAC
    legacy, _ = collect(snmpsim_fleet, "rtr-sim-009")
    assert status(legacy, "ipAddressTable") is SubtableStatus.ABSENT
    assert status(legacy, "ipAddrTable") is SubtableStatus.COMPLETE
    assert legacy.interfaces[0].addresses == ("192.0.2.16/31",)
    assert legacy.addresses_complete


def test_snmp_t05_hc_preferred_with_recorded_32_bit_fallback(snmpsim_fleet: SnmpsimFleet) -> None:
    """SNMP-T05, SNMP-007, SNMP-009: HC where it exists, 32-bit where not, recorded per sample.

    Args:
        snmpsim_fleet: The running fleet.

    Returns:
        None

    Raises:
        None
    """
    _, polled = collect(snmpsim_fleet, "rtr-sim-002")
    assert snmp_state(polled) is SnmpState.RESPONDING
    by_index = {s.if_index: s for s in samples(polled)}
    assert sorted(by_index) == [1, 2, 5, 6, 8, 9, 10, 11, 12, 13]
    assert by_index[12].family is CounterFamily.LEGACY32
    assert all(s.family is CounterFamily.HC64 for i, s in by_index.items() if i != 12)
    assert all(s.missing == () for s in by_index.values())
    assert by_index[2].discontinuity_ticks == 2_002
    assert by_index[1].in_errors is not None


def test_load_smoke_eleven_devices_well_within_one_interval(snmpsim_fleet: SnmpsimFleet) -> None:
    """Blueprint 4.2 at small scale: every device answers and poll p95 is far below 15 s.

    The full 100-device check is ``test_snmp_load.py`` (opt-in, VAKR_SNMP_LOAD=1).

    Args:
        snmpsim_fleet: The running fleet.

    Returns:
        None

    Raises:
        None
    """
    report = asyncio.run(run_cycle(snmpsim_fleet.port, snmpsim_fleet.communities))
    assert report.answered == report.devices == 11
    assert report.interfaces == 8 * 10 + 2 * 40 + 10
    assert report.discovery_s + report.poll_s < 60
    assert report.poll_p95_s < 5
