"""Read one bounded snapshot of inventory and current state through the ports (TUI-007).

A device list needs every device at once: its order depends on computed
status, which no store can sort by. So a refresh reads inventory in
bounded pages (E.29 ``max_devices``), the current state of each collector
(``CurrentStateQuery.device_states``), each collector's latest health, and,
only for devices whose ``sysUpTime`` is shorter than the restart window,
their recent SNMP history to find when the restart was detected (E.2 row 9).

Telemetry trouble never hides the inventory (13.4): a ``StorageError`` from
the telemetry store is caught and recorded, the devices are still listed,
and their status reads "Unknown — telemetry unavailable", never "Down".

Dependencies:
    vakr.ports.metadata, vakr.ports.telemetry: The only data access.
    vakr.domain: Records, observations, and paging.

Safety:
    Synchronous: every call blocks on storage. Async callers run it with
    ``asyncio.to_thread`` (no blocking on the event loop). Read-only.

Example:
    >>> snap = TelemetrySnapshot.unavailable("no telemetry database yet")
    >>> snap.available, snap.detail
    (False, 'no telemetry database yet')
"""

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from vakr.domain.catalog import Collector, Tag
from vakr.domain.enums import InterfaceLifecycle, ObservationType, UptimeContinuity
from vakr.domain.errors import StorageError
from vakr.domain.ids import CollectorId, DeviceId, SiteId, TagId
from vakr.domain.inventory import Device, Site
from vakr.domain.observations import CollectorHealthObservation, SnmpDeviceObservation
from vakr.domain.paging import MAX_PAGE_LIMIT, Page, PageRequest
from vakr.domain.series import TimeWindow
from vakr.domain.state import CurrentDeviceState
from vakr.ports.metadata import DeviceFilter, InterfaceFilter, MetadataUnitOfWork
from vakr.ports.telemetry import TelemetryBackend
from vakr.query.settings import QuerySettings

type StateKey = tuple[CollectorId, DeviceId]


def pages[T](fetch: Callable[[PageRequest], Page[T]], cap: int) -> tuple[list[T], bool]:
    """Read pages until the end or until ``cap`` items, whichever is first.

    Args:
        fetch: Returns the page for a request.
        cap: Most items to read.

    Returns:
        tuple[list[T], bool]: The items, and True when more existed.

    Raises:
        ValidationError: If a store rejects its own cursor.
        StorageError: If the store cannot be read.
    """
    items: list[T] = []
    cursor: str | None = None
    while True:
        page = fetch(PageRequest(limit=min(MAX_PAGE_LIMIT, cap - len(items) + 1), cursor=cursor))
        items.extend(page.items)
        cursor = page.next_cursor
        if len(items) > cap:
            return items[:cap], True
        if cursor is None:
            return items, False


@dataclass(frozen=True, slots=True)
class Inventory:
    """The metadata a list or detail view needs, read in one unit of work.

    Attributes:
        devices: Devices, in repository order.
        truncated: True when more devices exist than were read.
        sites: Sites by ID.
        tags: Tags by ID.
        collectors: Collectors (positions), in repository order.
        interface_counts: ``(active, total)`` non-retired interfaces per device.
        counts_truncated: True when interfaces were too many to count.
    """

    devices: tuple[Device, ...]
    truncated: bool
    sites: dict[SiteId, Site]
    tags: dict[TagId, Tag]
    collectors: tuple[Collector, ...]
    interface_counts: dict[DeviceId, tuple[int, int]] = field(default_factory=dict)
    counts_truncated: bool = False


def read_inventory(
    uow: MetadataUnitOfWork,
    settings: QuerySettings,
    *,
    include_retired: bool = False,
    devices: bool = True,
    count: bool = False,
) -> Inventory:
    """Read devices, sites, tags, collectors, and optionally interface counts.

    Args:
        uow: An open unit of work; nothing is written.
        settings: Read bounds.
        include_retired: Also read ``RETIRED`` devices (7.5).
        devices: Read devices at all; the detail view reads its one device itself.
        count: Also count interfaces per device (one bounded scan).

    Returns:
        Inventory: The records.

    Raises:
        StorageError: If the metadata store cannot be read.
    """
    limits = settings.limits
    where = DeviceFilter(include_retired=include_retired)
    found, truncated = (
        pages(lambda p: uow.devices.list_page(p, where), limits.max_devices)
        if devices
        else ([], False)
    )
    sites, _ = pages(uow.sites.list_page, limits.max_devices)
    tags, _ = pages(uow.tags.list_page, limits.max_devices)
    collectors, _ = pages(uow.collectors.list_page, MAX_PAGE_LIMIT)
    counts: dict[DeviceId, tuple[int, int]] = {}
    counts_truncated = False
    if count:
        interfaces, counts_truncated = pages(
            lambda p: uow.interfaces.list_page(p, InterfaceFilter()), limits.max_interfaces_counted
        )
        for i in interfaces:
            active, total = counts.get(i.device_id, (0, 0))
            counts[i.device_id] = (active + (i.lifecycle is InterfaceLifecycle.ACTIVE), total + 1)
    return Inventory(
        tuple(found),
        truncated,
        {s.id: s for s in sites},
        {t.id: t for t in tags},
        tuple(collectors),
        counts,
        counts_truncated,
    )


