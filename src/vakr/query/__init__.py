"""Backend-neutral current-state query services and view models (blueprint 6.5, TUI-007).

``StatusQueries`` answers the two questions an operator asks first: which
devices need attention right now, and what does this one device look like.
It reads only through the metadata and telemetry ports, applies the
measurement rules in ``vakr.logic`` (E.2 status, E.3 freshness, RATE-008
capacity), and returns immutable, presentation-ready views. ``vakr status``
and ``vakr tui`` both use it, so the CLI and the TUI can never disagree.

Modules:
    service: ``StatusQueries``, the facade.
    listing: ``DeviceListRequest``, search, filter, sort (7.7, E.29).
    snapshot: Bounded reads of inventory and current state.
    evidence: One collector's state to E.2 evidence and status.
    device_rows, interface_rows, detail: Rows and device-screen facts.
    views, detail_views: The view models.
    labels, text: Operator words and number formats.
    settings: Defaults and bounds (E.29).

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

from vakr.query.detail_views import DeviceDetailView, InterfaceRow
from vakr.query.listing import DeviceListRequest, DeviceSort
from vakr.query.service import StatusQueries
from vakr.query.settings import QueryLimits, QuerySettings, settings_from_config
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
    "CollectorHealthView",
    "DeviceDetailView",
    "DeviceListRequest",
    "DeviceListView",
    "DeviceRow",
    "DeviceSort",
    "HealthView",
    "InterfaceRow",
    "PositionView",
    "QueryLimits",
    "QuerySettings",
    "Reading",
    "StatusQueries",
    "StatusView",
    "settings_from_config",
]
