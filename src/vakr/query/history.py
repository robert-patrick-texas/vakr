"""``HistoryQueries``: interface graphs, latency smoke, and state timelines (10.6, 12.4-12.6).

The read service behind the M4 graph endpoints. It reads only through the
metadata and telemetry ports, chooses native or rollup data by the 10.6
table (or the caller's override), and returns the immutable views of
``history_views`` and ``latency_views``, which serialise to JSON as they
are (``jsonable``). Rules it keeps:

- one series per metric and collector; nothing is merged or summed across
  collectors (MPOS-008);
- gaps are ``None`` points (E.19), never zero; markers say why (12.6);
- utilization only against a known link capacity (E.17 #15): a tunnel's
  traffic graph works, its utilization graph is unavailable with the reason;
- telemetry trouble makes a view unavailable with the reason; it never
  reads as an idle link or a down device (invariant 2).

Dependencies:
    vakr.ports.metadata, vakr.ports.telemetry, vakr.ports.clock: Data and time.
    vakr.query.*: Requests, reads, markers, units, and views.

Safety:
    Read-only and synchronous; async callers use ``asyncio.to_thread``.
    Every read is bounded (E.19, E.28).

Example:
    >>> from vakr.adapters.memory import memory_telemetry_factory, memory_uow_factory
    >>> from vakr.domain.ids import InterfaceId, new_id
    >>> q = HistoryQueries(memory_uow_factory(), memory_telemetry_factory())
    >>> q.interface_graph(new_id(InterfaceId)) is None
    True
"""

from collections.abc import Callable
from dataclasses import replace

from vakr.domain.errors import StorageError
from vakr.domain.ids import DeviceId, InterfaceId
from vakr.domain.series import TimeWindow
from vakr.ports.clock import Clock, SystemClock
from vakr.ports.metadata import MetadataUnitOfWorkFactory
from vakr.ports.telemetry import TelemetryBackend
from vakr.query.graph_build import interface_graph_view, unavailable_graph
from vakr.query.history_context import (
    Subject,
    choose_collectors,
    interval_of,
    read_subject,
    window_view,
)
from vakr.query.history_limits import TIMELINE_MAX_SPAN
from vakr.query.history_requests import GraphKind, GraphRequest
from vakr.query.history_views import GraphView
from vakr.query.latency import latency_positions, notes_for
from vakr.query.latency_views import LatencyView, TimelineView
from vakr.query.settings import QuerySettings
from vakr.query.timeline import read_timeline
from vakr.query.units import MICROSECONDS, PERCENT, fixed

TIMELINE_NOTE = "Status comes from native samples only; rollups keep no interface status."


