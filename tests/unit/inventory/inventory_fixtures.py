"""Builders shared by the inventory tests: devices, discovery results, inventory files.

Addresses are RFC 5737 documentation addresses and names use
``example.net``; MACs come from the RFC 7042 documentation block.

Dependencies:
    vakr.discovery, vakr.domain, vakr.adapters.memory.

Example:
    >>> discovery([row(1, "Gi0/0/0")]).answered
    True
"""

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from vakr.discovery.model import (
    DiscoveredInterface,
    DiscoveryResult,
    SubtableResult,
    SubtableStatus,
    SystemFacts,
)
from vakr.domain.enums import IfAdminStatus, IfOperStatus
from vakr.domain.ids import DeviceId, new_id
from vakr.domain.inventory import API_MANAGED, Device
from vakr.ports.metadata import MetadataUnitOfWorkFactory
from vakr.ports.snmp import SnmpStats

T0 = datetime(2026, 10, 20, 12, tzinfo=UTC)
DAY = timedelta(days=1)

SAMPLE_INVENTORY = """\
[[sites]]
name = "dal1"
code = "DAL1"
region = "US-South"

[[devices]]
name = "edge1.example.net"
address = "192.0.2.10"
site = "dal1"
credential = "network-v2c"
tags = ["edge", "wan"]
groups = ["routers"]
comment = "Primary WAN router"

[[devices]]
name = "sw1.example.net"
address = "192.0.2.20"
site = "dal1"
credential = "network-v2c"
interval = "120s"
reachability = "snmp_fallback"

[[interface_annotations]]
device = "edge1.example.net"
ifname = "Gi0/0/0"
high_priority = true
wan_circuit = true
provider = "Example Carrier"
circuit_id = "CKT-0001"
contracted_bps = 100000000
"""


def row(if_index: int, name: str, **overrides: Any) -> DiscoveredInterface:
    """Build an up/up Ethernet row.

    Args:
        if_index: The ifIndex.
        name: ``ifName``; ``ifDescr`` is the same.
        **overrides: Field values to replace.

    Returns:
        DiscoveredInterface: The row.

    Raises:
        None
    """
    base: dict[str, Any] = {
        "if_index": if_index, "if_name": name, "if_descr": name, "if_alias": "", "if_type": 6,
        "admin_status": IfAdminStatus.UP, "oper_status": IfOperStatus.UP,
        "hc_octets": True, "hc_packets": True, "discontinuity": False,
    }  # fmt: skip
    return DiscoveredInterface(**base | overrides)


def discovery(
    rows: list[DiscoveredInterface], *, complete: bool = True, at: datetime = T0,
    ip_complete: bool = True,
) -> DiscoveryResult:  # fmt: skip
    """Build a discovery result whose tables answered.

    Args:
        rows: The interfaces.
        complete: Whether ``ifTable`` was read completely.
        at: When discovery ran.
        ip_complete: Whether IP-MIB answered completely.

    Returns:
        DiscoveryResult: The result.

    Raises:
        None
    """
    status = SubtableStatus.COMPLETE if complete else SubtableStatus.PARTIAL
    tables = (
        SubtableResult("system", SubtableStatus.COMPLETE),
        SubtableResult("ifTable", status, rows=len(rows)),
        SubtableResult("ifXTable", SubtableStatus.COMPLETE),
        SubtableResult(
            "ipAddressTable", SubtableStatus.COMPLETE if ip_complete else SubtableStatus.FAILED
        ),
    )
    return DiscoveryResult(
        "192.0.2.10:161", at, at, 0.5, SystemFacts(sys_name="edge1"), tuple(rows), tables,
        SnmpStats(requests=10, responses=10),
    )  # fmt: skip


def add_device(factory: MetadataUnitOfWorkFactory, name: str = "edge1.example.net") -> Device:
    """Commit an enabled, API-managed device.

    Args:
        factory: Opens units of work.
        name: Device name.

    Returns:
        Device: The device.

    Raises:
        None
    """
    device = Device(new_id(DeviceId), name, "192.0.2.10", API_MANAGED, T0, T0)
    with factory() as uow:
        uow.devices.add(device)
        uow.commit()
    return device


def write_inventory(directory: Path, text: str = SAMPLE_INVENTORY) -> Path:
    """Write an inventory file into a configuration directory.

    Args:
        directory: The configuration directory.
        text: The file content.

    Returns:
        Path: The file.

    Raises:
        None
    """
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "inventory.toml"
    path.write_text(text, encoding="utf-8")
    return path
