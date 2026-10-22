"""``DashboardQueries``: a custom dashboard resolved into one bounded view (UI-004, DASH-010).

Each widget is answered by the query services the built-in pages use, so a
dashboard says exactly what those pages say: pinned interfaces through
``CatalogQueries.pinned``, interface selections through the interface list,
device selections through ``OverviewQueries.browse``, and rankings through
``RankingQueries``. Every part is bounded by its widget (Appendix E.31):
at most 12 widgets, 50 pinned interfaces, 100 selection rows, and two
rankings of at most 25 rows. All parts show one position (MPOS-008): the
one asked for, else the local collector, else the first.

Dependencies:
    vakr.query.overview, catalog_order, catalog_scope, listing, ranking_views,
    dashboard_views, dashboard_criteria: Services, requests, and views.
    vakr.ports.metadata: Reads the dashboard definition.

Safety:
    Read-only and bounded; telemetry trouble leaves rows with "—" and the
    reason (UI-014), never zeros.

Example:
    >>> from vakr.adapters.memory import memory_uow_factory
    >>> factory = memory_uow_factory()
    >>> from vakr.domain.ids import DashboardId, new_id
    >>> DashboardQueries(factory, OverviewQueries(factory, None)).view(new_id(DashboardId)) is None
    True
"""

from vakr.domain.dashboard_widgets import (
    DeviceSelection,
    InterfaceSelection,
    PinnedInterfaces,
    RankingKind,
    RankingWidget,
    Widget,
)
from vakr.domain.dashboards import Dashboard
from vakr.domain.ids import CollectorId, DashboardId
from vakr.ports.clock import Clock, SystemClock
from vakr.ports.metadata import MetadataUnitOfWorkFactory
from vakr.query.catalog_index import CatalogIndex
from vakr.query.catalog_order import OK, InterfaceListRequest
from vakr.query.dashboard_criteria import criteria_view, device_scope, interface_scope
from vakr.query.dashboard_views import (
    DashboardView,
    DeviceWidgetView,
    PinnedWidgetView,
    RankingWidgetView,
    SelectionWidgetView,
    WidgetView,
)
from vakr.query.history_limits import RankingWindow
from vakr.query.listing import DeviceListRequest
from vakr.query.overview import OverviewQueries
from vakr.query.ranking_views import RankingRequest
from vakr.query.views import Reading


class DashboardQueries:
    """Custom dashboards, resolved for display."""

    def __init__(
        self,
        metadata: MetadataUnitOfWorkFactory,
        overview: OverviewQueries,
        clock: Clock | None = None,
    ) -> None:
        """Bind the service.

        Args:
            metadata: Opens metadata units of work (for the definition).
            overview: The shared overview services (catalog, rankings, browsing).
            clock: Time source of ``generated_at``; give the overview's.

        Returns:
            None

        Raises:
            None
        """
        self._metadata = metadata
        self._overview = overview
        self._clock = clock or SystemClock()

    def view(
        self, dashboard_id: DashboardId, collector_id: CollectorId | None = None
    ) -> DashboardView | None:
        """Return a dashboard with every widget resolved.

        Args:
            dashboard_id: The dashboard.
            collector_id: Position whose current values to show.

        Returns:
            DashboardView | None: The view, or ``None`` when no such dashboard exists.

        Raises:
            StorageError: If the metadata store cannot be read.
        """
        with self._metadata() as uow:
            board = uow.dashboards.get(dashboard_id)
        return None if board is None else self.resolve(board, collector_id)

    def resolve(self, board: Dashboard, collector_id: CollectorId | None = None) -> DashboardView:
        """Resolve a dashboard definition (``view`` without the lookup).

        Args:
            board: The definition.
            collector_id: Position whose current values to show.

        Returns:
            DashboardView: The view.

        Raises:
            StorageError: If the metadata store cannot be read.
        """
        catalog = self._overview.catalog
        index = catalog.inventory()
        probe = catalog.pinned((), collector_id)  # the position every widget shows
        chosen = probe.collector_id
        parts = [self._widget(w, index, chosen) for w in board.widgets]
        views = tuple(view for view, _ in parts)
        trouble = next((r for _, r in parts if r.value != OK.value), OK)
        return DashboardView(
            dashboard_id=board.id, name=board.name, description=board.description,
            refresh_s=board.refresh_s, version=board.version, widgets=views,
            collector_id=chosen, position=probe.position, telemetry=trouble,
            truncated=index.truncated, generated_at=self._clock.now_utc(),
        )  # fmt: skip

    def _widget(
        self, widget: Widget, index: CatalogIndex, collector_id: CollectorId | None
    ) -> tuple[WidgetView, Reading]:
        """Resolve one widget.

        Args:
            widget: The widget.
            index: The inventory, for filter names.
            collector_id: The position.

        Returns:
            tuple[WidgetView, Reading]: The view and its telemetry state.

        Raises:
            StorageError: If the metadata store cannot be read.
        """
        catalog = self._overview.catalog
        match widget:
            case PinnedInterfaces():
                rows = catalog.pinned(widget.interfaces, collector_id)
                return PinnedWidgetView(widget.title, widget.graphs, rows.entries), rows.telemetry
            case InterfaceSelection():
                scope = interface_scope(widget.criteria)
                page = catalog.interface_list(
                    InterfaceListRequest(scope=scope, limit=widget.limit, collector_id=collector_id)
                )
                more = page.total - len(page.rows)
                view = SelectionWidgetView(widget.title, criteria_view(index, widget.criteria),
                                           page.rows, page.total, more)  # fmt: skip
                return view, page.telemetry
            case DeviceSelection():
                listed = self._overview.browse(device_scope(widget.criteria),
                                               DeviceListRequest(limit=widget.limit))  # fmt: skip
                more = listed.total - len(listed.rows)
                view = DeviceWidgetView(widget.title, criteria_view(index, widget.criteria),
                                        listed.rows, listed.counts, listed.total, more)  # fmt: skip
                return view, listed.health.telemetry
            case RankingWidget():
                return self._ranking(widget, index, collector_id), OK

    def _ranking(
        self, widget: RankingWidget, index: CatalogIndex, collector_id: CollectorId | None
    ) -> RankingWidgetView:
        """Resolve a ranking widget with the rankings page's exact computation.

        Args:
            widget: The widget.
            index: The inventory, for filter names.
            collector_id: The position.

        Returns:
            RankingWidgetView: The view.

        Raises:
            StorageError: If the metadata store cannot be read.
        """
        rankings = self._overview.rankings
        request = RankingRequest(RankingWindow(widget.window), widget.limit, collector_id,
                                 interface_scope(widget.criteria))  # fmt: skip
        criteria = criteria_view(index, widget.criteria)
        if widget.ranking is RankingKind.UTILIZATION:
            return RankingWidgetView(widget.title, widget.ranking.value, criteria,
                                     rankings.top_utilization(request), None)  # fmt: skip
        return RankingWidgetView(widget.title, widget.ranking.value, criteria, None,
                                 rankings.top_errors(request))  # fmt: skip
