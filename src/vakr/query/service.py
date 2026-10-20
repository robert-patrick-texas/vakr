"""``StatusQueries``: the current-state read service behind ``vakr status`` and ``vakr tui``.

Until the M4 API exists, the CLI and the TUI use this service in-process
(TUI-007 allows "application/query services"). It reads only through the
metadata and telemetry ports, judges each device per collector with the
Appendix E.2 table, merges positions (MPOS-007), and returns immutable
view models. Metadata trouble raises ``StorageError``; telemetry trouble is
reported inside the view, so inventory stays visible (13.4).

Dependencies:
    vakr.ports.metadata, vakr.ports.telemetry, vakr.ports.clock: Data and time.
    vakr.query.*: Snapshot reads, judgements, rows, listing, detail facts.

Safety:
    Read-only and synchronous: every method blocks on storage, so async
    callers (the TUI) run it with ``asyncio.to_thread``. Every read is
    bounded (E.29).

Example:
    >>> from vakr.adapters.memory import memory_telemetry_factory, memory_uow_factory
    >>> view = StatusQueries(memory_uow_factory(), memory_telemetry_factory()).device_list()
    >>> view.total, view.health.telemetry.text
    (0, 'ok')
"""

from collections.abc import Callable
from dataclasses import replace
from datetime import datetime

from vakr.domain.inventory import Device
from vakr.logic.status import PositionStatus
from vakr.ports.clock import Clock, SystemClock
from vakr.ports.metadata import MetadataUnitOfWorkFactory
from vakr.ports.telemetry import TelemetryBackend
from vakr.query.detail import (
    MISSING_NOTE,
    UPTIME_NOTE,
    facts,
    find_device,
    read_detail_inventory,
    read_interface_states,
)
from vakr.query.detail_views import DeviceDetailView
from vakr.query.device_rows import Seen, device_row, in_position_order, shown
from vakr.query.evidence import (
    Judgement,
    collector_state,
    combine,
    judge,
    telemetry_unavailable,
)
from vakr.query.interface_rows import interface_row, order_key
from vakr.query.labels import quality_words
from vakr.query.listing import DeviceListRequest, bucket_counts, matches, ordered
from vakr.query.settings import QuerySettings
from vakr.query.snapshot import Inventory, TelemetrySnapshot, read_inventory, read_telemetry
from vakr.query.text import age_text, percent
from vakr.query.views import (
    CollectorHealthView,
    DeviceListView,
    DeviceRow,
    HealthView,
    Reading,
    missing,
)

OK = Reading("ok", "ok")


