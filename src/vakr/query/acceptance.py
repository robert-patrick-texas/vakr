"""``AcceptanceQueries``: the MVP acceptance reports from vakr's own data (27.6, E.35).

``schedule`` judges MVP-ACC-01 (on-time starts, no same-device overlap, no
drift, the 60 s workload), ``gaps`` MVP-ACC-10 (every period without data,
explained by recorded evidence), and ``coverage`` MVP-ACC-02 (every
monitored interface's traffic, packet, and error graphs at every range,
and no spike across reloads or discontinuities). Each report is computed
per collector, because each position is accepted on its own evidence
(MPOS-008), over a window that ends ``SETTLE`` before now so records still
in the spool are not mistaken for gaps.

Dependencies:
    vakr.ports: Metadata units of work and the telemetry acceptance port.
    vakr.query.acceptance_*: The reports' parts.

Safety:
    Read-only and synchronous; every read is bounded (``acceptance_limits``).
    Missing data is never zero, and collector or storage trouble is never a
    device cause (invariants 1 and 2).

Example:
    >>> from vakr.adapters.memory import memory_telemetry_factory, memory_uow_factory
    >>> q = AcceptanceQueries(memory_uow_factory(), memory_telemetry_factory())
    >>> q.schedule(AcceptanceRequest()).verdict.value
    'incomplete'
"""

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import cast

from vakr.domain.catalog import Collector
from vakr.domain.enums import AdminState, InterfaceLifecycle, ReachabilityStrategy
from vakr.domain.errors import StorageError, ValidationError
from vakr.domain.inventory import Device
from vakr.domain.series import TimeWindow
from vakr.domain.timestamps import to_epoch_us
from vakr.ports.metadata import MetadataUnitOfWorkFactory
from vakr.ports.telemetry import TelemetryBackend
from vakr.ports.telemetry.acceptance import AcceptanceBackend
from vakr.query.acceptance_coverage_scan import CoverageScan
from vakr.query.acceptance_gaps import GapScan
from vakr.query.acceptance_inventory import AcceptanceInventory, interface_name, read_inventory
from vakr.query.acceptance_limits import (
    BUCKET_US,
    REFERENCE_INTERVAL_S,
    SETTLE,
)
from vakr.query.acceptance_request import (
    AcceptanceRequest,
    LossReader,
    evidence_of,
    notes_of,
)
from vakr.query.acceptance_schedule import ScheduleTally
from vakr.query.acceptance_schedule_judge import judge
from vakr.query.acceptance_subjects import as_time
from vakr.query.acceptance_views import AcceptanceReport, combine


