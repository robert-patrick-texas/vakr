"""A deterministic synthetic Cisco IOS XE dataset: 80 routers and 20 switches.

The MVP reference workload (blueprint 27.2) is 100 IOS XE devices with
about 1,600 up/up interfaces. This dataset matches it exactly:

- **router** ``rtr-sim-NNN``: 13 interfaces, 10 up/up. ``Tunnel20``
  (ifIndex 12) has no ifHC* counters, so it is the 32-bit-only interface;
  ``GigabitEthernet0/0/1`` (ifIndex 2) is the one with
  ``ifCounterDiscontinuityTime``. ``Gi0/0/0`` has one IPv4 and two IPv6
  addresses, ``Gi0/0/1`` a primary and a secondary IPv4 address.
- **switch** ``sw-sim-NNN``: 57 interfaces, 40 up/up, including SVIs that
  share the chassis MAC (as Catalyst SVIs do), a port-channel, empty access
  ports, and uplinks without optics (``notPresent``).

Every value is a pure function of the device number, so the same device
always produces the same records. Addresses come from RFC 5737
(192.0.2.0/24, 198.51.100.0/24, 203.0.113.0/24) and RFC 3849 (2001:db8::/32),
MACs are locally administered, and names end in ``example.net``. Counters
and ``sysUpTime`` carry rates, so they advance in both simulators.

Dependencies:
    ipaddress: Address and index encoding.
    tests.simulators.snmp.snmprec: ``Record``.

Example:
    >>> fleet = reference_fleet()
    >>> len(fleet), sum(p.up for d in fleet for p in d.ports)
    (100, 1600)
"""

import ipaddress
from dataclasses import dataclass

from tests.simulators.snmp.ports import router_ports, switch_ports
from tests.simulators.snmp.simtypes import DeviceSpec, Port
from tests.simulators.snmp.snmprec import Record

SYSTEM = (1, 3, 6, 1, 2, 1, 1)
IF_NUMBER = (1, 3, 6, 1, 2, 1, 2, 1, 0)
IF_ENTRY = (1, 3, 6, 1, 2, 1, 2, 2, 1)
IFX_ENTRY = (1, 3, 6, 1, 2, 1, 31, 1, 1, 1)
IP_ADDR_ENTRY = (1, 3, 6, 1, 2, 1, 4, 20, 1)
IP_ADDRESS_ENTRY = (1, 3, 6, 1, 2, 1, 4, 34, 1)
IP_PREFIX_ORIGIN = (1, 3, 6, 1, 2, 1, 4, 32, 1, 5)
ROUTERS = 80
SWITCHES = 20
_DESCR = {
    "router": "Cisco IOS XE Software, Version 17.12.04\r\nCisco IOS Software [Dublin], "
    "c8000be Software (X86_64_LINUX_IOSD-UNIVERSALK9-M), Version 17.12.4, RELEASE SOFTWARE "
    "(fc1)\r\nvakr simulator dataset",
    "switch": "Cisco IOS XE Software, Version 17.09.05\r\nCisco IOS Software [Cupertino], "
    "Catalyst L3 Switch Software (CAT9K_IOSXE), Version 17.9.5, RELEASE SOFTWARE (fc1)\r\n"
    "vakr simulator dataset",
}
_OBJECT_ID = {"router": "1.3.6.1.4.1.9.1.2734", "switch": "1.3.6.1.4.1.9.1.2494"}


def router(number: int, ip_mib: str = "both") -> DeviceSpec:
    """Describe router ``rtr-sim-NNN``.

    Args:
        number: 1 to 80.
        ip_mib: ``both``, ``modern`` (ipAddressTable only), or ``legacy``
            (ipAddrTable only).

    Returns:
        DeviceSpec: The router.

    Raises:
        None
    """
    return DeviceSpec("router", number, f"rtr-sim-{number:03d}", router_ports(number), ip_mib)


def switch(number: int, ip_mib: str = "both") -> DeviceSpec:
    """Describe switch ``sw-sim-NNN``.

    Args:
        number: 1 to 20.
        ip_mib: As for ``router``.

    Returns:
        DeviceSpec: The switch.

    Raises:
        None
    """
    return DeviceSpec("switch", number, f"sw-sim-{number:03d}", switch_ports(number), ip_mib)


def reference_fleet(routers: int = ROUTERS, switches: int = SWITCHES) -> list[DeviceSpec]:
    """Return the MVP reference fleet, or a smaller one for quick tests.

    Args:
        routers: How many routers.
        switches: How many switches.

    Returns:
        list[DeviceSpec]: Routers first, then switches.

    Raises:
        None
    """
    return [router(n) for n in range(1, routers + 1)] + [switch(n) for n in range(1, switches + 1)]


