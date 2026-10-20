"""Builders for realistic Cisco IOS XE interface facts and policy rules.

The interfaces are the ones NetOps sees on an IOS XE edge router and an
access switch: ``GigabitEthernet0/0/0`` facing an ISP, a
``TenGigabitEthernet1/1/1`` uplink, ``Port-channel1``, ``Vlan100``,
``Loopback0``, ``Tunnel10``, and an access port described ``user port``.
The module name is unique on purpose: several test directories share one
pythonpath, so a helper called ``conftest`` or ``helpers`` would collide.

Dependencies:
    vakr.policy: Facts, rules, keywords, and the engine under test.

Example:
    >>> edge("GigabitEthernet0/0/0", "ISP: Carrier-X 1G ckt ABC12345").device_name
    'edge-rtr1'
"""

from typing import Any

from vakr.domain.enums import IfAdminStatus, IfOperStatus
from vakr.policy.facts import Annotations, Circuit, InterfaceFacts
from vakr.policy.keywords import KeywordSet, packaged_keywords
from vakr.policy.rules import Policy
from vakr.policy.rules_parse import parse_policy

# IANAifType numbers for the IOS XE interfaces used below.
IF_TYPES = {
    "GigabitEthernet": 6,
    "TenGigabitEthernet": 6,
    "Port-channel": 161,
    "Vlan": 53,
    "Loopback": 24,
    "Tunnel": 131,
}

DESCRIPTIONS = {
    "GigabitEthernet0/0/0": "ISP: Carrier-X 1G ckt ABC12345",
    "GigabitEthernet0/0/1": "[WAN] MPLS primary",
    "TenGigabitEthernet1/1/1": "UPLINK to core-sw1 Te1/0/1",
    "Port-channel1": "UPLINK to core-sw1 Po1",
    "Vlan100": "",
    "Loopback0": "",
    "Tunnel10": "",
}
"""An edge router's interfaces and their ``ifAlias`` descriptions."""


def edge(
    if_name: str,
    alias: str | None = None,
    *,
    device: str = "edge-rtr1",
    groups: tuple[str, ...] = ("wan-routers",),
    annotations: Annotations | None = None,
    if_index: int = 1,
    **extra: Any,
) -> InterfaceFacts:
    """Build facts for one up/up interface on an IOS XE device.

    Args:
        if_name: ``ifName`` such as ``GigabitEthernet0/0/0``.
        alias: ``ifAlias``; ``None`` means the description from ``DESCRIPTIONS``.
        device: Device name.
        groups: Device groups.
        annotations: Explicit vakr annotations.
        if_index: ``ifIndex``.
        **extra: Other ``InterfaceFacts`` fields to override.

    Returns:
        InterfaceFacts: The facts.

    Raises:
        None
    """
    kind = next((k for k in IF_TYPES if if_name.startswith(k)), "GigabitEthernet")
    values: dict[str, Any] = {
        "device_name": device,
        "device_address": "192.0.2.1",
        "site": "nyc01",
        "device_groups": groups,
        "if_index": if_index,
        "if_name": if_name,
        "if_descr": if_name,
        "if_alias": DESCRIPTIONS.get(if_name, "") if alias is None else alias,
        "if_type": IF_TYPES[kind],
        "physical": IF_TYPES[kind] == 6,
        "admin_status": IfAdminStatus.UP,
        "oper_status": IfOperStatus.UP,
        "annotations": annotations or Annotations(),
        "circuit": Circuit(),
    }
    values.update(extra)
    return InterfaceFacts(**values)


def rules(*tables: dict[str, Any], source: str = "") -> Policy:
    """Parse rule tables as they would appear under ``[[interface_policy.rules]]``.

    Args:
        *tables: Rule tables.
        source: Policy source label.

    Returns:
        Policy: The validated policy.

    Raises:
        PolicyError: If a table is invalid.
    """
    return parse_policy({"interface_policy": {"rules": list(tables)}}, source)


def defaults() -> KeywordSet:
    """Return the packaged keywords ISP, WAN, and UPLINK (Appendix E.15).

    Args:
        None

    Returns:
        KeywordSet: The packaged keyword list.

    Raises:
        None
    """
    return packaged_keywords()
