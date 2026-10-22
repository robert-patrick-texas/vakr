"""``CatalogQueries``: the global interface list, high-priority and WAN views, search (12.1, 12.5).

Lists across devices are built from the bounded inventory index and, for
current values, one position's latest samples (``CurrentStateQuery``); the
position is the requested collector, else the local one, else the first
(one position per view, MPOS-008). Order is total and stable (API-005):

- ``priority`` (default): sticky, WAN, primary, display rank, then natural
  interface name (E.29 #6), then natural device name, then UUID, so
  high-priority interfaces come first (UI-017, DASH-001);
- ``name``: natural device name, then interface name, then UUID;
- ``traffic`` / ``utilization``: the higher direction, largest first, with
  interfaces without a value last (missing is never zero).

Paging is by offset over the ordered list with ``total`` and
``next_offset``; pages are 1 to 500 rows (E.19). Current values are read
only for the devices on the page unless the order needs them all.
``pinned`` answers chosen interfaces in a dashboard's order (UI-004).

Dependencies:
    vakr.ports.telemetry: Current interface state.
    vakr.query.*: Index, scope, rows, search, and views.

Safety:
    Read-only and bounded (E.19, E.29); telemetry trouble leaves the rows
    in place with "—" and the reason, never zeros.

Example:
    >>> from vakr.adapters.memory import memory_uow_factory
    >>> CatalogQueries(memory_uow_factory(), None).interface_list().total
    0
"""

from collections.abc import Callable, Sequence

from vakr.domain.checks import require_int
from vakr.domain.enums import AdminState, InterestClass, InterfaceLifecycle
from vakr.domain.ids import CollectorId, InterfaceId
from vakr.domain.interfaces import Interface
from vakr.domain.paging import MAX_PAGE_LIMIT
from vakr.ports.clock import Clock, SystemClock
from vakr.ports.metadata import MetadataUnitOfWorkFactory
from vakr.ports.telemetry import TelemetryBackend
from vakr.query.catalog_index import CatalogIndex, IndexCache
from vakr.query.catalog_order import (
    OK,
    InterfaceListRequest,
    InterfaceSort,
    check_text,
    list_rows,
    matches_text,
    ordered,
)
from vakr.query.catalog_scope import InterfaceScope, interface_ref
from vakr.query.catalog_views import InterfaceListView, SearchView
from vakr.query.dashboard_views import GONE, RETIRED, PinnedEntry, PinnedRows
from vakr.query.history_context import pick_collector
from vakr.query.history_limits import OVERVIEW_ROWS, SEARCH_HITS_PER_KIND
from vakr.query.search import search_index
from vakr.query.settings import QuerySettings


def _retired(index: CatalogIndex, iface: Interface) -> bool:
    """Tell whether an interface, or its device, is retired.

    Args:
        index: The inventory.
        iface: The interface.

    Returns:
        bool: True when either is retired, or the device is unknown.

    Raises:
        None
    """
    device = index.devices.get(iface.device_id)
    return (iface.lifecycle is InterfaceLifecycle.RETIRED or device is None
            or device.admin_state is AdminState.RETIRED)  # fmt: skip


