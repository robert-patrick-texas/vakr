"""Causes of a missing ICMP or interface sample in a poll that ran, and runs of missing slots.

A device's poll can run and still leave a subject without data: the ICMP
gate skipped SNMP for an unreachable device, SNMP timed out or answered in
part, the collector failed on its side, or the interface was not sampled
(not discovered yet, identity unresolved, not up/up). ``interface_cause``
and ``icmp_cause`` pick the cause from the poll record, the SNMP device
observation of that poll, and the interface's inventory record. A missing
sample that none of these explains is ``unexplained``: vakr stores no
Stage 1 status for interfaces that are not up/up (E.27 #7), so a link that
went down and came back looks exactly like that. ``runs`` groups
consecutive missing slots with one cause.

Dependencies:
    vakr.domain: SNMP states, lifecycle, quality, observations.
    vakr.query.acceptance_evidence, acceptance_slots.

Safety:
    Pure computation.

Example:
    >>> CAUSE_TEXT[GapCause.UNEXPLAINED].startswith("no recorded evidence")
    True
"""

from collections.abc import Callable, Sequence
from dataclasses import dataclass

from vakr.domain.enums import InterfaceLifecycle, PollExecution, SnmpState
from vakr.domain.observations import SnmpDeviceObservation
from vakr.domain.quality import Quality
from vakr.query.acceptance_evidence import DeviceEvidence
from vakr.query.acceptance_limits import US
from vakr.query.acceptance_slots import GapCause, Slot, SlotKind

CAUSE_TEXT = {
    GapCause.COLLECTOR_STOPPED: "collector stopped or restarting (no health reports)",
    GapCause.COLLECTOR_BUSY: "collector did not attempt the poll (capacity)",
    GapCause.COLLECTOR_FAULT: "collector-side failure (COLLECTOR_DEGRADED)",
    GapCause.STORAGE: "spool, ingest, storage, or replication trouble",
    GapCause.TELEMETRY_LOSS: "telemetry-loss incident",
    GapCause.POLL_OVERRUN: "previous poll still running (MISSED_OVERRUN)",
    GapCause.DEVICE_UNREACHABLE: "device unreachable (ICMP gate skipped SNMP, or no answer)",
    GapCause.SNMP_TIMEOUT: "SNMP timeout",
    GapCause.SNMP_ERROR: "SNMP error or partial answer",
    GapCause.ADMIN_STATE: "device disabled, retired, or in maintenance",
    GapCause.NOT_YET_DISCOVERED: "interface not yet discovered, or identity unresolved",
    GapCause.INTERFACE_NOT_UP: "interface not up/up (lifecycle not active)",
    GapCause.INTERFACE_DOWN: "interface not up/up at Stage 1 (status record)",
    GapCause.INVENTORY_CHANGE: "inventory change (device added, removed, or edited)",
    GapCause.UNEXPLAINED: "no recorded evidence explains the gap",
}
"""Operator text of each cause (E.35)."""

_SNMP_CAUSE = {
    SnmpState.SKIPPED: GapCause.DEVICE_UNREACHABLE,
    SnmpState.TIMEOUT: GapCause.SNMP_TIMEOUT,
    SnmpState.ERROR: GapCause.SNMP_ERROR,
    SnmpState.PARTIAL: GapCause.SNMP_ERROR,
}


@dataclass(frozen=True, slots=True)
class InterfaceEvidence:
    """What the inventory says about one interface.

    Attributes:
        name: ``ifName``, else ``ifDescr``, else the UUID.
        created_us: When its identity was created (discovered).
        lifecycle: Its lifecycle now.
        updated_us: When the record last changed.
    """

    name: str
    created_us: int
    lifecycle: InterfaceLifecycle
    updated_us: int


@dataclass(frozen=True, slots=True)
class Run:
    """Consecutive missing slots of one subject with one cause.

    Attributes:
        first: Index of the first slot.
        last: Index of the last slot.
        slots: Missing slots in the run.
        cause: The cause.
    """

    first: int
    last: int
    slots: int
    cause: GapCause


