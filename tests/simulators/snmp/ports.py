"""Interface layouts of the simulated IOS XE router and Catalyst switch.

Addresses use only the documentation ranges of RFC 5737 and RFC 3849,
allocated so no two devices share an address:

- 192.0.2.0/24: router ``Gi0/0/0`` /31 links (.0-.158) and switch
  ``Vlan10`` /30 subnets (.161-.237);
- 198.51.100.0/24: router loopbacks (.1-.80), router ``Gi0/0/1`` primary
  (.101-.180), and switch management (.201-.220);
- 203.0.113.0/24: router ``Gi0/0/1`` secondary (.1-.80), router management
  (.101-.180), and switch ``Vlan20`` (.201-.220);
- 2001:db8::/32: per-device /64s and /128 loopbacks.

MACs have the locally administered bit set (``02:5e:...``).

Dependencies:
    tests.simulators.snmp.simtypes: ``Port``.

Example:
    >>> [p.name for p in router_ports(1) if not p.hc]
    ['Tunnel20']
"""

from tests.simulators.snmp.simtypes import SATURATED, Port

GIG = 1_000_000_000


def _mac(kind: int, device: int, if_index: int) -> bytes:
    """Return a locally administered MAC for one interface.

    Args:
        kind: 1 for routers, 2 for switches.
        device: Device number.
        if_index: Interface index, or 0 for the chassis MAC.

    Returns:
        bytes: Six octets beginning ``02:5e``.

    Raises:
        None
    """
    return bytes([0x02, 0x5E, kind, device & 0xFF, if_index >> 8, if_index & 0xFF])


def router_ports(n: int) -> tuple[Port, ...]:
    """Return router ``n``'s 13 interfaces; 10 are up/up.

    Args:
        n: Router number, 1 to 80.

    Returns:
        tuple[Port, ...]: Interfaces in ifIndex order.

    Raises:
        None
    """
    v6 = (f"2001:db8:0:{n:x}::1/64", f"2001:db8:1:{n:x}::1/64")
    return (
        Port(1, "GigabitEthernet0/0/0", 6, GIG, 1000, alias=f"ISP sim-carrier ckt SIM{n:04d}",
             mac=_mac(1, n, 1), addresses=(f"192.0.2.{2 * (n - 1)}/31", *v6),
             rate_bps=40_000_000, error_rate=1),
        Port(2, "GigabitEthernet0/0/1", 6, GIG, 1000, alias="UPLINK to core", mac=_mac(1, n, 2),
             discontinuity=2_000 + n, addresses=(f"198.51.100.{100 + n}/24", f"203.0.113.{n}/24"),
             rate_bps=120_000_000),
        Port(3, "GigabitEthernet0/0/2", 6, GIG, 1000, oper=2, alias="LAN users", mac=_mac(1, n, 3)),
        Port(4, "GigabitEthernet0/0/3", 6, GIG, 1000, admin=2, oper=2, mac=_mac(1, n, 4)),
        Port(5, "TenGigabitEthernet0/1/0", 6, SATURATED, 10_000, alias="WAN backbone",
             mac=_mac(1, n, 5), rate_bps=900_000_000),
        Port(6, "TenGigabitEthernet0/1/1", 6, SATURATED, 10_000, alias="DC interconnect",
             mac=_mac(1, n, 6), rate_bps=2_500_000_000),
        Port(7, "TenGigabitEthernet0/1/2", 6, SATURATED, 10_000, oper=6, mac=_mac(1, n, 7)),
        Port(8, "GigabitEthernet0", 6, GIG, 1000, alias="OOB management", mac=_mac(1, n, 8),
             addresses=(f"203.0.113.{100 + n}/24",), rate_bps=50_000),
        Port(9, "Null0", 1, SATURATED, 10_000),
        Port(10, "Loopback0", 24, SATURATED, 8_000, mtu=1514, alias="router-id",
             addresses=(f"198.51.100.{n}/32", f"2001:db8:ffff::{n:x}/128")),
        Port(11, "Tunnel10", 131, 100_000, 0, mtu=9976, alias="WAN DMVPN hub", rate_bps=2_000_000),
        Port(12, "Tunnel20", 131, 100_000, 0, mtu=9976, alias="legacy GRE", hc=False,
             rate_bps=64_000),
        Port(13, "GigabitEthernet0/0/0.100", 135, GIG, 1000, alias="customer vlan 100",
             mac=_mac(1, n, 1), rate_bps=5_000_000),
    )  # fmt: skip


def switch_ports(k: int) -> tuple[Port, ...]:
    """Return switch ``k``'s 57 interfaces; 40 are up/up.

    Args:
        k: Switch number, 1 to 20.

    Returns:
        tuple[Port, ...]: Interfaces in ifIndex order.

    Raises:
        None
    """
    chassis = _mac(2, k, 0)  # Catalyst SVIs all report the chassis MAC
    ports = [
        Port(1, "GigabitEthernet0/0", 6, GIG, 1000, alias="OOB management", mac=_mac(2, k, 1),
             addresses=(f"198.51.100.{200 + k}/24",), rate_bps=20_000),
        Port(2, "Vlan1", 53, GIG, 1000, admin=2, oper=2, mac=chassis),
        Port(3, "Vlan10", 53, GIG, 1000, alias="users", mac=chassis,
             addresses=(f"192.0.2.{160 + 4 * (k - 1) + 1}/30",), rate_bps=30_000_000),
        Port(4, "Vlan20", 53, GIG, 1000, alias="voice", mac=chassis,
             addresses=(f"203.0.113.{200 + k}/24", f"2001:db8:100:{k:x}::1/64"),
             rate_bps=2_000_000),
        Port(5, "Port-channel1", 161, SATURATED, 20_000, alias="UPLINK to distribution",
             mac=_mac(2, k, 56), rate_bps=400_000_000),
    ]  # fmt: skip
    for p in range(1, 49):
        up = p <= 34
        ports.append(
            Port(7 + p, f"GigabitEthernet1/0/{p}", 6, GIG, 1000, admin=2 if p > 44 else 1,
                 oper=1 if up else 2, alias=f"access port {p}" if up else "",
                 mac=_mac(2, k, 7 + p), rate_bps=1_000_000 + p * 50_000 if up else 0)
        )  # fmt: skip
    for u, alias in enumerate(("UPLINK dist-a", "UPLINK dist-b", "", ""), start=1):
        ports.append(
            Port(55 + u, f"TenGigabitEthernet1/1/{u}", 6, SATURATED, 10_000, oper=1 if alias else 6,
                 alias=alias, mac=_mac(2, k, 55 + u), rate_bps=200_000_000 if alias else 0)
        )  # fmt: skip
    return tuple(ports)
