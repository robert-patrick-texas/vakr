"""What every history answer needs besides its series: subject, positions, interval, zone.

A graph of one interface or device reads its inventory once (the device,
its site, the interface, and the collectors), picks the positions to show,
and finds each position's effective poll interval, which E.19 gaps and
coverage counts depend on. Positions are chosen so that evidence is never
merged (MPOS-008): every chosen collector gets its own series. Traffic
graphs default to the local collector when it is known (27.5 #6, named by
``scheduler.collector_name``), else every position; latency graphs always
show every position (MPOS-T04).

Dependencies:
    vakr.ports.metadata, vakr.ports.telemetry: Inventory and current state.
    vakr.query.*: Settings, zone, and window views.

Safety:
    Read-only; inventory records never carry secrets into views.

Example:
    >>> from vakr.adapters.memory import memory_uow_factory
    >>> with memory_uow_factory()() as uow:
    ...     read_subject(uow, interface_id=None, device_id=None) is None
    True
"""

from dataclasses import dataclass
from datetime import datetime

from vakr.domain.catalog import Collector
from vakr.domain.ids import CollectorId, DeviceId, InterfaceId
from vakr.domain.interfaces import Interface
from vakr.domain.inventory import Device, Site
from vakr.domain.paging import MAX_PAGE_LIMIT
from vakr.domain.series import TimeWindow
from vakr.logic.natural import natural_key
from vakr.ports.metadata import MetadataUnitOfWork
from vakr.ports.telemetry import TelemetryBackend
from vakr.query.history_limits import RangePreset
from vakr.query.history_views import WindowView
from vakr.query.interface_rows import display_name
from vakr.query.settings import QuerySettings
from vakr.query.snapshot import pages
from vakr.query.zone import site_zone, zone_name


@dataclass(frozen=True, slots=True)
class Subject:
    """The inventory behind one graph.

    Attributes:
        device: The device.
        interface: The interface, for interface graphs.
        site: The interface's (override) or device's site, if any.
        collectors: Every collector, in natural position order.
    """

    device: Device
    interface: Interface | None
    site: Site | None
    collectors: tuple[Collector, ...]

    @property
    def name(self) -> str:
        """Return display text, such as ``core1 Gi0/0/1``.

        Args:
            None

        Returns:
            str: Device name, plus the interface name for interface graphs.

        Raises:
            None
        """
        if self.interface is None:
            return self.device.name
        return f"{self.device.name} {display_name(self.interface)}"


def read_subject(
    uow: MetadataUnitOfWork, *, interface_id: InterfaceId | None, device_id: DeviceId | None
) -> Subject | None:
    """Read the device, interface, site, and collectors of a graph.

    Args:
        uow: An open unit of work; nothing is written.
        interface_id: The interface, for interface graphs.
        device_id: The device, for device graphs (ignored with an interface).

    Returns:
        Subject | None: ``None`` when the interface or device does not exist.

    Raises:
        StorageError: If the metadata store cannot be read.
    """
    interface = uow.interfaces.get(interface_id) if interface_id is not None else None
    if interface_id is not None and interface is None:
        return None
    wanted = interface.device_id if interface is not None else device_id
    device = uow.devices.get(wanted) if wanted is not None else None
    if device is None:
        return None
    site_id = device.site_id
    if interface is not None and interface.annotation.site_override is not None:
        site_id = interface.annotation.site_override
    site = uow.sites.get(site_id) if site_id is not None else None
    found, _ = pages(uow.collectors.list_page, MAX_PAGE_LIMIT)
    ordered = sorted(found, key=lambda c: (natural_key(c.position), c.name, str(c.id)))
    return Subject(device, interface, site, tuple(ordered))


def choose_collectors(
    collectors: tuple[Collector, ...],
    requested: CollectorId | None,
    local: str,
    *,
    every: bool,
) -> tuple[Collector, ...]:
    """Pick the positions to show; each gets its own series (MPOS-008).

    Args:
        collectors: Every collector, in position order.
        requested: Only this collector, when given (unknown IDs give none).
        local: The local collector's name; empty when unknown.
        every: Show every position (latency) instead of preferring the local one.

    Returns:
        tuple[Collector, ...]: The chosen collectors, in position order.

    Raises:
        None
    """
    if requested is not None:
        return tuple(c for c in collectors if c.id == requested)
    mine = tuple(c for c in collectors if c.name == local)
    return collectors if every or not mine else mine


def interval_of(
    backend: TelemetryBackend,
    device: Device,
    collector: Collector,
    settings: QuerySettings,
    now: datetime,
) -> int:
    """Return a position's effective poll interval for a device.

    The latest poll record's effective interval is the evidence; without
    one, the device's override or the configured default applies.

    Args:
        backend: The telemetry view.
        device: The device.
        collector: The position.
        settings: Defaults.
        now: Reference time (UTC).

    Returns:
        int: Seconds, at least 1.

    Raises:
        StorageError: If the store cannot be read.
    """
    state = backend.current.device_state(device.id, collector.id, now=now)
    if state is not None and state.poll is not None:
        return state.poll.effective_interval_s
    return device.poll_interval_s or settings.default_interval_s


def window_view(
    window: TimeWindow, preset: RangePreset | None, settings: QuerySettings, site: Site | None
) -> WindowView:
    """Describe a window with the display zone (TIME-002, TIME-003, API-006).

    Args:
        window: The window.
        preset: The preset it came from, if any.
        settings: For ``time.timezone``.
        site: The subject's site, for its zone.

    Returns:
        WindowView: Bounds, preset, and zones.

    Raises:
        None
    """
    zone = site_zone(site.timezone) if site is not None else None
    return WindowView(
        window.start,
        window.end,
        preset.value if preset else None,
        zone_name(settings.timezone),
        zone,
    )


def pick_collector(
    collectors: tuple[Collector, ...], requested: CollectorId | None, local: str
) -> Collector | None:
    """Choose the one collector a ranking uses.

    Args:
        collectors: Every collector, in position order.
        requested: The requested collector, if any.
        local: The local collector's name, if known.

    Returns:
        Collector | None: The requested one, else the local one, else the first.

    Raises:
        None
    """
    if requested is not None:
        return next((c for c in collectors if c.id == requested), None)
    return next((c for c in collectors if c.name == local), collectors[0] if collectors else None)