class HistoryQueries:
    """History reads over the ports."""

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

    def _subject(
        self, interface_id: InterfaceId | None, device_id: DeviceId | None
    ) -> Subject | None:
        """Read a graph's inventory in one unit of work.

        Args:
            interface_id: The interface, for interface views.
            device_id: The device, for device views.

        Returns:
            Subject | None: ``None`` when it does not exist.

        Raises:
            StorageError: If the metadata store cannot be read.
        """
        with self._metadata() as uow:
            return read_subject(uow, interface_id=interface_id, device_id=device_id)

    def _backend(self) -> TelemetryBackend:
        """Open the telemetry view.

        Args:
            None

        Returns:
            TelemetryBackend: The view.

        Raises:
            StorageError: If there is no telemetry store yet.
        """
        if self._telemetry is None:
            raise StorageError("no telemetry database yet", retryable=False)
        return self._telemetry()

    def interface_graph(
        self,
        interface_id: InterfaceId,
        kind: GraphKind = GraphKind.TRAFFIC,
        request: GraphRequest | None = None,
    ) -> GraphView | None:
        """Return an interface's traffic, packet, error, or utilization graph (12.5, 12.6).

        Args:
            interface_id: The interface.
            kind: Which graph.
            request: Range, source, collector, and point cap; defaults when ``None``.

        Returns:
            GraphView | None: ``None`` when the interface does not exist.

        Raises:
            ValidationError: If the range or source is out of bounds.
            StorageError: If the metadata store cannot be read.
        """
        request = request or GraphRequest()
        now = self._clock.now_utc()
        window, preset = request.range.resolve(now)
        subject = self._subject(interface_id, None)
        if subject is None:
            return None
        shown = window_view(window, preset, self._settings, subject.site)
        try:
            backend = self._backend()
            return interface_graph_view(
                backend, subject, kind, request, window, shown, self._settings, now
            )
        except StorageError as exc:
            return unavailable_graph(subject, kind, request, shown, now, _trouble(exc))

    def device_latency(
        self, device_id: DeviceId, request: GraphRequest | None = None
    ) -> LatencyView | None:
        """Return a device's reachability graph per position: smoke, median, loss (12.4, 12.6).

        Args:
            device_id: The device.
            request: Range, source, collector, and point cap; every position by default.

        Returns:
            LatencyView | None: ``None`` when the device does not exist.

        Raises:
            ValidationError: If the range or source is out of bounds.
            StorageError: If the metadata store cannot be read.
        """
        request = request or GraphRequest()
        now = self._clock.now_utc()
        window, preset = request.range.resolve(now)
        subject = self._subject(None, device_id)
        if subject is None:
            return None
        shown = window_view(window, preset, self._settings, subject.site)
        base = LatencyView(
            subject.device.id,
            subject.device.name,
            shown,
            request.source.value,
            fixed(MICROSECONDS, "ms", 1000.0),
            fixed(PERCENT, PERCENT, 1.0),
            (),
            False,
            True,
            None,
            now,
        )
        try:
            found, raw = latency_positions(
                self._backend(), subject, request, window, self._settings, now
            )
        except StorageError as exc:
            return replace(base, available=False, reason=_trouble(exc))
        if not found:
            return replace(base, available=False, reason="no such collector")
        cut = any(p.probes_truncated or p.median.truncated or p.loss.truncated for p in found)
        return replace(base, positions=tuple(found), truncated=cut, notes=notes_for(raw=raw))

    def interface_timeline(
        self, interface_id: InterfaceId, request: GraphRequest | None = None
    ) -> TimelineView | None:
        """Return an interface's ``admin/oper`` timeline from one position (12.5).

        Args:
            interface_id: The interface.
            request: Range and collector; the local (else first) position by default.

        Returns:
            TimelineView | None: ``None`` when the interface does not exist.

        Raises:
            ValidationError: If the range is out of bounds.
            StorageError: If the metadata store cannot be read.
        """
        request = request or GraphRequest()
        now = self._clock.now_utc()
        window, preset = request.range.resolve(now)
        window = TimeWindow(max(window.start, window.end - TIMELINE_MAX_SPAN), window.end)
        subject = self._subject(interface_id, None)
        if subject is None:
            return None
        shown = window_view(window, preset, self._settings, subject.site)
        chosen = choose_collectors(
            subject.collectors, request.collector_id, self._settings.local_collector, every=False
        )[:1]
        base = TimelineView(
            interface_id,
            subject.name,
            shown,
            None,
            "",
            (),
            False,
            False,
            "no collector",
            now,
            (TIMELINE_NOTE,),
        )
        if not chosen:
            return base
        try:
            backend = self._backend()
            interval = interval_of(backend, subject.device, chosen[0], self._settings, now)
            return read_timeline(backend, base, chosen[0], window, interval)
        except StorageError as exc:
            return replace(
                base, collector_id=chosen[0].id, position=chosen[0].position, reason=_trouble(exc)
            )


def _trouble(exc: StorageError) -> str:
    """Name storage trouble for the reader; never a statement about the target.

    Args:
        exc: The storage error.

    Returns:
        str: Such as ``"telemetry unavailable: no telemetry database yet"``.

    Raises:
        None
    """
    return f"telemetry unavailable: {exc}"
