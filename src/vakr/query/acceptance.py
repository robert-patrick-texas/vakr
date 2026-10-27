"""``AcceptanceExport``: vakr's own measurements for the parallel run (MVP-ACC-03, MVP-ACC-04).

MVP acceptance compares vakr with MRTG and SmokePing over at least 14 days.
MRTG reports 5-minute averages on its own poll schedule, and SmokePing one
median and loss count per step, so a fair comparison re-aggregates vakr's
data over *their* windows. That needs vakr's native measurements, not a
downsampled graph: every interface sample's interval and in/out rates, and
every ICMP cycle's probe RTTs. This service plans which devices and
interfaces to read (from a targets file, ``vakr.query.acceptance_targets``)
and reads their observations page by page through the telemetry read port.

Dependencies:
    vakr.ports.metadata, vakr.ports.telemetry: Inventory and raw observations.
    vakr.query.acceptance_plan, acceptance_targets: The plan and the targets filter.
    vakr.query.snapshot: Bounded paging of inventory lists.

Safety:
    Read-only and synchronous; every read is bounded by the window, the
    page limit, the inventory caps, and the caller's row budget. Missing
    rates stay ``None``; nothing here turns them into zero (invariant 1).

Example:
    >>> from vakr.adapters.memory import memory_telemetry_factory, memory_uow_factory
    >>> service = AcceptanceExport(memory_uow_factory(), memory_telemetry_factory())
    >>> service.collectors()
    ()
"""

from collections.abc import Callable, Iterator, Sequence
from datetime import datetime, timedelta
from uuid import UUID

from vakr.domain.catalog import Collector
from vakr.domain.enums import ObservationType
from vakr.domain.errors import ValidationError
from vakr.domain.interfaces import Interface
from vakr.domain.observations import IcmpObservation, InterfaceObservation, Observation
from vakr.domain.paging import MAX_PAGE_LIMIT, PageRequest
from vakr.domain.series import TimeWindow
from vakr.ports.clock import Clock, SystemClock
from vakr.ports.metadata import DeviceFilter, InterfaceFilter, MetadataUnitOfWorkFactory
from vakr.ports.telemetry import TelemetryBackend
from vakr.query.acceptance_plan import ExportPlan, PlannedDevice, PlannedInterface, resolve, wanted
from vakr.query.acceptance_targets import TargetSpec
from vakr.query.settings import QuerySettings
from vakr.query.snapshot import pages

MAX_WINDOW = timedelta(days=31)
"""Longest export window: the native retention (30 days) plus a day (E.34)."""
MAX_FUTURE = timedelta(minutes=5)
DEFAULT_MAX_ROWS = 5_000_000
"""Rows (interface samples plus ICMP cycles) one export writes by default (E.34)."""
MAX_ROWS = 20_000_000


def check_window(window: TimeWindow, now_utc: datetime) -> None:
    """Refuse windows longer than ``MAX_WINDOW`` or ending far in the future.

    Args:
        window: The requested window.
        now_utc: The current time (UTC).

    Returns:
        None

    Raises:
        ValidationError: If the window breaks a bound.
    """
    if window.duration > MAX_WINDOW:
        raise ValidationError("the export window is longer than 31 days", field="window")
    if window.end > now_utc + MAX_FUTURE:
        raise ValidationError("the export window ends in the future", field="window")