class CatalogQueries:
    """Cross-device interface views and global search."""

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

    def interface_list(self, request: InterfaceListRequest | None = None) -> InterfaceListView:
        """Return one page of the interface list across every device (12.5).

        Args:
            request: Search, scope, sort, page, and position; defaults when ``None``.

        Returns:
            InterfaceListView: Rows, totals, and the position shown.

        Raises:
            StorageError: If the metadata store cannot be read.
        """
        request = request or InterfaceListRequest()
        now = self._clock.now_utc()
        index = self._index.get()
        needle = request.search.strip().casefold()
        chosen = [
            i
            for i in index.interfaces
            if request.scope.matches(index, i) and matches_text(index, i, needle)
        ]
        collector = pick_collector(
            index.collectors, request.collector_id, self._settings.local_collector
        )
        rows, telemetry = ordered(
            index,
            chosen,
            request.sort,
            request.offset,
            request.limit,
            list_rows(self._telemetry, collector, now, self._settings),
        )
        end = request.offset + len(rows)
        return InterfaceListView(
            rows=rows,
            total=len(chosen),
            offset=request.offset,
            limit=request.limit,
            next_offset=end if end < len(chosen) else None,
            truncated=index.truncated,
            collector_id=collector.id if collector else None,
            position=collector.position if collector else "",
            telemetry=telemetry,
            generated_at=now,
        )

    def high_priority(
        self, limit: int = OVERVIEW_ROWS, collector_id: CollectorId | None = None
    ) -> InterfaceListView:
        """Return the primary-interest interfaces in priority order (DASH-001, UI-008, META-011).

        Args:
            limit: Rows, 1 to 500.
            collector_id: Position whose current values to show.

        Returns:
            InterfaceListView: The first page of primary interfaces.

        Raises:
            ValidationError: If ``limit`` is out of range.
            StorageError: If the metadata store cannot be read.
        """
        scope = InterfaceScope(interest=InterestClass.PRIMARY)
        return self.interface_list(
            InterfaceListRequest(scope=scope, limit=limit, collector_id=collector_id)
        )

    def wan_circuits(
        self,
        scope: InterfaceScope | None = None,
        limit: int = MAX_PAGE_LIMIT,
        collector_id: CollectorId | None = None,
    ) -> InterfaceListView:
        """Return WAN circuits with provider, circuit, contracted rate, and state (DASH-002).

        Group them by ``ref.provider`` or ``ref.site`` for the provider and
        site views (UI-008); rows are in name order so groups stay stable.

        Args:
            scope: Further filters (site, provider, tag, ...); WAN only is always applied.
            limit: Rows, 1 to 500.
            collector_id: Position whose current values to show.

        Returns:
            InterfaceListView: WAN circuits.

        Raises:
            ValidationError: If ``limit`` is out of range.
            StorageError: If the metadata store cannot be read.
        """
        base = scope or InterfaceScope()
        wan = InterfaceScope(
            base.device_id,
            base.site_id,
            base.tag_id,
            base.group_id,
            base.provider,
            True,
            base.interest,
            base.lifecycle,
            base.include_retired,
        )
        return self.interface_list(
            InterfaceListRequest(
                scope=wan, sort=InterfaceSort.NAME, limit=limit, collector_id=collector_id
            )
        )

    def inventory(self) -> CatalogIndex:
        """Return the shared bounded inventory index (for names and filters).

        Args:
            None

        Returns:
            CatalogIndex: The index, reread only after a metadata change.

        Raises:
            StorageError: If the metadata store cannot be read.
        """
        return self._index.get()

    def pinned(
        self, ids: Sequence[InterfaceId], collector_id: CollectorId | None = None
    ) -> PinnedRows:
        """Return chosen interfaces in the order given, with current values (UI-004).

        A retired interface (or one on a retired device) keeps its place with
        its names and the note ``retired``; one the index does not hold keeps
        it with ``no longer exists``. Neither has values.

        Args:
            ids: Interface UUIDs, in display order (at most a widget's 50).
            collector_id: Position whose current values to show.

        Returns:
            PinnedRows: One entry per UUID, the telemetry state, and the position.

        Raises:
            StorageError: If the metadata store cannot be read.
        """
        index = self._index.get()
        by_id = {i.id: i for i in index.interfaces}
        collector = pick_collector(index.collectors, collector_id, self._settings.local_collector)
        live = [by_id[i] for i in ids if i in by_id and not _retired(index, by_id[i])]
        reader = list_rows(self._telemetry, collector, self._clock.now_utc(), self._settings)
        rows, telemetry = reader(index, live) if live else ([], OK)
        found = {r.ref.interface_id: r for r in rows}
        entries = tuple(
            PinnedEntry(i, found[i], None, None) if i in found
            else PinnedEntry(i, None, interface_ref(index, by_id[i]), RETIRED) if i in by_id
            else PinnedEntry(i, None, None, GONE)
            for i in ids
        )  # fmt: skip
        return PinnedRows(entries, telemetry, collector.id if collector else None,
                          collector.position if collector else "")  # fmt: skip

    def search(self, text: str, per_kind: int = SEARCH_HITS_PER_KIND) -> SearchView:
        """Search devices, interfaces, sites, providers, circuits, tags, and groups (12.1).

        Args:
            text: Search text, at most 256 characters; plain substring, no patterns.
            per_kind: Most hits per kind, 1 to 500.

        Returns:
            SearchView: Hits in kind order with totals.

        Raises:
            ValidationError: If the text is too long or ``per_kind`` out of range.
            StorageError: If the metadata store cannot be read.
        """
        check_text(text)
        require_int(per_kind, "per_kind", minimum=1, maximum=MAX_PAGE_LIMIT)
        return search_index(self._index.get(), text, per_kind, self._clock.now_utc())
