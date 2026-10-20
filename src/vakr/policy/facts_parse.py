"""Parse interface facts from a TOML or JSON fixture (CFG-009).

Until inventory storage exists (M3/M4), ``vakr policy explain`` reads the
interfaces to explain from a file. Each ``[[interfaces]]`` entry has a
``device`` table (``name``, ``address``, ``site``, ``tags``, ``groups``), an
``interface`` table (``ifindex``, ``ifname``, ``ifdescr``, ``ifalias``,
``iftype``, ``addresses``, ``physical``, ``admin_status``, ``oper_status``),
and optional ``annotations`` and ``circuit`` tables. Keys match the rule
``match`` fields, so a fixture reads like the rules that select it.

Dependencies:
    ipaddress: Canonical interface addresses.
    vakr.policy.reader: Typed reads with dotted-path problems.

Safety:
    Fixture files hold inventory facts only; never put credentials in them.

Example:
    >>> doc = {"interfaces": [{"device": {"name": "edge-rtr1"},
    ...     "interface": {"ifname": "Tunnel10", "oper_status": "lowerLayerDown"}}]}
    >>> parse_facts(doc)[0].oper_status.name
    'LOWER_LAYER_DOWN'
"""

import ipaddress
from collections.abc import Mapping
from enum import IntEnum
from typing import Any

from vakr.domain.enums import IfAdminStatus, IfOperStatus
from vakr.policy.errors import PolicyIssue, raise_if_any
from vakr.policy.facts import Annotations, Circuit, InterfaceFacts
from vakr.policy.reader import TableReader
from vakr.policy.settings import read_interest

MAX_FACTS = 10000
"""Most interfaces one fixture may hold."""

_DEVICE_KEYS = ("name", "address", "site", "tags", "groups")
_INTERFACE_KEYS = (
    "ifindex",
    "ifname",
    "ifdescr",
    "ifalias",
    "iftype",
    "addresses",
    "physical",
    "admin_status",
    "oper_status",
)
_ANNOTATION_KEYS = ("interest", "high_priority", "sticky", "wan_circuit", "display_rank", "tags")
_CIRCUIT_KEYS = ("provider", "circuit_id", "contracted_bps")


def _status[T: IntEnum](reader: TableReader, key: str, enum: type[T]) -> T | None:
    """Read an IF-MIB status as a name (``up``, ``lowerLayerDown``) or number.

    Args:
        reader: Reader over the ``interface`` table.
        key: ``admin_status`` or ``oper_status``.
        enum: The IF-MIB enum.

    Returns:
        T | None: The status, or ``None`` when absent or invalid.

    Raises:
        None
    """
    value = reader.data.get(key)
    if value is None:
        return None
    by_name = {member.name.replace("_", "").lower(): member for member in enum}
    if isinstance(value, str) and value.replace("_", "").lower() in by_name:
        return by_name[value.replace("_", "").lower()]
    if isinstance(value, int) and not isinstance(value, bool) and value in enum._value2member_map_:
        return enum(value)
    names = ", ".join(member.name.lower() for member in enum)
    reader.problem(key, f"expected one of: {names}")
    return None


def _addresses(reader: TableReader) -> tuple[str, ...]:
    """Read interface addresses and render them canonically, such as ``2001:db8::1/64``.

    Args:
        reader: Reader over the ``interface`` table.

    Returns:
        tuple[str, ...]: Valid addresses in ``address/prefix`` form.

    Raises:
        None
    """
    found = []
    for index, text in enumerate(reader.strings("addresses")):
        try:
            found.append(ipaddress.ip_interface(text).with_prefixlen)
        except ValueError:
            reader.problem(f"addresses[{index}]", "expected an IPv4 or IPv6 address/prefix")
    return tuple(found)


def _annotations(table: object, path: str, issues: list[PolicyIssue]) -> Annotations:
    """Read the explicit vakr annotations.

    Args:
        table: The ``annotations`` table as loaded.
        path: Dotted location.
        issues: List that problems are appended to.

    Returns:
        Annotations: The annotations; unset values are ``None``.

    Raises:
        None
    """
    reader = TableReader(table, path, issues, _ANNOTATION_KEYS)
    return Annotations(
        interest=read_interest(reader),
        sticky=reader.boolean("sticky"),
        wan_circuit=reader.boolean("wan_circuit"),
        display_rank=reader.integer("display_rank", -1_000_000, 1_000_000),
        tags=reader.strings("tags"),
    )


def _interface(entry: object, path: str, issues: list[PolicyIssue]) -> InterfaceFacts | None:
    """Read one ``[[interfaces]]`` entry.

    Args:
        entry: The entry as loaded.
        path: Dotted location.
        issues: List that problems are appended to.

    Returns:
        InterfaceFacts | None: The facts, or ``None`` without a device name.

    Raises:
        None
    """
    keys = ("device", "interface", "annotations", "circuit")
    outer = TableReader(entry, path, issues, keys)
    device = TableReader(outer.data.get("device", {}), outer.at("device"), issues, _DEVICE_KEYS)
    iface = TableReader(
        outer.data.get("interface", {}), outer.at("interface"), issues, _INTERFACE_KEYS
    )
    circuit = TableReader(outer.data.get("circuit", {}), outer.at("circuit"), issues, _CIRCUIT_KEYS)
    name = device.text("name", required=True)
    facts = InterfaceFacts(
        device_name=name or "",
        device_address=device.text("address"),
        site=device.text("site"),
        device_tags=device.strings("tags"),
        device_groups=device.strings("groups"),
        if_index=iface.integer("ifindex", 1, 2**31 - 1),
        if_name=iface.text("ifname"),
        if_descr=iface.text("ifdescr"),
        if_alias=iface.text("ifalias"),
        if_type=iface.integer("iftype", 1, 2**31 - 1),
        addresses=_addresses(iface),
        physical=iface.boolean("physical"),
        admin_status=_status(iface, "admin_status", IfAdminStatus),
        oper_status=_status(iface, "oper_status", IfOperStatus),
        annotations=_annotations(
            outer.data.get("annotations", {}), outer.at("annotations"), issues
        ),
        circuit=Circuit(
            provider=circuit.text("provider"),
            circuit_id=circuit.text("circuit_id"),
            contracted_bps=circuit.integer("contracted_bps", 1),
        ),
    )
    return facts if name else None


def parse_facts(data: Mapping[str, Any]) -> tuple[InterfaceFacts, ...]:
    """Validate a facts document with an ``interfaces`` list.

    Args:
        data: Top-level table as loaded from TOML or JSON.

    Returns:
        tuple[InterfaceFacts, ...]: The interfaces, in file order.

    Raises:
        PolicyError: Listing every problem with its dotted path.
    """
    issues: list[PolicyIssue] = []
    top = TableReader(data, "", issues, ("interfaces",))
    entries = top.data.get("interfaces")
    if not isinstance(entries, list) or not 1 <= len(entries) <= MAX_FACTS:
        top.problem("interfaces", f"expected a list of 1 to {MAX_FACTS} [[interfaces]] tables")
        entries = []
    found = [f for i, e in enumerate(entries) if (f := _interface(e, f"interfaces[{i}]", issues))]
    raise_if_any(issues)
    return tuple(found)