def icmp_cause(slot: Slot) -> GapCause:
    """Explain a poll that ran without storing an ICMP result.

    Args:
        slot: The slot (its poll ran).

    Returns:
        GapCause: The cause.

    Raises:
        None
    """
    if slot.kind is SlotKind.CANCELLED:
        return GapCause.COLLECTOR_STOPPED
    flags = slot.mark.quality if slot.mark is not None else Quality.NONE
    return GapCause.COLLECTOR_FAULT if flags & Quality.COLLECTOR_DEGRADED else GapCause.UNEXPLAINED


def _sampled_cause(slot: Slot, iface: InterfaceEvidence, down: bool) -> GapCause:
    """Explain an interface left out of a poll whose SNMP device read answered.

    Args:
        slot: The slot.
        iface: The interface's inventory evidence.
        down: A status record says the interface was not up/up at this poll.

    Returns:
        GapCause: The cause.

    Raises:
        None
    """
    mark = slot.mark
    flags = mark.quality if mark is not None else Quality.NONE
    if mark is not None and mark.execution is PollExecution.PARTIAL:
        cause = GapCause.SNMP_ERROR  # Stage 2 did not finish
    elif flags & Quality.BASELINE_IDENTITY_UNRESOLVED or iface.created_us > slot.at_us:
        cause = GapCause.NOT_YET_DISCOVERED
    elif down:
        cause = GapCause.INTERFACE_DOWN
    elif flags & Quality.MAINTENANCE:
        cause = GapCause.ADMIN_STATE
    elif iface.lifecycle is not InterfaceLifecycle.ACTIVE and iface.updated_us <= slot.at_us:
        cause = GapCause.INTERFACE_NOT_UP
    else:
        cause = GapCause.UNEXPLAINED
    return cause


def interface_cause(
    slot: Slot, snmp: SnmpDeviceObservation | None, iface: InterfaceEvidence, down: bool = False
) -> GapCause:
    """Explain a poll that ran without sampling an interface.

    Args:
        slot: The slot (its poll ran).
        snmp: The poll's SNMP device observation, if stored.
        iface: The interface's inventory evidence.
        down: A status record says it was not up/up at this poll (E.35).

    Returns:
        GapCause: The cause.

    Raises:
        None
    """
    if slot.kind is SlotKind.CANCELLED:
        return GapCause.COLLECTOR_STOPPED
    if iface.created_us > slot.at_us:
        return GapCause.NOT_YET_DISCOVERED
    if snmp is None:
        partial = slot.mark is not None and slot.mark.execution is PollExecution.PARTIAL
        return GapCause.SNMP_ERROR if partial else GapCause.UNEXPLAINED
    if snmp.state in _SNMP_CAUSE:
        return _SNMP_CAUSE[snmp.state]
    if snmp.state is SnmpState.UNKNOWN:
        degraded = snmp.quality & Quality.COLLECTOR_DEGRADED
        return GapCause.COLLECTOR_FAULT if degraded else GapCause.UNEXPLAINED
    return _sampled_cause(slot, iface, down)


def runs(indices: Sequence[int], cause_of: Callable[[int], GapCause]) -> list[Run]:
    """Group missing slot indices into runs of consecutive slots with one cause.

    Args:
        indices: Missing slot indices, ascending.
        cause_of: The cause of one slot.

    Returns:
        list[Run]: The runs, oldest first.

    Raises:
        None
    """
    out: list[Run] = []
    for i in indices:
        cause = cause_of(i)
        last = out[-1] if out else None
        if last is not None and last.last == i - 1 and last.cause is cause:
            out[-1] = Run(last.first, i, last.slots + 1, cause)
        else:
            out.append(Run(i, i, 1, cause))
    return out


def refine(run: Run, slots: Sequence[Slot], device: DeviceEvidence) -> GapCause:
    """Explain an unexplained run by an audited inventory change at its edges.

    Args:
        run: The run.
        slots: The device's timeline.
        device: The device's inventory evidence.

    Returns:
        GapCause: ``inventory_change`` when a change falls within two intervals of the run.

    Raises:
        None
    """
    if run.cause is not GapCause.UNEXPLAINED:
        return run.cause
    first, last = slots[run.first], slots[run.last]
    margin = 2 * max(first.interval_s, last.interval_s) * US
    found = device.changed_near(first.at_us - margin, last.at_us + margin)
    return GapCause.UNEXPLAINED if found is None else GapCause.INVENTORY_CHANGE
