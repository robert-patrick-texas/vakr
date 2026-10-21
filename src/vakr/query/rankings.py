"""``RankingQueries``: top utilization and top errors over a window (12.2, DASH-003..005, UI-008).

Both rankings come from one ``RankingQuery.window_summaries`` scan of one
collector (MPOS-008: counters are never summed across collectors) and keep
the E.17 #13 rules:

- utilization ranks by the higher of the inbound and outbound nearest-rank
  p95 over the window, each divided by the interface's capacity; on a tie
  the inbound direction is reported. Only interfaces with a utilization
  denominator rank (E.17 #15: tunnels and other logical interfaces are
  excluded with the reason unless ``capacity_override_bps`` is set);
- errors rank by valid input plus output error deltas; errors per million
  packets only when every counted sample had packet deltas; interfaces with
  zero errors still rank, at the bottom;
- samples without a valid baseline never count; every row shows its
  coverage, and interfaces without usable samples are listed as excluded.

Windows are 15 minutes (the DASH-003 default), 1 hour, 6 hours, or 24
hours, ending on an E.28 alignment step; a scan is reused for the same
collector and aligned window (``RANKING_CACHE_ENTRIES``), so dashboard
refreshes do not repeat it. Create one service per process and share it.

Dependencies:
    vakr.ports.metadata, vakr.ports.telemetry: Inventory and the ranking port.
    vakr.logic.ranking: Expected samples and exclusion words.
    vakr.query.*: Index, scope, capacity, and views.

Safety:
    Read-only and bounded (one day at most, E.28); telemetry trouble makes
    the ranking unavailable with the reason, never empty or zero.

Example:
    >>> from vakr.adapters.memory import memory_telemetry_factory, memory_uow_factory
    >>> view = RankingQueries(memory_uow_factory(), memory_telemetry_factory()).top_errors()
    >>> view.available, view.reason
    (False, 'no collector has been registered yet')
"""

import threading
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import UTC, datetime

from vakr.domain.catalog import Collector
from vakr.domain.errors import StorageError
from vakr.domain.ids import CollectorId
from vakr.domain.series import TimeWindow
from vakr.ports.clock import Clock, SystemClock
from vakr.ports.metadata import MetadataUnitOfWorkFactory
from vakr.ports.telemetry import TelemetryBackend
from vakr.ports.telemetry.rankings import RankingScan
from vakr.query.catalog_index import CatalogIndex, IndexCache
from vakr.query.history_context import pick_collector
from vakr.query.history_fetch import history_of
from vakr.query.history_limits import (
    RANKING_ALIGN,
    RANKING_CACHE_ENTRIES,
    RANKING_SPANS,
    align_down,
)
from vakr.query.ranking_rows import error_rows, utilization_rows
from vakr.query.ranking_views import (
    ErrorRow,
    RankingRequest,
    RankingView,
    UtilizationRow,
    empty_view,
)
from vakr.query.settings import QuerySettings


@dataclass(frozen=True, slots=True)
class _Context:
    """Everything one ranking needs before its rows are built.

    Attributes:
        index: The inventory.
        collector: The ranked collector, if any.
        window: The aligned window.
        scan: The scan, when it could be read.
        computed_at: When the scan ran.
        base: The view to fill in.
    """

    index: CatalogIndex
    collector: Collector | None
    window: TimeWindow
    scan: RankingScan | None
    computed_at: datetime | None
    base: RankingView