class AcceptanceExport:
    """Plans and reads the parallel-run export over the ports."""

    def __init__(
        self,
        metadata: MetadataUnitOfWorkFactory,
        telemetry: Callable[[], TelemetryBackend],
        settings: QuerySettings | None = None,
        clock: Clock | None = None,
    ) -> None:
        """Bind the service to its stores.

        Args:
            metadata: Opens a metadata unit of work; only reads are made.
            telemetry: Opens a telemetry view.
            settings: Bounds and the local collector name.
            clock: Time source.

        Returns:
            None

        Raises:
            None
        """
        self._metadata = metadata
        self._telemetry = telemetry
        self._settings = settings or QuerySettings()
        self.clock = clock or SystemClock()

    def collectors(self) -> tuple[Collector, ...]:
        """List every collector (position), in name order.

        Args:
            None

        Returns:
            tuple[Collector, ...]: The collectors.

        Raises:
            StorageError: If the metadata store cannot be read.
        """
        with self._metadata() as uow:
            found, _ = pages(uow.collectors.list_page, MAX_PAGE_LIMIT)
        return tuple(sorted(found, key=lambda c: c.name))

    def _collector(self, name: str) -> tuple[Collector, tuple[Collector, ...]]:
        """Choose the position to export: the named one, the local one, else the first.

        Args:
            name: Requested collector name, or ``""``.

        Returns:
            tuple: The chosen collector and every collector.

        Raises:
            ValidationError: If there is no collector, or the named one is unknown.
        """
        every = self.collectors()
        wanted = name or self._settings.local_collector
        chosen = next((c for c in every if c.name == wanted), None)
        if chosen is None and name:
            raise ValidationError(f"no collector named {name!r}", field="collector")
        chosen = chosen or (every[0] if every else None)
        if chosen is None:
            raise ValidationError("no collector has been registered yet", field="collector")
        return chosen, every

    def plan(self, targets: Sequence[TargetSpec] | None, collector: str = "") -> ExportPlan:
        """Resolve the targets against the inventory.

        Args:
            targets: Parsed targets file; ``None`` exports every device's
                ICMP cycles and every interface's samples.
            collector: Collector name, or ``""`` for the local position.

        Returns:
            ExportPlan: Devices and interfaces to read, and unmatched lines.

        Raises:
            ValidationError: If no collector can be chosen.
            StorageError: If the metadata store cannot be read.
        """
        chosen, every = self._collector(collector)
        limits = self._settings.limits
        with self._metadata() as uow:
            devices, truncated = pages(
                lambda p: uow.devices.list_page(p, DeviceFilter()), limits.max_devices
            )
            chosen_devices = [d for d in devices if wanted(d, targets)]
            listed: dict[UUID, list[Interface]] = {}
            for device in chosen_devices:
                where = InterfaceFilter(device_id=device.id)
                found, cut = pages(lambda p, w=where: uow.interfaces.list_page(p, w),
                                   limits.max_device_interfaces)  # fmt: skip
                listed[device.id] = found
                truncated = truncated or cut
        planned, unmatched = resolve(sorted(chosen_devices, key=lambda d: d.name), listed, targets)
        return ExportPlan(chosen, every, planned, unmatched, truncated)

    def _pages(self, kind: ObservationType, subject: UUID, plan: ExportPlan,
               window: TimeWindow) -> Iterator[Observation]:  # fmt: skip
        """Yield one subject's observations oldest first, a page at a time.

        Args:
            kind: Observation type.
            subject: Interface or device.
            plan: The plan (its collector is read).
            window: The window.

        Returns:
            Iterator[Observation]: Observations.

        Raises:
            StorageError: If the telemetry store cannot be read.
        """
        query = self._telemetry().query
        cursor: str | None = None
        while True:
            page = query.read_raw(kind, subject, window, PageRequest(MAX_PAGE_LIMIT, cursor),
                                  collector_id=plan.collector.id)  # fmt: skip
            yield from page.items
            if page.next_cursor is None:
                return
            cursor = page.next_cursor

    def samples(self, plan: ExportPlan, iface: PlannedInterface,
                window: TimeWindow) -> Iterator[InterfaceObservation]:  # fmt: skip
        """Yield an interface's samples from the plan's collector, oldest first.

        Args:
            plan: The plan.
            iface: The interface.
            window: The window.

        Returns:
            Iterator[InterfaceObservation]: Samples.

        Raises:
            StorageError: If the telemetry store cannot be read.
        """
        found = self._pages(ObservationType.INTERFACE, iface.interface.id, plan, window)
        return (o for o in found if isinstance(o, InterfaceObservation))

    def cycles(self, plan: ExportPlan, device: PlannedDevice,
               window: TimeWindow) -> Iterator[IcmpObservation]:  # fmt: skip
        """Yield a device's ICMP cycles from the plan's collector, oldest first.

        Args:
            plan: The plan.
            device: The device.
            window: The window.

        Returns:
            Iterator[IcmpObservation]: Cycles.

        Raises:
            StorageError: If the telemetry store cannot be read.
        """
        found = self._pages(ObservationType.ICMP, device.device.id, plan, window)
        return (o for o in found if isinstance(o, IcmpObservation))
