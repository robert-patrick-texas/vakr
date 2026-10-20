"""Stage 2 counter OIDs and the raw interface sample built from their values.

For each up/up interface Stage 2 reads (9.6):

1. octets: ``ifHCInOctets``/``ifHCOutOctets`` (64-bit), or ``ifInOctets``/
   ``ifOutOctets`` (32-bit) where HC counters do not exist (SNMP-007);
2. packets: unicast, multicast, and broadcast components of the same
   width, summed into one inbound and one outbound total. The components
   themselves are not kept (SNMP-008, ADR-006);
3. ``ifInErrors``/``ifOutErrors``, which IF-MIB defines only as 32-bit;
4. ``ifCounterDiscontinuityTime`` where supported (SNMP-009).

A value must have the SNMP type its object defines. A ``Counter32`` where a
``Counter64`` belongs would wrap 2^32 times sooner than the rate math
assumes, so it is treated as missing. A total is missing when any component
is, because a partial sum would understate traffic. Missing is ``None``,
never zero (blueprint 4.3).

Dependencies:
    vakr.collectors.snmp.plan: Interface plans.
    vakr.discovery.mib: The OIDs.
    vakr.logic.counters: ``CounterSample`` for rate derivation.

Safety:
    Pure.

Example:
    >>> from vakr.collectors.snmp.plan import InterfacePlan
    >>> len(counter_oids(InterfacePlan(3)).all())
    11
"""

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime

from vakr.collectors.snmp.plan import InterfacePlan
from vakr.discovery.mib import IF_COLUMNS, IFX_COLUMNS, instance
from vakr.domain.enums import CounterFamily, IfAdminStatus, IfOperStatus
from vakr.domain.ids import InterfaceId
from vakr.domain.quality import Quality
from vakr.logic.counters import CounterSample
from vakr.ports.snmp import Oid, ValueKind, VarBind

_COLUMNS = {**IF_COLUMNS, **IFX_COLUMNS}
_OCTETS = {
    CounterFamily.HC64: ("ifHCInOctets", "ifHCOutOctets"),
    CounterFamily.LEGACY32: ("ifInOctets", "ifOutOctets"),
}
_PACKETS = {
    CounterFamily.HC64: (
        ("ifHCInUcastPkts", "ifHCInMulticastPkts", "ifHCInBroadcastPkts"),
        ("ifHCOutUcastPkts", "ifHCOutMulticastPkts", "ifHCOutBroadcastPkts"),
    ),
    CounterFamily.LEGACY32: (
        ("ifInUcastPkts", "ifInMulticastPkts", "ifInBroadcastPkts"),
        ("ifOutUcastPkts", "ifOutMulticastPkts", "ifOutBroadcastPkts"),
    ),
}
_WIDTH = {CounterFamily.HC64: ValueKind.COUNTER64, CounterFamily.LEGACY32: ValueKind.COUNTER32}
DISCONTINUITY = IFX_COLUMNS["ifCounterDiscontinuityTime"]


@dataclass(frozen=True, slots=True)
class CounterOids:
    """The Stage 2 OIDs of one interface for one counter family.

    Attributes:
        family: The counter family.
        in_octets: Inbound octet counter.
        out_octets: Outbound octet counter.
        in_packets: Inbound unicast, multicast, broadcast components.
        out_packets: Outbound components.
        in_errors: ``ifInErrors``.
        out_errors: ``ifOutErrors``.
        discontinuity: ``ifCounterDiscontinuityTime``, if requested.
    """

    family: CounterFamily
    in_octets: Oid
    out_octets: Oid
    in_packets: tuple[Oid, ...]
    out_packets: tuple[Oid, ...]
    in_errors: Oid
    out_errors: Oid
    discontinuity: Oid | None

    def all(self) -> list[Oid]:
        """List every OID, in request order.

        Args:
            None

        Returns:
            list[Oid]: Octets, packet components, errors, then discontinuity.

        Raises:
            None
        """
        oids = [self.in_octets, self.out_octets, *self.in_packets, *self.out_packets]
        oids += [self.in_errors, self.out_errors]
        return [*oids, self.discontinuity] if self.discontinuity else oids


def counter_oids(entry: InterfacePlan) -> CounterOids:
    """Build an interface's Stage 2 OIDs from its plan.

    HC packet components are requested only when the plan says they exist;
    otherwise packet totals are left unavailable rather than mixing widths.

    Args:
        entry: The interface plan.

    Returns:
        CounterOids: The OIDs to request.

    Raises:
        None
    """
    i = entry.if_index
    octets, (inbound, outbound) = _OCTETS[entry.family], _PACKETS[entry.family]
    packets = entry.family is CounterFamily.LEGACY32 or entry.hc_packets
    in_pk = tuple(instance(_COLUMNS[n], i) for n in inbound) if packets else ()
    out_pk = tuple(instance(_COLUMNS[n], i) for n in outbound) if packets else ()
    return CounterOids(
        entry.family,
        instance(_COLUMNS[octets[0]], i),
        instance(_COLUMNS[octets[1]], i),
        in_pk,
        out_pk,
        instance(IF_COLUMNS["ifInErrors"], i),
        instance(IF_COLUMNS["ifOutErrors"], i),
        instance(DISCONTINUITY, i) if entry.discontinuity else None,
    )


