"""Requests, order, and current values of cross-device interface lists (12.5, E.29 #6, API-005).

``InterfaceListRequest`` bounds a list request: offset paging, 1 to 500
rows, search text of at most 256 characters (SEC-019). ``ordered`` sorts
the interfaces in scope and pages them; for the metadata-only orders it
reads current values for the page alone, for the traffic and utilization
orders it reads them for every interface first.
``list_rows`` reads one position's current samples device by device and
builds rows with ``interface_rows.interface_row``, so the global list says
exactly what the device screen says about an interface.

Dependencies:
    vakr.query.detail, interface_rows, catalog_scope: State reads, rows, refs.
    vakr.query.history_limits: The search-text bound (E.29).

Safety:
    Bounded per device (E.29); a telemetry problem leaves every value "—"
    with the reason and is reported once in the view's ``telemetry``.

Example:
    >>> InterfaceSort.PRIORITY.value, InterfaceSort.UTILIZATION.value
    ('priority', 'utilization')
"""

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum

from vakr.domain.catalog import Collector
from vakr.domain.checks import require_int
from vakr.domain.errors import ValidationError
from vakr.domain.ids import CollectorId, InterfaceId
from vakr.domain.interfaces import Interface
from vakr.domain.paging import DEFAULT_PAGE_LIMIT, MAX_PAGE_LIMIT
from vakr.domain.state import CurrentInterfaceState
from vakr.logic.natural import natural_key
from vakr.ports.telemetry import TelemetryBackend
from vakr.query.catalog_index import CatalogIndex
from vakr.query.catalog_scope import InterfaceScope, interface_ref, ref_key
from vakr.query.catalog_views import InterfaceListRow
from vakr.query.detail import read_interface_states
from vakr.query.history_limits import SEARCH_TEXT_MAX
from vakr.query.interface_rows import display_name, interface_row, order_key
from vakr.query.settings import QuerySettings
from vakr.query.views import Reading

OK = Reading("ok", "ok")

type RowReader = Callable[
    [CatalogIndex, Sequence[Interface]], tuple[list[InterfaceListRow], Reading]
]


class InterfaceSort(StrEnum):
    """Orders of the global interface list."""

    PRIORITY = "priority"
    NAME = "name"
    TRAFFIC = "traffic"
    UTILIZATION = "utilization"


def list_rows(
    telemetry: Callable[[], TelemetryBackend] | None,
    collector: Collector | None,
    now: datetime,
    settings: QuerySettings,
) -> RowReader:
    """Return a reader of list rows with one position's current values.

    Args:
        telemetry: Opens a telemetry view, or ``None``.
        collector: The position, or ``None`` when none exists.
        now: Reference time (UTC).
        settings: Bounds.

    Returns:
        RowReader: Builds rows for given interfaces, plus the telemetry reading.

    Raises:
        None
    """

    def read(
        index: CatalogIndex, interfaces: Sequence[Interface]
    ) -> tuple[list[InterfaceListRow], Reading]:
        """Read current values for the interfaces' devices and build rows.

        Args:
            index: The inventory.
            interfaces: The interfaces, in output order.

        Returns:
            tuple: Rows in the same order, and ``ok`` or why values are missing.

        Raises:
            None
        """
        states: dict[InterfaceId, CurrentInterfaceState] = {}
        problem = None if collector else "no collector has been registered yet"
        for device_id in dict.fromkeys(i.device_id for i in interfaces):
            if problem or collector is None:
                break
            found, problem = read_interface_states(
                telemetry, device_id, collector.id, now, settings.limits.max_device_interfaces
            )
            states.update(found)
        position = collector.position if collector else "any collector"
        rows = [
            InterfaceListRow(
                interface_ref(index, i),
                interface_row(i, states.get(i.id), position),
                i.annotation.contracted_bps,
                i.addresses,
            )
            for i in interfaces
        ]
        return rows, OK if problem is None else Reading(
            f"unavailable: {problem}", "unavailable", problem
        )

    return read


def _priority(index: CatalogIndex, iface: Interface) -> tuple:
    """Sort key of the priority order: markers and rank, then device, name, UUID.

    Args:
        index: The inventory.
        iface: The interface.

    Returns:
        tuple: Smaller sorts first.

    Raises:
        None
    """
    device = index.devices.get(iface.device_id)
    flags = order_key(iface)[:4]
    return (
        *flags,
        natural_key(device.name if device else ""),
        natural_key(display_name(iface)),
        str(iface.id),
    )