@dataclass(frozen=True, slots=True)
class TelemetrySnapshot:
    """Current state of every collector, or why it could not be read.

    Attributes:
        available: False when the telemetry store could not be read.
        detail: Why it is unavailable, for the operator.
        states: Device state per ``(collector, device)``.
        health: Latest health report per collector, if any.
        resets: When a recent agent restart was detected, per ``(collector, device)``.
    """

    available: bool
    detail: str | None = None
    states: dict[StateKey, CurrentDeviceState] = field(default_factory=dict)
    health: dict[CollectorId, CollectorHealthObservation] = field(default_factory=dict)
    resets: dict[StateKey, datetime] = field(default_factory=dict)

    @classmethod
    def unavailable(cls, detail: str) -> TelemetrySnapshot:
        """Return the snapshot of a store that could not be read.

        Args:
            detail: Why, for the operator.

        Returns:
            TelemetrySnapshot: No states, ``available=False``.

        Raises:
            None
        """
        return cls(False, detail)


def _recent_reset(
    backend: TelemetryBackend, state: CurrentDeviceState, now: datetime, settings: QuerySettings
) -> datetime | None:
    """Find when a restart within the window was detected, reading history only if needed.

    An agent that has been up longer than the window cannot have restarted
    within it, so most devices cost no extra read.

    Args:
        backend: The telemetry store.
        state: The device's current state from one collector.
        now: Reference time (UTC).
        settings: Restart window and history bound.

    Returns:
        datetime | None: The newest ``RESET_DETECTED`` time, if within the window.

    Raises:
        StorageError: If the store cannot be read.
    """
    snmp = state.snmp
    if snmp is None:
        return None
    if snmp.uptime_continuity is UptimeContinuity.RESET_DETECTED:
        return snmp.timestamp
    window_s = settings.restart_window_s
    if snmp.sys_uptime_ticks is None or snmp.sys_uptime_ticks / 100 > window_s:
        return None
    window = TimeWindow(now - timedelta(seconds=window_s), now)
    page = backend.query.read_raw(
        ObservationType.SNMP_DEVICE,
        state.device_id,
        window,
        PageRequest(limit=min(MAX_PAGE_LIMIT, settings.limits.restart_history)),
        collector_id=state.collector_id,
    )
    resets = (
        o.timestamp
        for o in page.items
        if isinstance(o, SnmpDeviceObservation)
        and o.uptime_continuity is UptimeContinuity.RESET_DETECTED
    )
    return max(resets, default=None)


def read_telemetry(
    open_backend: Callable[[], TelemetryBackend] | None,
    collectors: tuple[Collector, ...],
    now: datetime,
    settings: QuerySettings,
    only: DeviceId | None = None,
) -> TelemetrySnapshot:
    """Read the current state of every collector, or record why it could not be read.

    Args:
        open_backend: Opens a telemetry view; ``None`` when there is no store yet.
        collectors: Collectors to read.
        now: Reference time (UTC).
        settings: Bounds and restart window.
        only: Read just this device (the detail view), if given.

    Returns:
        TelemetrySnapshot: States, health, and restart times.

    Raises:
        None
    """
    if open_backend is None:
        return TelemetrySnapshot.unavailable("no telemetry database yet")
    try:
        backend = open_backend()
        snap = TelemetrySnapshot(True)
        for c in collectors:
            if (health := backend.collector_health.latest(c.id)) is not None:
                snap.health[c.id] = health
            if only is not None:
                one = backend.current.device_state(only, c.id, now=now)
                found = [one] if one is not None else []
            else:
                found, _ = pages(
                    lambda p, cid=c.id: backend.current.device_states(cid, p, now=now),
                    settings.limits.max_devices,
                )
            for state in found:
                key = (c.id, state.device_id)
                snap.states[key] = state
                if (reset := _recent_reset(backend, state, now, settings)) is not None:
                    snap.resets[key] = reset
    except StorageError as exc:
        return TelemetrySnapshot.unavailable(f"telemetry unreadable: {exc}")
    return snap
