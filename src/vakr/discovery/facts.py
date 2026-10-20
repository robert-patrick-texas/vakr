"""Map discovery results into the interface facts collection policy matches on.

``vakr.policy`` decides from ``InterfaceFacts``, a plain record of what a
rule can match (POLICY-003). This module fills it from one
``DiscoveredInterface`` plus the device's inventory context, without
changing what any fact means: unknown stays ``None`` and is never coerced.

Whether an interface is ``physical`` is derived here, because no MIB object
says so directly. Ports such as Ethernet, serial, and SONET are physical;
loopbacks, SVIs, tunnels, VLAN subinterfaces, port-channels, and MPLS
interfaces are not; an Ethernet-typed name with a dot (``Gi0/0/0.100``) is a
subinterface; any other type is unknown (``None``).

Dependencies:
    vakr.discovery: The discovery result and the ifType sets.
    vakr.policy.facts: ``InterfaceFacts`` and its annotation types.

Safety:
    Pure. When ``DiscoveryResult.addresses_complete`` is False, ``addresses``
    holds only what was retrieved; callers that act on addresses must check
    it, because policy treats an empty list as known-empty.

Example:
    >>> is_physical(6, "Gi0/0/0"), is_physical(135, "Gi0/0/0.100"), is_physical(1, "Null0")
    (True, False, None)
"""

from collections.abc import Callable
from dataclasses import dataclass

from vakr.discovery.mib import LOGICAL_TYPES, PHYSICAL_TYPES
from vakr.discovery.model import DiscoveredInterface, DiscoveryResult
from vakr.policy.facts import Annotations, Circuit, InterfaceFacts

type Annotate = Callable[[DiscoveredInterface], tuple[Annotations, Circuit]]
"""Supplies an interface's operator annotations and circuit metadata."""


@dataclass(frozen=True, slots=True)
class DeviceContext:
    """The inventory facts about the device that policy rules match on.

    Attributes:
        name: Inventory name of the device.
        address: Poll address or DNS name.
        site: Site name, if any.
        tags: Device tags.
        groups: Device groups.
    """

    name: str
    address: str | None = None
    site: str | None = None
    tags: tuple[str, ...] = ()
    groups: tuple[str, ...] = ()


def is_physical(if_type: int | None, if_name: str | None) -> bool | None:
    """Decide whether an interface is a physical port.

    Args:
        if_type: IANAifType number.
        if_name: ``ifName``, for spotting subinterfaces.

    Returns:
        bool | None: True for a port, False for a logical interface, ``None``
        when the type does not say.

    Raises:
        None
    """
    if if_type in LOGICAL_TYPES:
        return False
    if if_type in PHYSICAL_TYPES:
        return not (if_name is not None and "." in if_name)
    return None


def interface_facts(
    device: DeviceContext,
    interface: DiscoveredInterface,
    annotations: Annotations | None = None,
    circuit: Circuit | None = None,
) -> InterfaceFacts:
    """Build the policy facts for one discovered interface.

    Args:
        device: The device's inventory context.
        interface: The discovered interface.
        annotations: Operator annotations, if any.
        circuit: Circuit metadata, if any.

    Returns:
        InterfaceFacts: Facts for ``vakr.policy.engine.decide``.

    Raises:
        None
    """
    return InterfaceFacts(
        device_name=device.name,
        device_address=device.address,
        site=device.site,
        device_tags=device.tags,
        device_groups=device.groups,
        if_index=interface.if_index,
        if_name=interface.if_name,
        if_descr=interface.if_descr,
        if_alias=interface.if_alias,
        if_type=interface.if_type,
        addresses=interface.addresses,
        physical=is_physical(interface.if_type, interface.if_name),
        admin_status=interface.admin_status,
        oper_status=interface.oper_status,
        annotations=Annotations() if annotations is None else annotations,
        circuit=Circuit() if circuit is None else circuit,
    )


def discovery_facts(
    device: DeviceContext, result: DiscoveryResult, annotate: Annotate | None = None
) -> tuple[InterfaceFacts, ...]:
    """Build policy facts for every discovered interface (DISC-007).

    Args:
        device: The device's inventory context.
        result: The discovery result.
        annotate: Supplies annotations and circuit metadata per interface;
            ``None`` means none are set.

    Returns:
        tuple[InterfaceFacts, ...]: One entry per interface, in ifIndex order.

    Raises:
        None
    """
    facts = []
    for interface in result.interfaces:
        annotations, circuit = (None, None) if annotate is None else annotate(interface)
        facts.append(interface_facts(device, interface, annotations, circuit))
    return tuple(facts)