def device_records(spec: DeviceSpec) -> list[Record]:
    """Build every record of one device.

    Args:
        spec: The device.

    Returns:
        list[Record]: System group, IF-MIB, and IP-MIB records.

    Raises:
        None
    """
    records = _system(spec)
    records.append(Record(IF_NUMBER, "2", str(len(spec.ports))))
    for port in spec.ports:
        records += _if_entry(spec, port) + _ifx_entry(spec, port)
        for text in port.addresses:
            records += _addresses(spec, port, ipaddress.ip_interface(text))
    return records


def _system(spec: DeviceSpec) -> list[Record]:
    """Build the system group.

    Args:
        spec: The device.

    Returns:
        list[Record]: sysDescr through sysServices.

    Raises:
        None
    """
    uptime = 1_000_000 + spec.seed * 7_919  # hundredths of a second
    return [
        Record((*SYSTEM, 1, 0), "4x", _DESCR[spec.kind].encode().hex()),
        Record((*SYSTEM, 2, 0), "6", _OBJECT_ID[spec.kind]),
        Record((*SYSTEM, 3, 0), "67", str(uptime), rate=100),
        Record((*SYSTEM, 4, 0), "4", "noc@example.net"),
        Record((*SYSTEM, 5, 0), "4", f"{spec.name}.example.net"),
        Record((*SYSTEM, 6, 0), "4", f"Lab site {spec.number % 5 + 1}, rack {spec.number}"),
        Record((*SYSTEM, 7, 0), "2", "78" if spec.kind == "router" else "6"),
    ]


@dataclass(frozen=True, slots=True)
class _Counters:
    """Initial values and per-second rates of one interface's counters.

    Attributes:
        octets: Initial (inbound, outbound) octets.
        rates: Inbound and outbound octets per second, or ``None`` when idle.
    """

    octets: tuple[int, int]
    rates: tuple[int | None, int | None]

    def packets(self, direction: int, share: int) -> tuple[int, int | None]:
        """Return a packet component's initial value and rate.

        Args:
            direction: 0 inbound, 1 outbound.
            share: Divisor of the octet count (800 unicast, 40,000 multicast,
                160,000 broadcast).

        Returns:
            tuple[int, int | None]: Initial value and rate.

        Raises:
            None
        """
        rate = self.rates[direction]
        return self.octets[direction] // share, None if rate is None else max(1, rate // share)


