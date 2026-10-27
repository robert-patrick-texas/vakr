"""What ``vakr acceptance export`` reads: devices, interfaces, and their short keys.

The targets file (``vakr.query.acceptance_targets``) names devices by name
or address and interfaces by ``ifIndex``, name, description, address, or
MAC. This module matches it against the inventory and gives every device
and interface a short key (``d0001``, ``i00001``) that each exported row
carries, so the rows stay small. Targets that match nothing are reported
by line number, so the comparison can say which MRTG or SmokePing targets
vakr does not monitor.

Dependencies:
    vakr.domain: Collectors, devices, interfaces.
    vakr.query.acceptance_targets: Matching rules.

Safety:
    Pure functions over records already read; no I/O.

Example:
    >>> resolve([], {}, None)
    ((), ())
"""

from collections.abc import Sequence
from dataclasses import dataclass
from uuid import UUID

from vakr.domain.catalog import Collector
from vakr.domain.interfaces import Interface
from vakr.domain.inventory import Device
from vakr.query.acceptance_targets import TargetSpec, device_matches, interface_matches


@dataclass(frozen=True, slots=True)
class PlannedInterface:
    """One interface the export reads.

    Attributes:
        key: Short key used by every row of this interface, such as ``i0001``.
        interface: The interface as the inventory has it now.
        lines: Targets-file lines that selected it (empty without a file).
    """

    key: str
    interface: Interface
    lines: tuple[int, ...] = ()


@dataclass(frozen=True, slots=True)
class PlannedDevice:
    """One device the export reads.

    Attributes:
        key: Short key, such as ``d0001``.
        device: The device.
        interfaces: Interfaces to read samples of.
        icmp: Whether to read its ICMP cycles.
    """

    key: str
    device: Device
    interfaces: tuple[PlannedInterface, ...]
    icmp: bool


@dataclass(frozen=True, slots=True)
class ExportPlan:
    """What one export reads.

    Attributes:
        collector: The position whose observations are read (the vantage point).
        collectors: Every known position, for the summary.
        devices: Devices in name order.
        unmatched: ``(line, reason)`` for targets that matched nothing.
        truncated: True when an inventory cap left devices or interfaces out.
    """

    collector: Collector
    collectors: tuple[Collector, ...]
    devices: tuple[PlannedDevice, ...]
    unmatched: tuple[tuple[int, str], ...]
    truncated: bool


def wanted(device: Device, targets: Sequence[TargetSpec] | None) -> bool:
    """Report whether the export reads a device at all.

    Args:
        device: The device.
        targets: The targets, or ``None`` for everything.

    Returns:
        bool: True without targets, or when a target names the device.

    Raises:
        None
    """
    return targets is None or any(device_matches(t, device) for t in targets)


def resolve(
    devices: Sequence[Device],
    listed: dict[UUID, list[Interface]],
    targets: Sequence[TargetSpec] | None,
) -> tuple[tuple[PlannedDevice, ...], tuple[tuple[int, str], ...]]:
    """Match targets to devices and interfaces, and give each a short key.

    Args:
        devices: Candidate devices in name order.
        listed: Each device's interfaces.
        targets: The targets, or ``None`` for everything.

    Returns:
        tuple: Planned devices, and ``(line, reason)`` for unmatched targets.

    Raises:
        None
    """
    planned: list[PlannedDevice] = []
    hits: set[int] = set()
    count = 0
    for number, device in enumerate(devices, start=1):
        mine = [t for t in targets if device_matches(t, device)] if targets is not None else None
        chosen: list[PlannedInterface] = []
        for iface in sorted(listed.get(device.id, []), key=lambda i: (i.if_name, str(i.id))):
            lines = tuple(t.line for t in mine or () if t.kind == "interface"
                          and interface_matches(t, iface))  # fmt: skip
            if mine is None or lines:
                count += 1
                chosen.append(PlannedInterface(f"i{count:05d}", iface, lines))
                hits.update(lines)
        icmp = mine is None or any(t.kind == "icmp" for t in mine)
        hits.update(t.line for t in mine or () if t.kind == "icmp")
        planned.append(PlannedDevice(f"d{number:04d}", device, tuple(chosen), icmp))
    known = {t.line for t in targets or () if any(device_matches(t, d) for d in devices)}
    unmatched = tuple(
        (t.line, "no interface matches" if t.line in known else "no device matches")
        for t in targets or ()
        if t.line not in hits
    )
    return tuple(p for p in planned if p.interfaces or p.icmp), unmatched
