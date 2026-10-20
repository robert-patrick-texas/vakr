"""What a policy rule can match on for one interface (POLICY-003).

``InterfaceFacts`` gathers, for one interface, the inventory and discovery
facts a rule may select on: the device's name, address, site, tags, and
groups; the IF-MIB identity and state (``ifName``, ``ifDescr``, ``ifAlias``,
``ifType``, ``ifIndex``, admin/oper status); addresses; whether it is
physical; the operator's explicit annotations; and circuit metadata. It is
defined here, not in the domain model, so the policy engine stays a pure
function of plain values and can be explained from a fixture file before
inventory storage exists.

``None`` means "not known" and is different from an empty string: an
interface with ``if_alias = ""`` has a blank description, while
``if_alias = None`` means the description was never collected. Unknown
values match no pattern (missing data is never coerced, blueprint 4.3).

Dependencies:
    vakr.domain.enums: Interest class and IF-MIB status values.

Safety:
    Facts carry no credentials. ``ifIndex`` is a match field only, never
    identity (IFID-001).

Example:
    >>> f = InterfaceFacts("edge-rtr1", if_name="GigabitEthernet0/0/0", if_index=1)
    >>> field_values(f, MatchField.IFNAME), field_values(f, MatchField.IFINDEX)
    (('GigabitEthernet0/0/0',), ('1',))
"""

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from enum import StrEnum

from vakr.domain.enums import IfAdminStatus, IfOperStatus, InterestClass


@dataclass(frozen=True, slots=True)
class Annotations:
    """Explicit vakr annotations on an interface (META-007, META-011, META-013).

    These outrank description keywords and policy rules (META-012). ``None``
    means the operator has not set the value.

    Attributes:
        interest: Explicit interest class; ``high_priority`` is an alias.
        sticky: Pin the interface to the top of device pages (LIFE-007).
        wan_circuit: Mark the interface as a WAN circuit.
        display_rank: Lower ranks list first among equals (LIFE-007).
        tags: Tags added in vakr, stored apart from description tags.
    """

    interest: InterestClass | None = None
    sticky: bool | None = None
    wan_circuit: bool | None = None
    display_rank: int | None = None
    tags: tuple[str, ...] = ()

    @property
    def classifies(self) -> bool:
        """Whether the operator has classified the interface in vakr (META-013).

        Args:
            None

        Returns:
            bool: ``True`` when interest, sticky, WAN circuit, or any tag is set.

        Raises:
            None
        """
        flags = (self.interest, self.sticky, self.wan_circuit)
        return any(value is not None for value in flags) or bool(self.tags)


@dataclass(frozen=True, slots=True)
class Circuit:
    """Provider circuit metadata on an interface (META-007).

    Attributes:
        provider: Carrier or ISP name.
        circuit_id: Provider circuit identifier.
        contracted_bps: Contracted rate in bits per second.
    """

    provider: str | None = None
    circuit_id: str | None = None
    contracted_bps: int | None = None


@dataclass(frozen=True, slots=True)
class InterfaceFacts:
    """Everything policy rules may match on for one interface; immutable.

    Attributes:
        device_name: Inventory name of the device.
        device_address: Poll address or DNS name.
        site: Site name, or ``None`` when the device has no site.
        device_tags: Device tags (META-005).
        device_groups: Device groups (META-005).
        if_index: Current ``ifIndex``; a match field, never identity.
        if_name: ``ifName``, for example ``GigabitEthernet0/0/0``.
        if_descr: ``ifDescr``.
        if_alias: ``ifAlias``, the operator-set description.
        if_type: IANAifType number, for example 6 for ``ethernetCsmacd``.
        addresses: Interface addresses in ``address/prefix`` form.
        physical: ``True`` for a physical port, ``False`` for a logical
            interface (VLAN, loopback, tunnel, port-channel).
        admin_status: ``ifAdminStatus``.
        oper_status: ``ifOperStatus``.
        annotations: Explicit vakr annotations.
        circuit: Circuit metadata.
    """

    device_name: str
    device_address: str | None = None
    site: str | None = None
    device_tags: tuple[str, ...] = ()
    device_groups: tuple[str, ...] = ()
    if_index: int | None = None
    if_name: str | None = None
    if_descr: str | None = None
    if_alias: str | None = None
    if_type: int | None = None
    addresses: tuple[str, ...] = ()
    physical: bool | None = None
    admin_status: IfAdminStatus | None = None
    oper_status: IfOperStatus | None = None
    annotations: Annotations = field(default_factory=Annotations)
    circuit: Circuit = field(default_factory=Circuit)

    @property
    def label(self) -> str:
        """Short operator-facing name such as ``edge-rtr1 GigabitEthernet0/0/0``.

        Args:
            None

        Returns:
            str: Device name and ``ifName``, or ``ifIndex`` when unnamed.

        Raises:
            None
        """
        name = self.if_name if self.if_name is not None else f"ifIndex {self.if_index}"
        return f"{self.device_name} {name}"


