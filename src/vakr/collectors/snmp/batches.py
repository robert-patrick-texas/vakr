"""Stage 2 batching and capability learning (SNMP-010, SNMP-017).

Interfaces are packed whole into batches under the session's varbind limit,
so each interface's counters share one request and one timestamp. After a
batch answers, ``learn`` spots counters the agent says do not exist, so the
poll plan stops requesting them until the next re-probe.

Dependencies:
    vakr.collectors.snmp: Plans and counter OIDs.
    vakr.ports.snmp: Varbinds and value kinds.

Safety:
    Pure.

Example:
    >>> from vakr.collectors.snmp.plan import InterfacePlan
    >>> [len(c) for c in pack([InterfacePlan(i) for i in (1, 2, 3)], 25)]
    [2, 1]
"""

from collections.abc import Iterable, Mapping, Sequence

from vakr.collectors.snmp.counters import CounterOids, counter_oids
from vakr.collectors.snmp.plan import CapabilityChange, InterfacePlan
from vakr.domain.enums import CounterFamily
from vakr.ports.snmp import Oid, ValueKind, VarBind

_ABSENT = (ValueKind.NO_SUCH_OBJECT, ValueKind.NO_SUCH_INSTANCE)


def pack(entries: Sequence[InterfacePlan], limit: int) -> list[list[InterfacePlan]]:
    """Group whole interfaces into batches of at most ``limit`` varbinds.

    Args:
        entries: Interfaces to sample.
        limit: Varbinds per request; a batch always holds at least one
            interface, which the session splits further if it must.

    Returns:
        list[list[InterfacePlan]]: Batches in plan order.

    Raises:
        None
    """
    batches: list[list[InterfacePlan]] = []
    size = 0
    for entry in entries:
        need = len(counter_oids(entry).all())
        if not batches or size + need > limit:
            batches.append([])
            size = 0
        batches[-1].append(entry)
        size += need
    return batches


def first_batch(entries: Sequence[InterfacePlan], limit: int) -> list[InterfacePlan]:
    """Return the first batch ``pack`` would build, without packing the rest.

    Args:
        entries: Interfaces to sample; at least one.
        limit: Varbinds per request.

    Returns:
        list[InterfacePlan]: The leading interfaces that fit.

    Raises:
        None
    """
    batch, size = [], 0
    for entry in entries:
        need = len(counter_oids(entry).all())
        if batch and size + need > limit:
            break
        batch.append(entry)
        size += need
    return batch


def _absent(got: Mapping[Oid, VarBind], oids: Iterable[Oid]) -> bool:
    """Report whether the agent said every OID does not exist.

    Args:
        got: Varbinds by OID.
        oids: OIDs to check.

    Returns:
        bool: True when each one was answered ``noSuchObject``/``noSuchInstance``.

    Raises:
        None
    """
    found = [got.get(oid) for oid in oids]
    return bool(found) and all(vb is not None and vb.kind in _ABSENT for vb in found)


def learn(
    entry: InterfacePlan, oids: CounterOids, got: Mapping[Oid, VarBind]
) -> list[CapabilityChange]:
    """Detect counters the agent does not have (SNMP-010).

    Args:
        entry: The interface plan.
        oids: The OIDs requested.
        got: Varbinds by OID.

    Returns:
        list[CapabilityChange]: Unsupported capabilities seen this cycle.

    Raises:
        None
    """
    changes = []
    hc = entry.family is CounterFamily.HC64
    if hc and _absent(got, (oids.in_octets, oids.out_octets)):
        changes.append(CapabilityChange(entry.if_index, "hc_octets", False))
    elif hc and _absent(got, (*oids.in_packets, *oids.out_packets)):
        changes.append(CapabilityChange(entry.if_index, "hc_packets", False))
    if oids.discontinuity and _absent(got, (oids.discontinuity,)):
        changes.append(CapabilityChange(entry.if_index, "discontinuity", False))
    return changes
