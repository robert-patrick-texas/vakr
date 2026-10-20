"""DISC-005, DISC-006, DISC-008: discovery parsing, interface assembly, and policy facts.

Pure functions are checked with hand-built walk results: IP-MIB index and
prefix decoding (including zoned addresses), masks, MACs, missing-field and
capability-probe semantics, ifType-based physical classification, and the
mapping into ``vakr.policy`` facts, which keyword classification then uses.

Dependencies:
    pytest: Parametrised cases.
    vakr.discovery, vakr.policy: The code under test.

Example:
    $ uv run pytest tests/unit/discovery/test_discovery_parse.py
"""

import ipaddress

import pytest

from vakr.discovery import DeviceContext, discovery_facts, is_physical
from vakr.discovery.interfaces import build_interfaces
from vakr.discovery.mib import IF_COLUMNS, IFX_COLUMNS, IP_ADDR_COLUMNS, IP_ADDRESS_COLUMNS
from vakr.discovery.model import DiscoveredInterface, DiscoveryResult, SystemFacts
from vakr.discovery.parse import (
    address_from_index,
    interface_address,
    legacy_addresses,
    mac_text,
    mask_length,
    modern_addresses,
    prefix_from_pointer,
)
from vakr.domain.enums import IfAdminStatus, IfOperStatus
from vakr.domain.quality import Quality
from vakr.policy.classify import classify
from vakr.policy.keywords import packaged_keywords
from vakr.ports.clock import SystemClock
from vakr.ports.snmp import Oid, SnmpError, SnmpErrorKind, ValueKind, VarBind, WalkResult

V6 = tuple(ipaddress.IPv6Address("2001:db8::1").packed)


def walk(
    column: Oid, rows: dict, kind: ValueKind = ValueKind.INTEGER, complete: bool = True
) -> WalkResult:
    """Build a column walk result.

    Args:
        column: The column OID.
        rows: Row index (int or tuple) to value.
        kind: The values' kind.
        complete: Whether the walk reached its end.

    Returns:
        WalkResult: The walk.

    Raises:
        None
    """
    varbinds = tuple(
        VarBind((*column, *(i if isinstance(i, tuple) else (i,))), kind, v) for i, v in rows.items()
    )
    error = None if complete else SnmpError(SnmpErrorKind.TIMEOUT, "no answer")
    return WalkResult(column, varbinds, complete, error)


@pytest.mark.parametrize(
    ("index", "expected"),
    [
        ((1, 4, 192, 0, 2, 1), "192.0.2.1"),
        ((2, 16, *V6), "2001:db8::1"),
        ((3, 8, 192, 0, 2, 1, 0, 0, 0, 7), "192.0.2.1"),
        ((4, 20, *V6, 0, 0, 0, 7), "2001:db8::1"),
        ((1, 3, 192, 0, 2), None),
        ((1, 4, 192, 0, 2, 999), None),
        ((9, 4, 192, 0, 2, 1), None),
        ((1,), None),
    ],
)
def test_ip_address_table_index_decoding(index: tuple[int, ...], expected: str | None) -> None:
    """DISC-006: ipAddressTable indexes decode, zones are dropped, malformed ones skipped.

    Args:
        index: The row index.
        expected: The address, or ``None``.

    Returns:
        None

    Raises:
        None
    """
    ip = address_from_index(index)
    assert (None if ip is None else str(ip)) == expected


def test_prefixes_masks_and_macs() -> None:
    """Prefix pointers, netmasks, and MACs parse; unknowns stay unknown.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    ip = ipaddress.ip_address("192.0.2.1")
    assert prefix_from_pointer((1, 3, 6, 1, 2, 1, 4, 32, 1, 5, 1, 1, 4, 192, 0, 2, 0, 31), ip) == 31
    assert prefix_from_pointer((0, 0), ip) is None
    assert prefix_from_pointer((1, 99), ip) is None
    assert mask_length(bytes([255, 255, 255, 252])) == 30
    assert mask_length(bytes([255, 0, 255, 0])) is None
    assert mask_length(b"\xff") is None
    assert interface_address(ip, None) == "192.0.2.1/32"
    assert mac_text(b"") is None
    assert mac_text(bytes(range(6))) == "00:01:02:03:04:05"


def test_modern_and_legacy_address_tables() -> None:
    """DISC-006: several addresses per interface; broadcast rows and junk indexes skipped.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    v4 = (1, 4, 192, 0, 2, 1)
    v6 = (2, 16, *V6)
    walks = {
        "ipAddressIfIndex": walk(
            IP_ADDRESS_COLUMNS["ipAddressIfIndex"], {v4: 3, v6: 3, (1, 4, 192, 0, 2, 255): 3}
        ),
        "ipAddressType": walk(
            IP_ADDRESS_COLUMNS["ipAddressType"], {v4: 1, v6: 1, (1, 4, 192, 0, 2, 255): 3}
        ),
        "ipAddressPrefix": walk(
            IP_ADDRESS_COLUMNS["ipAddressPrefix"],
            {v4: (1, 3, 6, 1, 2, 1, 4, 32, 1, 5, 3, 1, 4, 192, 0, 2, 0, 24), v6: (0, 0)},
            ValueKind.OBJECT_ID,
        ),
    }
    found, unknown, saw_ipv4 = modern_addresses(walks, IP_ADDRESS_COLUMNS)
    assert found == {3: ["192.0.2.1/24", "2001:db8::1/128"]}
    assert unknown == {3}
    assert saw_ipv4
    legacy = {
        "ipAdEntIfIndex": walk(
            IP_ADDR_COLUMNS["ipAdEntIfIndex"], {(198, 51, 100, 7): 5, (1, 2): 5}
        ),
        "ipAdEntNetMask": walk(
            IP_ADDR_COLUMNS["ipAdEntNetMask"],
            {(198, 51, 100, 7): bytes([255] * 3 + [0])},
            ValueKind.IP_ADDRESS,
        ),
    }
    assert legacy_addresses(legacy, IP_ADDR_COLUMNS) == ({5: ["198.51.100.7/24"]}, set())