class RankingQueries:
    """Top-N rankings over the ports, with a small scan cache."""

    def __init__(
        self,
        metadata: MetadataUnitOfWorkFactory,
        telemetry: Callable[[], TelemetryBackend] | None,
        settings: QuerySettings | None = None,
        clock: Clock | None = None,
        index: IndexCache | None = None,
    ) -> None:
        """Bind the service.

        Args:
            metadata: Opens a metadata unit of work.
            telemetry: Opens a telemetry view; ``None`` when no store exists yet.
            settings: Defaults and bounds.
            clock: Time source.
            index: Shared inventory index; a private one when ``None``.

        Returns:
            None

        Raises:
            None
        """
        self._telemetry = telemetry
        self._settings = settings or QuerySettings()
        self._clock = clock or SystemClock()
        self._index = index or IndexCache(metadata, self._settings)
        self._lock = threading.Lock()
        self._scans: OrderedDict[tuple, tuple[RankingScan, datetime]] = OrderedDict()

    def top_utilization(self, request: RankingRequest | None = None) -> RankingView[UtilizationRow]:
        """Rank interfaces by directional p95 utilization (DASH-003, E.17 #13, #15).

        Args:
            request: Window, limit, collector, and scope; defaults when ``None``.

        Returns:
            RankingView[UtilizationRow]: Rows, exclusions, and coverage.

        Raises:
            StorageError: If the metadata store cannot be read.
        """
        request = request or RankingRequest()
        ctx = self._context(request, "utilization")
        if ctx.scan is None or ctx.collector is None:
            return ctx.base
        rows, excluded, candidates = utilization_rows(
            ctx.index, ctx.scan, request, ctx.window, self._settings, ctx.collector.position
        )
        return replace(
            ctx.base,
            rows=rows,
            excluded=excluded[0],
            excluded_total=excluded[1],
            candidates=candidates,
        )

    def top_errors(self, request: RankingRequest | None = None) -> RankingView[ErrorRow]:
        """Rank interfaces by valid error deltas (DASH-004, E.17 #13).

        Args:
            request: Window, limit, collector, and scope; defaults when ``None``.

        Returns:
            RankingView[ErrorRow]: Rows, exclusions, and coverage.

        Raises:
            StorageError: If the metadata store cannot be read.
        """
        request = request or RankingRequest()
        ctx = self._context(request, "errors")
        if ctx.scan is None or ctx.collector is None:
            return ctx.base
        rows, excluded, candidates = error_rows(
            ctx.index, ctx.scan, request, ctx.window, self._settings, ctx.collector.position
        )
        return replace(
            ctx.base,
            rows=rows,
            excluded=excluded[0],
            excluded_total=excluded[1],
            candidates=candidates,
        )

    def _context(self, request: RankingRequest, kind: str) -> _Context:
        """Read the inventory, choose the collector, align the window, and scan.

        Args:
            request: The request.
            kind: ``utilization`` or ``errors``.

        Returns:
            _Context: The context; ``scan`` is ``None`` when unavailable.

        Raises:
            StorageError: If the metadata store cannot be read.
        """
        now = self._clock.now_utc()
        index = self._index.get()
        end = datetime.fromtimestamp(
            align_down(now.timestamp(), RANKING_ALIGN[request.window]), UTC
        )
        window = TimeWindow(end - RANKING_SPANS[request.window], end)
        collector = pick_collector(
            index.collectors, request.collector_id, self._settings.local_collector
        )
        base = empty_view(kind, request, window, collector, index.truncated, now, self._settings)
        if collector is None:
            reason = "no collector has been registered yet"
            return _Context(
                index, None, window, None, None, replace(base, available=False, reason=reason)
            )
        try:
            scan, at = self._scan(collector.id, window)
        except StorageError as exc:
            reason = f"telemetry unavailable: {exc}"
            return _Context(
                index, collector, window, None, None, replace(base, available=False, reason=reason)
            )
        done = replace(base, computed_at=at, truncated=base.truncated or scan.truncated)
        return _Context(index, collector, window, scan, at, done)

    def _scan(self, collector: CollectorId, window: TimeWindow) -> tuple[RankingScan, datetime]:
        """Return the scan for one collector and aligned window, reusing a cached one.

        Args:
            collector: The collector.
            window: The aligned window.

        Returns:
            tuple: The scan and when it ran.

        Raises:
            StorageError: If there is no telemetry store or it cannot be read.
        """
        key = (collector, window.start, window.end)
        with self._lock:
            if key in self._scans:
                self._scans.move_to_end(key)
                return self._scans[key]
        if self._telemetry is None:
            raise StorageError("no telemetry database yet", retryable=False)
        scan = history_of(self._telemetry()).window_summaries(collector, window)
        found = (scan, self._clock.now_utc())
        with self._lock:
            self._scans[key] = found
            while len(self._scans) > RANKING_CACHE_ENTRIES:
                self._scans.popitem(last=False)
        return found
