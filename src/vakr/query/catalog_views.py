"""View models for the global interface list, global search, and interface references (12.1, 12.5).

``InterfaceRef`` is how every M4 list names an interface: its UUID (never
``ifIndex``, invariant 6), its device, the names an operator searches by,
its site and circuit metadata, and the ``sticky``/``WAN``/``primary``
markers that order lists (E.29 #6, UI-017). Search hits carry the UUIDs a
client needs to open the device or the interface's full history directly
(UI-017: within two interactions of global search).

Dependencies:
    vakr.query.detail_views, views: The interface-table row and readings.

Safety:
    No credential reference or secret is ever copied into a view (TUI-010).

Example:
    >>> SearchKind.CIRCUIT.value
    'circuit'
"""

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from vakr.domain.ids import CollectorId, DeviceId, InterfaceId
from vakr.query.detail_views import InterfaceRow
from vakr.query.views import Reading


@dataclass(frozen=True, slots=True)
class InterfaceRef:
    """An interface as lists and rankings name it.

    Attributes:
        interface_id: Persistent interface UUID.
        device_id: Its device.
        device: The device's display name.
        name: ``ifName`` (else ``ifDescr``).
        alias: ``ifAlias``.
        description: ``ifDescr``.
        site: Site name (the interface's override, else the device's), if any.
        provider: Circuit provider, if annotated.
        circuit_id: Carrier circuit ID, if annotated.
        markers: ``sticky``, ``WAN``, ``primary`` as they apply.
        lifecycle: Inventory lifecycle state.
    """

    interface_id: InterfaceId
    device_id: DeviceId
    device: str
    name: str
    alias: str
    description: str
    site: str | None
    provider: str | None
    circuit_id: str | None
    markers: tuple[str, ...]
    lifecycle: str


@dataclass(frozen=True, slots=True)
class InterfaceListRow:
    """One line of the global interface list (12.5).

    Attributes:
        ref: The interface.
        current: Current values from the shown position (rates, utilization, age).
        contracted_bps: Contracted rate, if annotated.
        addresses: IP interface addresses.
    """

    ref: InterfaceRef
    current: InterfaceRow
    contracted_bps: int | None
    addresses: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class InterfaceListView:
    """One page of the global interface list (API-005).

    Attributes:
        rows: Interfaces on this page, in the requested order.
        total: Interfaces matching the request, across pages.
        offset: Index of the first row.
        limit: Page size used.
        next_offset: Offset of the next page, or ``None`` on the last page.
        truncated: True when the inventory was larger than one index reads (E.29).
        collector_id: Position whose current values are shown, if any.
        position: Its label.
        telemetry: ``ok``, or why current values are unavailable.
        generated_at: When the view was computed (UTC).
    """

    rows: tuple[InterfaceListRow, ...]
    total: int
    offset: int
    limit: int
    next_offset: int | None
    truncated: bool
    collector_id: CollectorId | None
    position: str
    telemetry: Reading
    generated_at: datetime


class SearchKind(StrEnum):
    """What a search hit is (12.1)."""

    DEVICE = "device"
    INTERFACE = "interface"
    SITE = "site"
    PROVIDER = "provider"
    CIRCUIT = "circuit"
    TAG = "tag"
    GROUP = "group"


@dataclass(frozen=True, slots=True)
class SearchHit:
    """One search result.

    Attributes:
        kind: What it is.
        id: UUID text of the record; ``None`` for a provider (a name, not a record).
        label: What to show, such as ``core1 Gi0/0/1``.
        detail: Context, such as the device's site.
        matched: Field that matched, such as ``if_alias`` or ``poll_address``.
        device_id: Device to open, for device and interface hits.
        interface_id: Interface whose history to open, for interface and circuit hits.
    """

    kind: SearchKind
    id: str | None
    label: str
    detail: str
    matched: str
    device_id: DeviceId | None = None
    interface_id: InterfaceId | None = None


@dataclass(frozen=True, slots=True)
class SearchView:
    """Global search results, grouped by kind in a fixed order (12.1).

    Attributes:
        text: The normalised search text.
        hits: Hits, devices first, then interfaces, sites, providers, circuits, tags, groups.
        totals: ``(kind, matches)`` per kind, before the per-kind cap.
        truncated: True when a kind had more matches than shown, or the
            inventory was larger than one index reads.
        generated_at: When the view was computed (UTC).
    """

    text: str
    hits: tuple[SearchHit, ...]
    totals: tuple[tuple[str, int], ...]
    truncated: bool
    generated_at: datetime
