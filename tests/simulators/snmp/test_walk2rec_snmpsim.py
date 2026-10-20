"""M3 simulator path: a field-kit walk becomes an snmpsim device vakr can discover.

The sample capture in ``tools/snmpsim/testdata/rtr-sample`` is converted by
``walk2rec``, served by the real snmpsim responder, and discovered by vakr,
proving the whole route from production captures to simulator fixtures
(blueprint 23, 27.8 M3).

Dependencies:
    walk2rec: The portable converter (on the test path).
    tests.simulators.snmp: The snmpsim runner and resolver.

Example:
    $ uv run pytest tests/simulators/snmp/test_walk2rec_snmpsim.py
"""

import asyncio
from pathlib import Path

import walk2rec

from tests.simulators.snmp.simkit import StaticResolver, fake_community
from tests.simulators.snmp.snmprec import parse_line
from tests.simulators.snmp.snmpsim_server import snmpsim
from vakr.adapters.snmp import PysnmpSessionFactory
from vakr.discovery import DiscoveryResult, discover
from vakr.domain.enums import IfAdminStatus
from vakr.ports.clock import SystemClock
from vakr.ports.snmp import SnmpSettings, SnmpTarget

SAMPLE = Path(__file__).parents[3] / "tools" / "snmpsim" / "testdata" / "rtr-sample"


def test_converted_capture_is_discoverable(tmp_path: Path) -> None:
    """Field capture -> walk2rec -> snmpsim -> vakr discovery keeps the device's facts.

    Args:
        tmp_path: snmpsim's data directory.

    Returns:
        None

    Raises:
        None
    """
    found, problems = walk2rec.convert_files(
        sorted(SAMPLE.glob("*.txt")), walk2rec.DEFAULT_HEX_HINTS
    )
    assert len(problems) == 1  # the deliberately unsupported Opaque value
    records = [parse_line(line) for line in walk2rec.render(found).splitlines()]
    community = fake_community()

    async def scenario(port: int) -> DiscoveryResult:
        """Run the scenario against the simulator.

        Args:
            port: The simulator's UDP port.

        Returns:
            DiscoveryResult: What the assertions check.

        Raises:
            None
        """
        factory = PysnmpSessionFactory(StaticResolver({"sample": community}))
        async with factory.open(SnmpTarget("127.0.0.1", "sample", port=port), SnmpSettings()) as s:
            return await discover(s, SystemClock())

    with snmpsim(tmp_path, {community: records}) as port:
        result = asyncio.run(scenario(port))
    assert result.system.sys_name == "rtr-sample.example.net"
    assert result.system.sys_uptime_ticks == 123_456_789
    gi, lo = result.interfaces
    assert (gi.if_name, gi.mac, gi.if_alias) == (
        "Gi0/0/0",
        "02:5e:01:00:00:01",
        "WAN to ISP ckt SIM-1",
    )
    assert gi.admin_status is IfAdminStatus.UP
    assert gi.addresses == ("192.0.2.1/31", "2001:db8::1/128")
    assert (lo.if_type, lo.mac, lo.if_alias) == (24, None, "")
    assert gi.hc_octets
    assert gi.discontinuity is False
