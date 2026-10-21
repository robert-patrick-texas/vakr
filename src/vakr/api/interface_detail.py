"""One interface's identity, metadata, and current values, for ``GET /interfaces/{uuid}`` (12.5).

The query services list interfaces; none answers for a single one. This
reads the interface record through the metadata port and finds its list
row (current rates, utilization, age from one position) through
``CatalogQueries.interface_list``, narrowed by the interface's own name so
the search touches a handful of rows, at most ``MAX_SCAN`` (E.29: the
largest device has 2,000 interfaces).

Dependencies:
    vakr.query: The catalog service and its request types.
    vakr.ports.metadata: The interface record.

Safety:
    Blocking: call from a worker thread. Read-only and bounded.

Example:
    >>> MAX_SCAN
    2000
"""

from vakr.api.context import ApiContext
from vakr.api.schemas import InterfaceDetail, InterfaceOut
from vakr.domain.ids import CollectorId, InterfaceId
from vakr.domain.interfaces import Interface
from vakr.domain.paging import MAX_PAGE_LIMIT
from vakr.query import InterfaceListRequest, InterfaceScope, InterfaceSort
from vakr.query.catalog_views import InterfaceListRow, InterfaceListView
from vakr.query.history_limits import SEARCH_TEXT_MAX

MAX_SCAN = 2000
"""Rows read at most to find one interface's current values (E.29)."""


def interface_out(iface: Interface) -> InterfaceOut:
    """Copy an interface record into its API shape (allowlist).

    Args:
        iface: The record.

    Returns:
        InterfaceOut: The shape.

    Raises:
        None
    """
    a = iface.annotation
    return InterfaceOut(
        id=iface.id, device_id=iface.device_id, if_index=iface.if_index, if_name=iface.if_name,
        if_descr=iface.if_descr, if_alias=iface.if_alias, if_type=iface.if_type, mac=iface.mac,
        if_speed=iface.if_speed, if_high_speed=iface.if_high_speed, addresses=iface.addresses,
        lifecycle=iface.lifecycle.value, managed_by=iface.managed_by.kind.value,
        interest=a.interest.value, sticky=a.sticky, is_wan_circuit=a.is_wan_circuit,
        provider=a.provider, circuit_id=a.circuit_id, contracted_bps=a.contracted_bps,
        capacity_override_bps=a.capacity_override_bps, display_rank=a.display_rank,
        comment=iface.comment, created_at=iface.created_at, updated_at=iface.updated_at,
    )  # fmt: skip


def _find_row(
    context: ApiContext, iface: Interface, collector: CollectorId | None
) -> tuple[InterfaceListRow | None, InterfaceListView]:
    """Page through the interface's device, narrowed by its name, until its row appears.

    Args:
        context: The services.
        iface: The interface.
        collector: Position whose values to show.

    Returns:
        tuple: The row (``None`` when the index does not hold it) and the last view read.

    Raises:
        StorageError: If the metadata store cannot be read.
    """
    needle = (iface.if_name or iface.if_descr)[:SEARCH_TEXT_MAX]
    scope = InterfaceScope(device_id=iface.device_id, include_retired=True)
    offset = 0
    while True:
        request = InterfaceListRequest(needle, scope, InterfaceSort.NAME, offset,
                                       MAX_PAGE_LIMIT, collector)  # fmt: skip
        view = context.overview.catalog.interface_list(request)
        row = next((r for r in view.rows if r.ref.interface_id == iface.id), None)
        if row is not None or view.next_offset is None or view.next_offset >= MAX_SCAN:
            return row, view
        offset = view.next_offset


def interface_detail(
    context: ApiContext, interface_id: InterfaceId, collector: CollectorId | None
) -> InterfaceDetail | None:
    """Return one interface's detail (blocking).

    Args:
        context: The services.
        interface_id: The interface.
        collector: Position whose current values to show; the default position when ``None``.

    Returns:
        InterfaceDetail | None: ``None`` for an unknown interface.

    Raises:
        StorageError: If the metadata store cannot be read.
    """
    with context.metadata() as uow:
        iface = uow.interfaces.get(interface_id)
    if iface is None:
        return None
    row, view = _find_row(context, iface, collector)
    return InterfaceDetail(interface_out(iface), row, view.position, view.telemetry,
                           view.generated_at)  # fmt: skip