class StatusQueries:
    """Device list and device detail over the ports."""

    def __init__(
        self,
        metadata: MetadataUnitOfWorkFactory,
        telemetry: Callable[[], TelemetryBackend] | None,
        settings: QuerySettings | None = None,
        clock: Clock | None = None,
    ) -> None:
        """Bind the service to its stores.

        Args:
            metadata: Opens a metadata unit of work; only reads are made.
            telemetry: Opens a telemetry view; ``None`` when no store exists yet.
            settings: Defaults and bounds; packaged defaults when ``None``.
            clock: Time source; the system clock when ``None``.

        Returns:
            None

        Raises:
            None
        """
        self._metadata = metadata
        self._telemetry = telemetry
        self._settings = settings or QuerySettings()
        self._clock = clock or SystemClock()

    def device_list(self, request: DeviceListRequest | None = None) -> DeviceListView:
        """Return one page of the device list in 7.7 order (or the requested sort).

        Args:
            request: Search, filter, sort, and page; defaults when ``None``.

        Returns:
            DeviceListView: Rows, counts, and health.

        Raises:
            StorageError: If the metadata store cannot be read.
        """
        request = request or DeviceListRequest()
        now = self._clock.now_utc()
        with self._metadata() as uow:
            inv = read_inventory(
                uow, self._settings, include_retired=request.include_retired, count=True
            )
        snap = read_telemetry(self._telemetry, inv.collectors, now, self._settings)
        rows = [self._row(d, inv, snap, now)[0] for d in inv.devices]
        matching = ordered((r for r in rows if matches(r, request)), request.sort)
        page = matching[request.offset : request.offset + request.limit]
        return DeviceListView(
            rows=tuple(page),
            total=len(matching),
            offset=request.offset,
            limit=request.limit,
            counts=bucket_counts(matching),
            truncated=inv.truncated,
            generated_at=now,
            health=self._health(inv, snap, now),
        )

    def device_detail(self, key: str) -> DeviceDetailView | None:
        """Return the device screen for one device (TUI-004..006).

        Args:
            key: Exact display name or device UUID.

        Returns:
            DeviceDetailView | None: ``None`` when no device matches.

        Raises:
            StorageError: If the metadata store cannot be read.
        """
        now = self._clock.now_utc()
        cap = self._settings.limits.max_device_interfaces
        with self._metadata() as uow:
            device = find_device(uow, key)
            if device is None:
                return None
            base = read_inventory(uow, self._settings, devices=False)
            inv, interfaces, truncated = read_detail_inventory(uow, device, cap, base)
        snap = read_telemetry(self._telemetry, inv.collectors, now, self._settings, device.id)
        row, seen = self._row(device, inv, snap, now)
        best = shown(seen)
        states, problem = {}, snap.detail
        if best.collector is not None and snap.available:
            states, problem = read_interface_states(
                self._telemetry, device.id, best.collector.id, now, cap
            )
        position = best.collector.position if best.collector else "any collector"
        table = [
            interface_row(i, states.get(i.id), position) for i in sorted(interfaces, key=order_key)
        ]
        state = best.judgement.state
        warnings = quality_words(state.quality) if state is not None else ()
        if problem:
            warnings = (*warnings, problem)
        return DeviceDetailView(
            row=row,
            facts=facts(device, row, best.judgement),
            warnings=warnings,
            interfaces=tuple(table),
            interfaces_total=len(interfaces),
            interfaces_truncated=truncated,
            generated_at=now,
            health=self._health(inv, snap, now),
            notes=(UPTIME_NOTE, MISSING_NOTE),
        )

    def _row(
        self, device: Device, inv: Inventory, snap: TelemetrySnapshot, now: datetime
    ) -> tuple[DeviceRow, list[Seen]]:
        """Judge a device at every position and build its row.

        Args:
            device: The device.
            inv: Inventory with collectors, sites, tags, and counts.
            snap: Telemetry snapshot.
            now: Reference time (UTC).

        Returns:
            tuple: The row, and every position's judgement in position order.

        Raises:
            None
        """
        s = self._settings
        seen = []
        for c in inv.collectors:
            key = (c.id, device.id)
            reported = collector_state(snap.health.get(c.id), now, s.default_interval_s)
            j = judge(device, snap.states.get(key), reported, snap.resets.get(key), s, now)
            seen.append(Seen(c, self._relabel(j, snap)))
        fallback = self._relabel(judge(device, None, None, None, s, now), snap)
        seen = in_position_order(seen or [Seen(None, fallback)])
        positions = [
            PositionStatus(x.collector.position, x.judgement.status) for x in seen if x.collector
        ]
        status = combine(positions, fallback.status).status
        if not snap.available:
            status = telemetry_unavailable(status)
        return device_row(device, inv, seen, status, now), seen

    @staticmethod
    def _relabel(j: Judgement, snap: TelemetrySnapshot) -> Judgement:
        """Name storage trouble in a judgement's status when telemetry is unreadable.

        Args:
            j: A judgement.
            snap: The telemetry snapshot.

        Returns:
            Judgement: Unchanged when telemetry was read.

        Raises:
            None
        """
        if snap.available:
            return j
        return replace(j, status=telemetry_unavailable(j.status))

    def _health(self, inv: Inventory, snap: TelemetrySnapshot, now: datetime) -> HealthView:
        """Summarise storage and collector health for the header (13.2, TUI-009).

        Args:
            inv: Inventory with collectors.
            snap: Telemetry snapshot.
            now: Reference time (UTC).

        Returns:
            HealthView: Metadata, telemetry, and per-collector health.

        Raises:
            None
        """
        if snap.available:
            telemetry = OK
        else:
            telemetry = Reading(f"unavailable: {snap.detail}", "unavailable", snap.detail)
        collectors = []
        for c in inv.collectors:
            report = snap.health.get(c.id)
            judged = collector_state(report, now, self._settings.default_interval_s)
            if report is None or judged is None:
                none = missing("no health report yet")
                collectors.append(CollectorHealthView(c.id, c.position, none, none, none))
                continue
            age_s = (now - report.timestamp).total_seconds()
            pct = report.spool_used_pct
            spool = Reading(percent(pct), pct) if pct is not None else missing("not reported")
            collectors.append(
                CollectorHealthView(
                    c.id,
                    c.position,
                    Reading(judged.value, judged.value),
                    Reading(age_text(age_s), age_s),
                    spool,
                )
            )
        return HealthView(OK, telemetry, tuple(collectors))
