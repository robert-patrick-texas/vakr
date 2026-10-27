"""The inventory the acceptance reports judge, read once in one unit of work (E.35 bounds).

Collectors, devices (retired ones too, since a device retired during the
window still has gaps to explain), every interface of each device, and
the audited inventory changes since the window started: an audit event
about a device, an ``inventory.apply`` of the whole file, or a discovery
decision. Each read is bounded (``acceptance_limits``) and a bound that
cuts a list short is reported, never silently dropped.

Dependencies:
    vakr.domain, vakr.ports.metadata: Inventory records and filters.
    vakr.query.acceptance_*: Evidence types and bounds.
    vakr.query.snapshot: Bounded paging.

Safety:
    Read-only; credential references are never read into the reports.

Example:
    >>> interface_name(None, "", "", "x")
    'x'
"""

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from uuid import UUID

from vakr.domain.catalog import Collector
from vakr.domain.enums import ReachabilityStrategy
from vakr.domain.interfaces import Interface
from vakr.domain.inventory import Device
from vakr.domain.paging import MAX_PAGE_LIMIT, PageRequest
from vakr.domain.timestamps import to_epoch_us
from vakr.ports.metadata import MetadataUnitOfWork, MetadataUnitOfWorkFactory
from vakr.ports.metadata.filters import DeviceFilter, InterfaceFilter
from vakr.query.acceptance_causes import InterfaceEvidence
from vakr.query.acceptance_evidence import DeviceEvidence
from vakr.query.acceptance_gaps import DeviceInput
from vakr.query.acceptance_limits import MAX_AUDIT_EVENTS, MAX_DEVICES, MAX_INTERFACES_PER_DEVICE
from vakr.query.snapshot import pages

_INVENTORY_WIDE = frozenset({"inventory.apply"})


def interface_name(if_name: str | None, if_descr: str, alias: str, fallback: str) -> str:
    """Name an interface as operators know it.

    Args:
        if_name: ``ifName``.
        if_descr: ``ifDescr``.
        alias: ``ifAlias``, unused unless nothing else names it.
        fallback: The UUID text.

    Returns:
        str: The first non-empty of ``ifName``, ``ifDescr``, ``ifAlias``, ``fallback``.

    Raises:
        None
    """
    return if_name or if_descr or alias or fallback


@dataclass(slots=True)
class AcceptanceInventory:
    """Everything the reports read from metadata.

    Attributes:
        collectors: Every collector.
        devices: Every device, retired ones included.
        interfaces: Device UUID to its interfaces.
        changes: Device UUID to audited change times; key ``None`` holds inventory-wide ones.
        truncated: Lists cut short by a bound, by name.
    """

    collectors: list[Collector] = field(default_factory=list)
    devices: list[Device] = field(default_factory=list)
    interfaces: dict[UUID, list[Interface]] = field(default_factory=dict)
    changes: dict[UUID | None, list[int]] = field(default_factory=lambda: defaultdict(list))
    truncated: list[str] = field(default_factory=list)

    def device_input(self, device: Device, strategy: ReachabilityStrategy) -> DeviceInput:
        """Build the gap scan's view of one device.

        Args:
            device: The device.
            strategy: The configured default reachability strategy.

        Returns:
            DeviceInput: Its evidence and interfaces.

        Raises:
            None
        """
        changes = sorted([*self.changes.get(device.id, []), *self.changes.get(None, [])])
        evidence = DeviceEvidence(to_epoch_us(device.created_at), device.admin_state,
                                  to_epoch_us(device.updated_at), tuple(changes))  # fmt: skip
        ifaces = tuple(
            (i.id, InterfaceEvidence(interface_name(i.if_name, i.if_descr, i.if_alias, str(i.id)),
                                     to_epoch_us(i.created_at), i.lifecycle,
                                     to_epoch_us(i.updated_at)))
            for i in self.interfaces.get(device.id, [])
        )  # fmt: skip
        mode = device.reachability or strategy
        return DeviceInput(device.id, device.name, evidence, ifaces,
                           icmp=mode is not ReachabilityStrategy.SNMP_ONLY,
                           snmp=mode is not ReachabilityStrategy.ICMP_ONLY)  # fmt: skip


def read_inventory(
    uow_factory: MetadataUnitOfWorkFactory, since: datetime, *, audit: bool
) -> AcceptanceInventory:
    """Read collectors, devices, interfaces, and (optionally) audited changes since a time.

    Args:
        uow_factory: Opens a metadata unit of work.
        since: Window start; older audit events are not read.
        audit: Whether to read audit events.

    Returns:
        AcceptanceInventory: The inventory.

    Raises:
        StorageError: If the metadata database is unavailable.
    """
    inv = AcceptanceInventory()
    with uow_factory() as uow:
        inv.collectors, _cut = pages(uow.collectors.list_page, 1_000)
        everything = DeviceFilter(include_retired=True)
        inv.devices, cut = pages(lambda p: uow.devices.list_page(p, everything), MAX_DEVICES)
        if cut:
            inv.truncated.append(f"devices beyond {MAX_DEVICES}")
        for device in inv.devices:
            where = InterfaceFilter(device_id=device.id, include_retired=True)
            found, cut = pages(lambda p, w=where: uow.interfaces.list_page(p, w),
                               MAX_INTERFACES_PER_DEVICE)  # fmt: skip
            inv.interfaces[device.id] = found
            if cut:
                inv.truncated.append(f"interfaces of {device.name} beyond "
                                     f"{MAX_INTERFACES_PER_DEVICE}")  # fmt: skip
        if audit:
            _read_audit(uow, inv, since)
    return inv


def _read_audit(uow: MetadataUnitOfWork, inv: AcceptanceInventory, since: datetime) -> None:
    """Collect audited inventory changes since ``since``, newest first, bounded.

    Args:
        uow: The open unit of work.
        inv: The inventory to fill.
        since: Oldest event time wanted.

    Returns:
        None

    Raises:
        StorageError: If the metadata database is unavailable.
    """
    cursor: str | None = None
    read = 0
    while read < MAX_AUDIT_EVENTS:
        page = uow.audit.list_page(PageRequest(MAX_PAGE_LIMIT, cursor))
        for event in page.items:
            read += 1
            if event.occurred_at < since:
                return
            if event.action in _INVENTORY_WIDE:
                inv.changes[None].append(to_epoch_us(event.occurred_at))
            elif event.object_id is not None:
                inv.changes[event.object_id].append(to_epoch_us(event.occurred_at))
        cursor = page.next_cursor
        if cursor is None:
            return
    inv.truncated.append(f"audit events beyond {MAX_AUDIT_EVENTS}")