def _counters(spec: DeviceSpec, port: Port) -> _Counters:
    """Derive an interface's counters from the device and ifIndex.

    Args:
        spec: The device.
        port: The interface.

    Returns:
        _Counters: Deterministic initial values and rates.

    Raises:
        None
    """
    base = spec.seed * 7_919 + port.if_index * 104_729
    octets = (base * 1_000_003 % 2**40, base * 999_983 % 2**40)
    if not port.up or not port.rate_bps:
        return _Counters(octets, (None, None))
    inbound = port.rate_bps // 8
    return _Counters(octets, (inbound, inbound * 3 // 4))


def _if_entry(spec: DeviceSpec, port: Port) -> list[Record]:
    """Build one ifTable row.

    Args:
        spec: The device.
        port: The interface.

    Returns:
        list[Record]: ifIndex through ifOutErrors.

    Raises:
        None
    """
    i, c = port.if_index, _counters(spec, port)
    in_ucast, out_ucast = c.packets(0, 800), c.packets(1, 800)
    mac = ("4x", port.mac.hex()) if port.mac else ("4", "")
    errors = (spec.seed + i) % 7, (spec.seed + i) % 3
    return [
        Record((*IF_ENTRY, 1, i), "2", str(i)),
        Record((*IF_ENTRY, 2, i), "4", port.descr or port.name),
        Record((*IF_ENTRY, 3, i), "2", str(port.if_type)),
        Record((*IF_ENTRY, 4, i), "2", str(port.mtu)),
        Record((*IF_ENTRY, 5, i), "66", str(port.speed_bps)),
        Record((*IF_ENTRY, 6, i), *mac),
        Record((*IF_ENTRY, 7, i), "2", str(port.admin)),
        Record((*IF_ENTRY, 8, i), "2", str(port.oper)),
        Record((*IF_ENTRY, 9, i), "67", str(4_000 + i * 11)),
        Record((*IF_ENTRY, 10, i), "65", str(c.octets[0] % 2**32), c.rates[0]),
        Record((*IF_ENTRY, 11, i), "65", str(in_ucast[0] % 2**32), in_ucast[1]),
        Record((*IF_ENTRY, 13, i), "65", "0"),
        Record((*IF_ENTRY, 14, i), "65", str(errors[0]), port.error_rate),
        Record((*IF_ENTRY, 15, i), "65", "0"),
        Record((*IF_ENTRY, 16, i), "65", str(c.octets[1] % 2**32), c.rates[1]),
        Record((*IF_ENTRY, 17, i), "65", str(out_ucast[0] % 2**32), out_ucast[1]),
        Record((*IF_ENTRY, 19, i), "65", "0"),
        Record((*IF_ENTRY, 20, i), "65", str(errors[1])),
    ]


def _ifx_entry(spec: DeviceSpec, port: Port) -> list[Record]:
    """Build one ifXTable row; HC columns only where the port has them.

    Args:
        spec: The device.
        port: The interface.

    Returns:
        list[Record]: ifName through ifCounterDiscontinuityTime.

    Raises:
        None
    """
    i, c = port.if_index, _counters(spec, port)
    parts = [(d, share) for d in (0, 1) for share in (800, 40_000, 160_000)]
    values = {key: c.packets(*key) for key in parts}
    rows = [Record((*IFX_ENTRY, 1, i), "4", port.name)]
    for column, key in zip((2, 3, 4, 5), [parts[1], parts[2], parts[4], parts[5]], strict=True):
        initial, rate = values[key]
        rows.append(Record((*IFX_ENTRY, column, i), "65", str(initial % 2**32), rate))
    if port.hc:
        rows.append(Record((*IFX_ENTRY, 6, i), "70", str(c.octets[0]), c.rates[0]))
        rows += [
            Record((*IFX_ENTRY, 7 + n, i), "70", str(values[parts[n]][0]), values[parts[n]][1])
            for n in range(3)
        ]
        rows.append(Record((*IFX_ENTRY, 10, i), "70", str(c.octets[1]), c.rates[1]))
        rows += [
            Record(
                (*IFX_ENTRY, 11 + n, i), "70", str(values[parts[3 + n]][0]), values[parts[3 + n]][1]
            )
            for n in range(3)
        ]
    rows += [
        Record((*IFX_ENTRY, 14, i), "2", "1"),
        Record((*IFX_ENTRY, 15, i), "66", str(port.high_speed)),
        Record((*IFX_ENTRY, 16, i), "2", "2"),
        Record((*IFX_ENTRY, 17, i), "2", "1" if port.mac else "2"),
        Record((*IFX_ENTRY, 18, i), "4", port.alias),
    ]
    if port.discontinuity is not None:
        rows.append(Record((*IFX_ENTRY, 19, i), "67", str(port.discontinuity)))
    return rows


def _addresses(
    spec: DeviceSpec, port: Port, iface: ipaddress.IPv4Interface | ipaddress.IPv6Interface
) -> list[Record]:
    """Build the IP-MIB rows for one interface address.

    Args:
        spec: The device; ``ip_mib`` selects which tables exist.
        port: The interface the address is on.
        iface: The address with its prefix.

    Returns:
        list[Record]: ipAddressTable and/or ipAddrTable rows.

    Raises:
        None
    """
    rows: list[Record] = []
    octets = tuple(iface.ip.packed)
    kind = 1 if iface.version == 4 else 2
    if spec.ip_mib in ("both", "modern"):
        index = (kind, len(octets), *octets)
        prefix = (port.if_index, kind, len(octets), *iface.network.network_address.packed)
        pointer = ".".join(map(str, (*IP_PREFIX_ORIGIN, *prefix, iface.network.prefixlen)))
        for column, tag, value in (
            (3, "2", str(port.if_index)),
            (4, "2", "1"),  # unicast
            (5, "6", pointer),
            (6, "2", "2"),  # manual
            (7, "2", "1"),  # preferred
            (10, "2", "1"),  # active
        ):
            rows.append(Record((*IP_ADDRESS_ENTRY, column, *index), tag, value))
    if iface.version == 4 and spec.ip_mib in ("both", "legacy"):
        rows += [
            Record((*IP_ADDR_ENTRY, 1, *octets), "64", str(iface.ip)),
            Record((*IP_ADDR_ENTRY, 2, *octets), "2", str(port.if_index)),
            Record((*IP_ADDR_ENTRY, 3, *octets), "64", str(iface.netmask)),
            Record((*IP_ADDR_ENTRY, 4, *octets), "2", "1"),
            Record((*IP_ADDR_ENTRY, 5, *octets), "2", "18024"),
        ]
    return rows