def test_build_interfaces_flags_missing_fields_and_unknown_capabilities() -> None:
    """DISC-008, SNMP-010: absence after a failed walk is missing; probes are tri-state.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    walks = {
        "ifDescr": walk(IF_COLUMNS["ifDescr"], {1: b"Gi0/0/0", 2: b"Tu20"}, ValueKind.OCTET_STRING),
        "ifType": walk(IF_COLUMNS["ifType"], {1: 6, 2: 131}),
        "ifAdminStatus": walk(IF_COLUMNS["ifAdminStatus"], {1: 1, 2: 9}),
        "ifOperStatus": walk(IF_COLUMNS["ifOperStatus"], {1: 1}, complete=False),
        "ifPhysAddress": walk(
            IF_COLUMNS["ifPhysAddress"], {1: bytes(6), 2: b""}, ValueKind.OCTET_STRING
        ),
        "ifName": walk(
            IFX_COLUMNS["ifName"], {1: b"Gi0/0/0", 2: b"Tunnel20"}, ValueKind.OCTET_STRING
        ),
        "ifAlias": walk(IFX_COLUMNS["ifAlias"], {1: b"UPLINK core"}, ValueKind.OCTET_STRING),
        "ifHCInOctets": walk(IFX_COLUMNS["ifHCInOctets"], {1: 5}, ValueKind.COUNTER64),
        "ifCounterDiscontinuityTime": walk(
            IFX_COLUMNS["ifCounterDiscontinuityTime"], {}, complete=False
        ),
    }
    for name in ("ifMtu", "ifSpeed", "ifLastChange"):
        walks[name] = walk(IF_COLUMNS[name], {})
    walks["ifHighSpeed"] = walk(IFX_COLUMNS["ifHighSpeed"], {})
    first, second = build_interfaces(walks, {1: ["192.0.2.1/31"]}, {2})
    assert first.up_up
    assert first.mac == "00:00:00:00:00:00"
    assert first.missing == ()
    assert (first.hc_octets, first.hc_packets, first.discontinuity) == (True, None, None)
    assert second.admin_status is None
    assert second.oper_status is None
    assert second.mac is None
    assert second.missing == ("ifAdminStatus", "ifOperStatus", "address_prefix")
    assert second.hc_octets is False
    assert second.quality is Quality.MISSING_FIELDS
    assert second.if_alias is None  # a complete walk without the row: not collected, not missing


@pytest.mark.parametrize(
    ("if_type", "name", "physical"),
    [
        (6, "GigabitEthernet0/0/0", True),
        (6, "GigabitEthernet0/0/0.100", False),
        (135, "Gi0/0/0.100", False),
        (24, "Loopback0", False),
        (53, "Vlan10", False),
        (161, "Port-channel1", False),
        (131, "Tunnel10", False),
        (1, "Null0", None),
        (None, None, None),
    ],
)
def test_physical_follows_iftype_and_subinterface_names(
    if_type: int | None, name: str | None, physical: bool | None
) -> None:
    """Ports are physical, logical interfaces are not, and unknown types stay unknown.

    Args:
        if_type: IANAifType.
        name: ifName.
        physical: Expected result.

    Returns:
        None

    Raises:
        None
    """
    assert is_physical(if_type, name) is physical


def test_discovery_facts_feed_policy_keyword_classification() -> None:
    """META-012, DISC-007: discovered aliases reach policy, which tags them unchanged.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    now = SystemClock().now_utc()
    iface = DiscoveredInterface(
        1, if_name="Gi0/0/0", if_alias="WAN ISP circuit", if_type=6,
        admin_status=IfAdminStatus.UP, oper_status=IfOperStatus.DOWN, addresses=("192.0.2.1/31",),
    )  # fmt: skip
    result = DiscoveryResult("192.0.2.1:161", now, now, 0.0, SystemFacts(), (iface,), ())
    (facts,) = discovery_facts(DeviceContext("edge-rtr1", "192.0.2.1", tags=("edge",)), result)
    assert (facts.device_name, facts.if_alias, facts.physical, facts.addresses) == (
        "edge-rtr1", "WAN ISP circuit", True, ("192.0.2.1/31",)
    )  # fmt: skip
    assert facts.oper_status is IfOperStatus.DOWN
    assert facts.device_tags == ("edge",)
    assert set(classify(facts.if_alias, packaged_keywords()).tags) == {"isp", "wan"}