class MatchField(StrEnum):
    """Fact fields a rule's ``match`` table may name; values are the TOML keys."""

    DEVICE = "device"
    DEVICE_ADDRESS = "device_address"
    SITE = "site"
    DEVICE_TAGS = "device_tags"
    DEVICE_GROUPS = "device_groups"
    TAGS = "tags"
    INTERFACE_TAGS = "interface_tags"
    IFNAME = "ifname"
    IFDESCR = "ifdescr"
    IFALIAS = "ifalias"
    IFTYPE = "iftype"
    IFINDEX = "ifindex"
    ADDRESSES = "addresses"
    PHYSICAL = "physical"
    ADMIN_STATUS = "admin_status"
    OPER_STATUS = "oper_status"
    PROVIDER = "provider"
    CIRCUIT_ID = "circuit_id"


TYPED_FIELDS: Mapping[MatchField, tuple[str, ...]] = {
    MatchField.PHYSICAL: ("true", "false"),
    MatchField.ADMIN_STATUS: tuple(s.name.lower() for s in IfAdminStatus),
    MatchField.OPER_STATUS: tuple(s.name.lower() for s in IfOperStatus),
}
"""Fields that take fixed values instead of patterns, with their allowed values."""

MULTI_VALUED = frozenset(
    {
        MatchField.DEVICE_TAGS,
        MatchField.DEVICE_GROUPS,
        MatchField.TAGS,
        MatchField.INTERFACE_TAGS,
        MatchField.ADDRESSES,
    }
)
"""Fields holding a list, where an empty list is known to be empty, not unknown."""


def _one(value: str | int | None) -> tuple[str, ...]:
    """Wrap a single fact as a tuple of strings; unknown becomes empty.

    Args:
        value: Fact value, or ``None`` when unknown.

    Returns:
        tuple[str, ...]: Zero or one string.

    Raises:
        None
    """
    return () if value is None else (str(value),)


def _state(value: IfAdminStatus | IfOperStatus | bool | None) -> tuple[str, ...]:
    """Render a typed fact as the lowercase name a rule compares against.

    Args:
        value: Status enum or physical flag, or ``None`` when unknown.

    Returns:
        tuple[str, ...]: ``("up",)``, ``("lower_layer_down",)``, ``("true",)``, or empty.

    Raises:
        None
    """
    if value is None:
        return ()
    if isinstance(value, bool):
        return ("true" if value else "false",)
    return (value.name.lower(),)


_ACCESSORS: Mapping[MatchField, Callable[[InterfaceFacts], tuple[str, ...]]] = {
    MatchField.DEVICE: lambda f: _one(f.device_name),
    MatchField.DEVICE_ADDRESS: lambda f: _one(f.device_address),
    MatchField.SITE: lambda f: _one(f.site),
    MatchField.DEVICE_TAGS: lambda f: f.device_tags,
    MatchField.DEVICE_GROUPS: lambda f: f.device_groups,
    MatchField.IFNAME: lambda f: _one(f.if_name),
    MatchField.IFDESCR: lambda f: _one(f.if_descr),
    MatchField.IFALIAS: lambda f: _one(f.if_alias),
    MatchField.IFTYPE: lambda f: _one(f.if_type),
    MatchField.IFINDEX: lambda f: _one(f.if_index),
    MatchField.ADDRESSES: lambda f: f.addresses,
    MatchField.PHYSICAL: lambda f: _state(f.physical),
    MatchField.ADMIN_STATUS: lambda f: _state(f.admin_status),
    MatchField.OPER_STATUS: lambda f: _state(f.oper_status),
    MatchField.PROVIDER: lambda f: _one(f.circuit.provider),
    MatchField.CIRCUIT_ID: lambda f: _one(f.circuit.circuit_id),
}


def field_values(
    facts: InterfaceFacts, match_field: MatchField, description_tags: tuple[str, ...] = ()
) -> tuple[str, ...]:
    """Return the values of one match field, in a stable order.

    Args:
        facts: The interface.
        match_field: Field to read.
        description_tags: Tags derived from the description (META-012);
            included in ``interface_tags`` and ``tags``.

    Returns:
        tuple[str, ...]: Values to test; empty when the fact is unknown.

    Raises:
        None
    """
    interface_tags = facts.annotations.tags + description_tags
    if match_field is MatchField.INTERFACE_TAGS:
        return interface_tags
    if match_field is MatchField.TAGS:
        return facts.device_tags + interface_tags
    return _ACCESSORS[match_field](facts)