def _largest(row: InterfaceListRow, sort: InterfaceSort) -> tuple:
    """Sort key of the measured orders: the higher direction, largest first, missing last.

    Args:
        row: A row with current values.
        sort: ``traffic`` or ``utilization``.

    Returns:
        tuple: Smaller sorts first.

    Raises:
        None
    """
    c = row.current
    pair = (c.in_rate, c.out_rate) if sort is InterfaceSort.TRAFFIC else (c.in_util, c.out_util)
    values = [float(r.value) for r in pair if isinstance(r.value, int | float)]
    top = max(values) if values else None
    return top is None, -(top or 0.0), ref_key(row.ref)


def ordered(
    index: CatalogIndex,
    chosen: Sequence[Interface],
    sort: InterfaceSort,
    offset: int,
    limit: int,
    read: RowReader,
) -> tuple[tuple[InterfaceListRow, ...], Reading]:
    """Sort, page, and fill in current values.

    Args:
        index: The inventory.
        chosen: Interfaces in scope.
        sort: The order.
        offset: First row.
        limit: Page size.
        read: Builds rows with current values.

    Returns:
        tuple: The page's rows, and the telemetry reading.

    Raises:
        None
    """
    if sort in (InterfaceSort.PRIORITY, InterfaceSort.NAME):
        if sort is InterfaceSort.PRIORITY:
            page = sorted(chosen, key=lambda i: _priority(index, i))[offset : offset + limit]
        else:
            page = sorted(chosen, key=lambda i: ref_key(interface_ref(index, i)))
            page = page[offset : offset + limit]
        rows, telemetry = read(index, page)
        return tuple(rows), telemetry
    rows, telemetry = read(index, chosen)
    rows.sort(key=lambda r: _largest(r, sort))
    return tuple(rows[offset : offset + limit]), telemetry


@dataclass(frozen=True, slots=True)
class InterfaceListRequest:
    """What part of the global interface list to show (12.5, API-005).

    Attributes:
        search: Case-insensitive substring of device, interface, alias,
            description, provider, circuit, or site; empty matches all.
        scope: Filters (device, site, tag, group, provider, WAN, interest, lifecycle).
        sort: Order.
        offset: Index of the first row.
        limit: Rows per page, 1 to 500.
        collector_id: Position whose current values to show.
    """

    search: str = ""
    scope: InterfaceScope = field(default_factory=InterfaceScope)
    sort: InterfaceSort = InterfaceSort.PRIORITY
    offset: int = 0
    limit: int = DEFAULT_PAGE_LIMIT
    collector_id: CollectorId | None = None

    def __post_init__(self) -> None:
        """Validate the request.

        Args:
            None

        Returns:
            None

        Raises:
            ValidationError: If paging, sort, or search text is out of bounds.
        """
        require_int(self.offset, "offset", minimum=0)
        require_int(self.limit, "limit", minimum=1, maximum=MAX_PAGE_LIMIT)
        check_text(self.search)
        if not isinstance(self.sort, InterfaceSort):
            raise ValidationError("unknown sort", field="sort")


def check_text(text: str) -> None:
    """Refuse search text longer than the E.29 bound (SEC-019).

    Args:
        text: The text.

    Returns:
        None

    Raises:
        ValidationError: If it is longer than 256 characters or not text.
    """
    if not isinstance(text, str) or len(text) > SEARCH_TEXT_MAX:
        raise ValidationError(
            f"search text is limited to {SEARCH_TEXT_MAX} characters", field="search"
        )


def matches_text(index: CatalogIndex, iface: Interface, needle: str) -> bool:
    """Tell whether an interface matches list search text.

    Args:
        index: The inventory.
        iface: The interface.
        needle: Case-folded text; empty matches everything.

    Returns:
        bool: True on a match.

    Raises:
        None
    """
    if not needle:
        return True
    device = index.devices.get(iface.device_id)
    site = index.site_of(iface)
    a = iface.annotation
    texts = (
        device.name if device else "",
        iface.if_name,
        iface.if_alias,
        iface.if_descr,
        a.provider or "",
        a.circuit_id or "",
        site.name if site else "",
    )
    return any(needle in t.casefold() for t in texts)
