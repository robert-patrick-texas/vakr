"""Backend-neutral query services and view models (blueprint 6.5, 10.6, 12, TUI-007).

``StatusQueries`` answers the two questions an operator asks first: which
devices need attention right now, and what does this one device look like.
It reads only through the metadata and telemetry ports, applies the
measurement rules in ``vakr.logic`` (E.2 status, E.3 freshness, RATE-008
capacity), and returns immutable, presentation-ready views. ``vakr status``
and ``vakr tui`` both use it, so the CLI and the TUI can never disagree.

The M4 read services behind the API and web UI are ``HistoryQueries``
(graphs, latency smoke, state timelines), ``RankingQueries`` (top
utilization and top errors), ``CatalogQueries`` (global interface list,
WAN and high-priority views, global search), and ``OverviewQueries`` (the
overview dashboard and grouped device browsing). Their views serialise with
``to_jsonable`` and come back with ``from_jsonable``, which is how
``vakr status`` and ``vakr tui`` read them from ``vakr-api`` (``StatusSource``);
``docs/architecture/queries.md`` describes them.

Modules:
    service: ``StatusQueries``, the facade.
    listing: ``DeviceListRequest``, search, filter, sort (7.7, E.29).
    snapshot: Bounded reads of inventory and current state.
    evidence: One collector's state to E.2 evidence and status.
    device_rows, interface_rows, detail: Rows and device-screen facts.
    views, detail_views: The view models.
    labels, text: Operator words and number formats.
    settings: Defaults and bounds (E.29).
    history, graph_build, latency, timeline, history_*: Graph reads (10.6, E.19).
    rankings, ranking_rows, ranking_views: Rankings (E.17 #13, #15).
    catalog, catalog_*, search: Cross-device lists and search (12.1, 12.5).
    overview, overview_*: The overview dashboard (12.2).
    export, jsonable, units, zone: Export, JSON, SI units, display zone.
    from_json, from_json_leaves, source: Views back from JSON; ``StatusSource`` (E.29 #10).

Dependencies:
    vakr.ports, vakr.logic, vakr.domain: Data access, rules, and vocabulary.

Safety:
    Read-only and synchronous. Never imports a storage driver or adapter
    (ARCH-001, TUI-T05); async callers use ``asyncio.to_thread``. Missing data
    is shown as "—" with a reason, never as zero (invariant 1).

Example:
    >>> from vakr.adapters.memory import memory_uow_factory
    >>> StatusQueries(memory_uow_factory(), None).device_list().health.telemetry.value
    'unavailable'
"""

from vakr.query.catalog import CatalogQueries
from vakr.query.catalog_index import IndexCache
from vakr.query.catalog_order import InterfaceListRequest, InterfaceSort
from vakr.query.catalog_scope import InterfaceScope
from vakr.query.detail_views import DeviceDetailView, InterfaceRow
from vakr.query.export import ExportFormat, export_view
from vakr.query.from_json import ContractError, from_jsonable
from vakr.query.history import HistoryQueries
from vakr.query.history_limits import RangePreset, RankingWindow
from vakr.query.history_requests import GraphKind, GraphRequest, SourceChoice, TimeRange
from vakr.query.jsonable import to_jsonable
from vakr.query.listing import DeviceListRequest, DeviceSort, FreshnessFilter
from vakr.query.overview import DeviceScope, OverviewQueries
from vakr.query.ranking_views import RankingRequest
from vakr.query.rankings import RankingQueries
from vakr.query.service import StatusQueries
from vakr.query.settings import QueryLimits, QuerySettings, settings_from_config
from vakr.query.source import SourceUnavailableError, StatusSource
from vakr.query.views import (
    DASH,
    BucketCounts,
    CollectorHealthView,
    DeviceListView,
    DeviceRow,
    HealthView,
    PositionView,
    Reading,
    StatusView,
)

__all__ = [
    "DASH",
    "BucketCounts",
    "CatalogQueries",
    "CollectorHealthView",
    "ContractError",
    "DeviceDetailView",
    "DeviceListRequest",
    "DeviceListView",
    "DeviceRow",
    "DeviceScope",
    "DeviceSort",
    "ExportFormat",
    "FreshnessFilter",
    "GraphKind",
    "GraphRequest",
    "HealthView",
    "HistoryQueries",
    "IndexCache",
    "InterfaceListRequest",
    "InterfaceRow",
    "InterfaceScope",
    "InterfaceSort",
    "OverviewQueries",
    "PositionView",
    "QueryLimits",
    "QuerySettings",
    "RangePreset",
    "RankingQueries",
    "RankingRequest",
    "RankingWindow",
    "Reading",
    "SourceChoice",
    "SourceUnavailableError",
    "StatusQueries",
    "StatusSource",
    "StatusView",
    "TimeRange",
    "export_view",
    "from_jsonable",
    "settings_from_config",
    "to_jsonable",
]
