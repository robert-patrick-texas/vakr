"""The standards-based OIDs vakr reads: the blueprint 9.4 discovery catalog.

Every object comes from SNMPv2-MIB (RFC 3418), IF-MIB (RFC 2863), or IP-MIB
(RFC 4293), so discovery works on any compliant agent. Columns are listed by
their numeric sub-identifier under each table entry; an instance OID is the
column followed by the row index, for example ``ifDescr.3`` is
``1.3.6.1.2.1.2.2.1.2.3``. ``docs/operations/snmp.md`` has the full catalog
with types, units, stages, fallbacks, and IOS XE caveats.

Dependencies:
    vakr.ports.snmp: The ``Oid`` type.

Safety:
    Constants only.

Example:
    >>> instance(IF_COLUMNS["ifOperStatus"], 3)
    (1, 3, 6, 1, 2, 1, 2, 2, 1, 8, 3)
"""

from vakr.logic.capacity import PORT_TYPES
from vakr.ports.snmp import Oid

SYSTEM = (1, 3, 6, 1, 2, 1, 1)
SYS_SCALARS: dict[str, Oid] = {
    "sysDescr": (*SYSTEM, 1, 0),
    "sysObjectID": (*SYSTEM, 2, 0),
    "sysUpTime": (*SYSTEM, 3, 0),
    "sysContact": (*SYSTEM, 4, 0),
    "sysName": (*SYSTEM, 5, 0),
    "sysLocation": (*SYSTEM, 6, 0),
}
"""System group scalars collected by discovery (DISC-004)."""
SYS_UPTIME = SYS_SCALARS["sysUpTime"]

IF_ENTRY = (1, 3, 6, 1, 2, 1, 2, 2, 1)
IFX_ENTRY = (1, 3, 6, 1, 2, 1, 31, 1, 1, 1)
IP_ADDR_ENTRY = (1, 3, 6, 1, 2, 1, 4, 20, 1)
IP_ADDRESS_ENTRY = (1, 3, 6, 1, 2, 1, 4, 34, 1)
IP_ADDRESS_PREFIX_ENTRY = (1, 3, 6, 1, 2, 1, 4, 32, 1)

IF_COLUMNS: dict[str, Oid] = {
    name: (*IF_ENTRY, n)
    for name, n in (
        ("ifDescr", 2),
        ("ifType", 3),
        ("ifMtu", 4),
        ("ifSpeed", 5),
        ("ifPhysAddress", 6),
        ("ifAdminStatus", 7),
        ("ifOperStatus", 8),
        ("ifLastChange", 9),
        ("ifInOctets", 10),
        ("ifInUcastPkts", 11),
        ("ifInErrors", 14),
        ("ifOutOctets", 16),
        ("ifOutUcastPkts", 17),
        ("ifOutErrors", 20),
    )
}
"""ifTable columns by name (RFC 2863)."""

IFX_COLUMNS: dict[str, Oid] = {
    name: (*IFX_ENTRY, n)
    for name, n in (
        ("ifName", 1),
        ("ifInMulticastPkts", 2),
        ("ifInBroadcastPkts", 3),
        ("ifOutMulticastPkts", 4),
        ("ifOutBroadcastPkts", 5),
        ("ifHCInOctets", 6),
        ("ifHCInUcastPkts", 7),
        ("ifHCInMulticastPkts", 8),
        ("ifHCInBroadcastPkts", 9),
        ("ifHCOutOctets", 10),
        ("ifHCOutUcastPkts", 11),
        ("ifHCOutMulticastPkts", 12),
        ("ifHCOutBroadcastPkts", 13),
        ("ifHighSpeed", 15),
        ("ifAlias", 18),
        ("ifCounterDiscontinuityTime", 19),
    )
}
"""ifXTable columns by name (RFC 2863)."""

IP_ADDRESS_COLUMNS: dict[str, Oid] = {
    "ipAddressIfIndex": (*IP_ADDRESS_ENTRY, 3),
    "ipAddressType": (*IP_ADDRESS_ENTRY, 4),
    "ipAddressPrefix": (*IP_ADDRESS_ENTRY, 5),
}
"""ipAddressTable columns (RFC 4293), indexed by address type and address."""

IP_ADDR_COLUMNS: dict[str, Oid] = {
    "ipAdEntIfIndex": (*IP_ADDR_ENTRY, 2),
    "ipAdEntNetMask": (*IP_ADDR_ENTRY, 3),
}
"""The deprecated IPv4-only ipAddrTable (RFC 4293), indexed by address."""

DISCOVERY_IF_COLUMNS = (
    "ifDescr",
    "ifType",
    "ifMtu",
    "ifSpeed",
    "ifPhysAddress",
    "ifAdminStatus",
    "ifOperStatus",
    "ifLastChange",
)
"""ifTable columns discovery walks; counters are left to Stage 2 (SNMP-004)."""
DISCOVERY_IFX_COLUMNS = (
    "ifName",
    "ifHighSpeed",
    "ifAlias",
    "ifHCInOctets",
    "ifHCInUcastPkts",
    "ifCounterDiscontinuityTime",
)
"""ifXTable columns discovery walks; the last three probe capabilities (SNMP-010)."""

PHYSICAL_TYPES = PORT_TYPES
"""IANAifType values that are ports: Ethernet, DS1/E1/DS3, serial, SONET, Wi-Fi."""
LOGICAL_TYPES = frozenset({24, 53, 131, 135, 136, 150, 161, 166})
"""IANAifType values that are not: loopback, SVI, tunnel, VLAN subinterface, LAG, MPLS."""


def instance(column: Oid, index: int | tuple[int, ...]) -> Oid:
    """Build the instance OID of one column and row.

    Args:
        column: The column OID.
        index: The row index, such as an ifIndex.

    Returns:
        Oid: The column followed by the index.

    Raises:
        None
    """
    return (*column, *index) if isinstance(index, tuple) else (*column, index)
