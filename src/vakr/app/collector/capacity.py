"""Capacity inputs for rate checks, read from the interface records (RATE-005, RATE-008..010).

Rates themselves need no capacity, but two baseline rules do: a 32-bit
counter wrap is accepted only when the interface's line rate makes it
physically possible (RATE-005, E.17 #8), and a rate above 1.10 times the
effective capacity is flagged ``RATE_EXCEEDS_CAPACITY`` (RATE-009). The
interface records in the metadata database carry ``ifSpeed``,
``ifHighSpeed``, and the operator's capacity override and contracted rate,
so the collector reads them per device when its plan changes. A device
whose records are missing gets ``NO_CAPACITY``: absolute rates, flagged
``CAPACITY_UNKNOWN``, and no 32-bit wrap accepted (RATE-010).

Dependencies:
    vakr.ports.metadata: The interface repository, through a unit of work.
    vakr.logic.capacity: ``CapacityInputs``.

Safety:
    Read-only; blocking, so call it from a worker thread. Bounded by
    ``MAX_PAGES`` pages of ``MAX_PAGE_LIMIT`` interfaces.

Example:
    >>> MAX_PAGES * MAX_PAGE_LIMIT
    10000
"""

from vakr.domain.ids import DeviceId, InterfaceId
from vakr.domain.paging import MAX_PAGE_LIMIT, PageRequest
from vakr.logic.capacity import CapacityInputs, reports_link_capacity
from vakr.ports.metadata import InterfaceFilter, MetadataUnitOfWorkFactory

MAX_PAGES = 20
"""Most pages read per device: 10,000 interfaces, far above any IOS XE chassis."""


def load_capacity(
    uow_factory: MetadataUnitOfWorkFactory, device_id: DeviceId
) -> dict[InterfaceId, CapacityInputs]:
    """Read a device's interface capacities.

    Args:
        uow_factory: Units of work on the metadata database.
        device_id: The device.

    Returns:
        dict[InterfaceId, CapacityInputs]: Inputs by interface; empty when
        the device has no interface records yet.

    Raises:
        StorageError: If the metadata database is unavailable.
    """
    found: dict[InterfaceId, CapacityInputs] = {}
    where = InterfaceFilter(device_id=device_id)
    cursor: str | None = None
    with uow_factory() as uow:
        for _ in range(MAX_PAGES):
            page = uow.interfaces.list_page(PageRequest(MAX_PAGE_LIMIT, cursor), where)
            for record in page.items:
                note = record.annotation
                found[record.id] = CapacityInputs(
                    override_bps=note.capacity_override_bps,
                    contracted_bps=note.contracted_bps,
                    if_high_speed_mbps=record.if_high_speed,
                    if_speed_bps=record.if_speed,
                    link_speed=reports_link_capacity(record.if_type, record.if_name),
                )
            cursor = page.next_cursor
            if cursor is None:
                break
    return found