@dataclass(frozen=True, slots=True)
class InterfaceSample:
    """The raw counters of one interface from one poll, before rates (10.3).

    Attributes:
        if_index: ifIndex the counters were read from.
        interface_id: Persistent UUID from the plan, once reconciled.
        sampled_at: When the request carrying them was sent (UTC).
        sampled_monotonic: The same moment on the monotonic clock.
        family: Counter width of the octet and packet values.
        admin_status: From Stage 1.
        oper_status: From Stage 1.
        in_octets: Inbound octets, or ``None``.
        out_octets: Outbound octets, or ``None``.
        in_packets: Sum of the three inbound components, or ``None``.
        out_packets: Sum of the outbound components, or ``None``.
        in_errors: ``ifInErrors``, or ``None``.
        out_errors: ``ifOutErrors``, or ``None``.
        discontinuity_ticks: ``ifCounterDiscontinuityTime``, or ``None``.
        missing: Fields that did not arrive.
        quality: ``MISSING_FIELDS`` and/or ``PARTIAL_RESPONSE``.
    """

    if_index: int
    interface_id: InterfaceId | None
    sampled_at: datetime
    sampled_monotonic: float
    family: CounterFamily
    admin_status: IfAdminStatus | None
    oper_status: IfOperStatus | None
    in_octets: int | None = None
    out_octets: int | None = None
    in_packets: int | None = None
    out_packets: int | None = None
    in_errors: int | None = None
    out_errors: int | None = None
    discontinuity_ticks: int | None = None
    missing: tuple[str, ...] = ()
    quality: Quality = Quality.NONE

    def counter_sample(self, identity_resolved: bool = True) -> CounterSample:
        """Convert to the ``vakr.logic`` sample that rate derivation takes.

        Args:
            identity_resolved: False while the persistent identity awaits
                review, which invalidates the baseline (IFID-004).

        Returns:
            CounterSample: The counters and evidence.

        Raises:
            None
        """
        return CounterSample(
            self.family, self.in_octets, self.out_octets, self.in_packets, self.out_packets,
            self.in_errors, self.out_errors, self.discontinuity_ticks, identity_resolved,
        )  # fmt: skip


def _value(got: Mapping[Oid, VarBind], oid: Oid, kind: ValueKind) -> int | None:
    """Read one counter, refusing values of the wrong SNMP type.

    Args:
        got: Varbinds by OID.
        oid: The counter's OID.
        kind: The type IF-MIB defines for it.

    Returns:
        int | None: The value, or ``None``.

    Raises:
        None
    """
    vb = got.get(oid)
    return vb.integer() if vb is not None and vb.kind is kind else None


def _total(got: Mapping[Oid, VarBind], oids: tuple[Oid, ...], kind: ValueKind) -> int | None:
    """Sum packet components; any missing component makes the total missing.

    Args:
        got: Varbinds by OID.
        oids: The three components.
        kind: Their SNMP type.

    Returns:
        int | None: The aggregate, or ``None``.

    Raises:
        None
    """
    values = [_value(got, oid, kind) for oid in oids]
    if not values or any(value is None for value in values):
        return None
    return sum(value for value in values if value is not None)


def counter_values(oids: CounterOids, got: Mapping[Oid, VarBind]) -> dict[str, int | None]:
    """Read every Stage 2 value of one interface.

    Args:
        oids: The interface's OIDs.
        got: Varbinds by OID.

    Returns:
        dict[str, int | None]: The six counters and ``discontinuity_ticks``.

    Raises:
        None
    """
    width = _WIDTH[oids.family]
    found: dict[str, int | None] = {
        "in_octets": _value(got, oids.in_octets, width),
        "out_octets": _value(got, oids.out_octets, width),
        "in_packets": _total(got, oids.in_packets, width),
        "out_packets": _total(got, oids.out_packets, width),
        "in_errors": _value(got, oids.in_errors, ValueKind.COUNTER32),
        "out_errors": _value(got, oids.out_errors, ValueKind.COUNTER32),
    }
    disc = oids.discontinuity
    found["discontinuity_ticks"] = _value(got, disc, ValueKind.TIMETICKS) if disc else None
    return found