class AcceptanceQueries:
    """The acceptance reports over the metadata and telemetry ports."""

    def __init__(
        self, uow_factory: MetadataUnitOfWorkFactory, telemetry: Callable[[], TelemetryBackend],
        *, losses: LossReader | None = None, local_collector: str | None = None,
        strategy: ReachabilityStrategy = ReachabilityStrategy.ICMP_GATED,
        clock: Callable[[], datetime] | None = None,
    ) -> None:  # fmt: skip
        """Bind the service.

        Args:
            uow_factory: Opens metadata units of work.
            telemetry: Opens a telemetry backend that offers ``acceptance``.
            losses: Reads a collector's telemetry-loss incidents; none when ``None``.
            local_collector: This node's collector name (a peer's silence may be lag).
            strategy: The configured default reachability strategy.
            clock: Returns now (UTC); the system clock when ``None``.

        Returns:
            None

        Raises:
            None
        """
        self._uow, self._telemetry = uow_factory, telemetry
        self._losses = losses or (lambda _c: ())
        self._local, self._strategy = local_collector, strategy
        self._clock = clock or (lambda: datetime.now(UTC))

    def _backend(self) -> AcceptanceBackend:
        """Open the telemetry backend and confirm it offers the acceptance port.

        Args:
            None

        Returns:
            AcceptanceBackend: The backend.

        Raises:
            StorageError: If the store cannot be opened or serves no acceptance reads.
        """
        backend = self._telemetry()
        if getattr(backend, "acceptance", None) is None:
            raise StorageError("this telemetry store serves no acceptance reports", retryable=False)
        return cast(AcceptanceBackend, backend)

    def _window(self, span: timedelta, *, align: bool) -> tuple[datetime, int, int]:
        """Return now and the judged window ``(start_us, end_us)``.

        Args:
            span: The window length.
            align: Align the start down to a 15-minute bucket.

        Returns:
            tuple[datetime, int, int]: Now, start, end.

        Raises:
            None
        """
        now = self._clock()
        end = to_epoch_us(now - SETTLE)
        start = end - int(span.total_seconds() * 1_000_000)
        return now, (start - start % BUCKET_US) if align else start, end

    def _collectors(self, inv: AcceptanceInventory, only: str | None) -> list[Collector]:
        """Return the collectors to judge.

        Args:
            inv: The inventory.
            only: A collector name, or ``None`` for all.

        Returns:
            list[Collector]: By name.

        Raises:
            ValidationError: If ``only`` names no collector.
        """
        chosen = [c for c in inv.collectors if only is None or c.name == only]
        if only is not None and not chosen:
            raise ValidationError(f"no collector is named {only!r}", field="collector")
        return sorted(chosen, key=lambda c: c.name)

    def schedule(self, request: AcceptanceRequest) -> AcceptanceReport:
        """MVP-ACC-01 for every collector (or one).

        Args:
            request: Window, row limit, collector.

        Returns:
            AcceptanceReport: The ``schedule`` report.

        Raises:
            ValidationError: If the collector is unknown.
            StorageError: If a store is unavailable.
        """
        now, start, end = self._window(request.window, align=False)
        inv = read_inventory(self._uow, as_time(start), audit=False)
        backend, window = self._backend(), TimeWindow(as_time(start), as_time(end))
        sections = []
        for collector in self._collectors(inv, request.collector):
            tally = ScheduleTally()
            for device in sorted(inv.devices, key=lambda d: d.name):
                marks = backend.acceptance.poll_marks(device.id, collector.id, window)
                tally.add_device(device.name, marks.items, (start, end), REFERENCE_INTERVAL_S)
            latest = backend.collector_health.latest(collector.id)
            interfaces = None if latest is None else latest.active_interfaces
            sections.append(judge(tally, collector.name, interfaces))
        return AcceptanceReport(
            "schedule",
            "MVP-ACC-01",
            now,
            window.start,
            window.end,
            tuple(sections),
            combine(s.verdict for s in sections),
        )

    def gaps(self, request: AcceptanceRequest) -> AcceptanceReport:
        """MVP-ACC-10 for every collector (or one).

        Args:
            request: Window, row limit, collector.

        Returns:
            AcceptanceReport: The ``gaps`` report.

        Raises:
            ValidationError: If the collector is unknown.
            StorageError: If a store is unavailable.
        """
        now, start, end = self._window(request.window, align=True)
        inv = read_inventory(self._uow, as_time(start), audit=True)
        backend = self._backend()
        frontier = backend.rollups.coverage_end() if backend.capabilities.rollups else None
        notes = notes_of(inv.truncated, frontier, end)
        sections = []
        for collector in self._collectors(inv, request.collector):
            scan = GapScan(
                backend,
                collector.id,
                evidence_of(
                    backend,
                    collector,
                    (start, end),
                    self._losses(collector.id),
                    local=collector.name == self._local,
                ),
                (start, end),
                None if frontier is None else to_epoch_us(frontier),
            )
            for device in sorted(inv.devices, key=lambda d: d.name):
                scan.device(inv.device_input(device, self._strategy))
            sections.append(scan.result(collector.name, request.limit, notes))
        return AcceptanceReport(
            "gaps",
            "MVP-ACC-10",
            now,
            as_time(start),
            as_time(end),
            tuple(sections),
            combine(s.verdict for s in sections),
        )

    def coverage(self, request: AcceptanceRequest) -> AcceptanceReport:
        """MVP-ACC-02 for every collector (or one); ``request.window`` is the event window.

        Args:
            request: Event window, row limit, collector.

        Returns:
            AcceptanceReport: The ``coverage`` report.

        Raises:
            ValidationError: If the collector is unknown.
            StorageError: If a store is unavailable.
        """
        now, start, end = self._window(request.window, align=False)
        inv = read_inventory(self._uow, as_time(start), audit=False)
        backend = self._backend()
        frontier = backend.rollups.coverage_end() if backend.capabilities.rollups else None
        frontier_us = None if frontier is None else to_epoch_us(frontier)
        notes = notes_of(inv.truncated, frontier, end)
        sections = []
        for collector in self._collectors(inv, request.collector):
            scan = CoverageScan(backend, collector.id, to_epoch_us(now), (start, end), frontier_us)
            for device in sorted(inv.devices, key=lambda d: d.name):
                self._cover_device(scan, inv, device)
            sections.append(scan.result(collector.name, request.limit, notes))
        return AcceptanceReport(
            "coverage",
            "MVP-ACC-02",
            now,
            as_time(start),
            as_time(end),
            tuple(sections),
            combine(s.verdict for s in sections),
        )

    def _cover_device(self, scan: CoverageScan, inv: AcceptanceInventory, d: Device) -> None:
        """Judge one device's monitored interfaces and reloads.

        Args:
            scan: The collector's scan.
            inv: The inventory.
            d: The device.

        Returns:
            None

        Raises:
            StorageError: If the store is unavailable.
        """
        mode = d.reachability or self._strategy
        offline = d.admin_state in (AdminState.DISABLED, AdminState.RETIRED)
        if offline or mode is ReachabilityStrategy.ICMP_ONLY:
            return
        monitored = [(i.id, interface_name(i.if_name, i.if_descr, i.if_alias, str(i.id)),
                      to_epoch_us(i.created_at))
                     for i in inv.interfaces.get(d.id, [])
                     if i.lifecycle is InterfaceLifecycle.ACTIVE]  # fmt: skip
        for iface, name, created in monitored:
            scan.interface(d.name, iface, name, created)
        interval = d.poll_interval_s or REFERENCE_INTERVAL_S
        scan.device_reloads(d.id, d.name, [(i, n) for i, n, _c in monitored], interval)
