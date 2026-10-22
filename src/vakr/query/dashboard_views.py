"""View models of a custom dashboard: each widget resolved, with current values (UI-004, DASH-T06).

``GET /api/v1/dashboards/{uuid}/view`` answers one ``DashboardView``: the
dashboard's facts and one view per widget, in the admin's order, so a page
refresh is one bounded request plus the graph cards it shows lazily. Each
widget view carries ``kind`` so a client can tell them apart.

- ``PinnedWidgetView``: one entry per pinned interface, in order. An
  interface that is retired, or that no longer exists, keeps its place
  with a ``note`` and no values: it never disappears silently and never
  fails the dashboard.
- ``SelectionWidgetView`` / ``DeviceWidgetView``: the first ``limit``
  matches, the total, and how many more there are.
- ``RankingWidgetView``: a ranking within the selection, exactly as the
  rankings page computes it.

Missing values stay missing (``Reading`` with a reason), never zero.

Dependencies:
    vakr.query.catalog_views, ranking_views, views: The row and ranking views.

Safety:
    Views carry display names and measurements only.

Example:
    >>> CriteriaView(None, None, None, None, False, None, "every interface").text
    'every interface'
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from vakr.domain.ids import CollectorId, DashboardId, InterfaceId
from vakr.query.catalog_views import InterfaceListRow, InterfaceRef
from vakr.query.ranking_views import ErrorRow, RankingView, UtilizationRow
from vakr.query.views import BucketCounts, DeviceRow, Reading

GONE = "no longer exists"
RETIRED = "retired"


@dataclass(frozen=True, slots=True)
class CriteriaView:
    """A widget's filters, with the names they refer to (for headings and kiosk text).

    Attributes:
        site: Site name, if filtered by site.
        tag: Tag name, if filtered by tag.
        group: Group name, if filtered by group.
        provider: Provider, if filtered by provider.
        wan_only: Only WAN circuits.
        interest: ``primary`` or ``secondary``, if filtered by interest.
        text: The whole filter in words, such as ``site Dallas DC1, WAN only``.
    """

    site: str | None
    tag: str | None
    group: str | None
    provider: str | None
    wan_only: bool
    interest: str | None
    text: str


@dataclass(frozen=True, slots=True)
class PinnedEntry:
    """One pinned interface in its place.

    Attributes:
        interface_id: The pinned UUID.
        row: Current values, or ``None`` when retired or gone.
        ref: The interface's names when retired (``None`` when gone or when ``row`` is set).
        note: ``retired`` or ``no longer exists``; ``None`` for a live interface.
    """

    interface_id: InterfaceId
    row: InterfaceListRow | None
    ref: InterfaceRef | None
    note: str | None


@dataclass(frozen=True, slots=True)
class PinnedRows:
    """Pinned interfaces with one position's current values (``CatalogQueries.pinned``).

    Attributes:
        entries: One per pinned UUID, in the order asked.
        telemetry: ``ok``, or why current values are unavailable.
        collector_id: The position whose values are shown, if any.
        position: Its label.
    """

    entries: tuple[PinnedEntry, ...]
    telemetry: Reading
    collector_id: CollectorId | None
    position: str


@dataclass(frozen=True, slots=True)
class PinnedWidgetView:
    """A pinned-interfaces widget (UI-004).

    Attributes:
        title: Heading.
        graphs: Whether the page shows graph cards (lazy, at most 12 per page, E.31).
        entries: The interfaces in the admin's order.
        kind: ``pinned_interfaces``.
    """

    title: str
    graphs: bool
    entries: tuple[PinnedEntry, ...]
    kind: Literal["pinned_interfaces"] = "pinned_interfaces"


@dataclass(frozen=True, slots=True)
class SelectionWidgetView:
    """An interface-selection widget (UI-004, DASH-006).

    Attributes:
        title: Heading.
        criteria: The filters in words.
        rows: The first ``limit`` matches, in priority order.
        total: Matches in all.
        more: Matches not shown (``total - len(rows)``).
        kind: ``interface_selection``.
    """

    title: str
    criteria: CriteriaView
    rows: tuple[InterfaceListRow, ...]
    total: int
    more: int
    kind: Literal["interface_selection"] = "interface_selection"


@dataclass(frozen=True, slots=True)
class DeviceWidgetView:
    """A device-selection widget, in status order (7.7).

    Attributes:
        title: Heading.
        criteria: The filters in words.
        rows: The first ``limit`` matching devices.
        counts: Attention, Active, and Offline among all matches.
        total: Matches in all.
        more: Matches not shown.
        kind: ``device_selection``.
    """

    title: str
    criteria: CriteriaView
    rows: tuple[DeviceRow, ...]
    counts: BucketCounts
    total: int
    more: int
    kind: Literal["device_selection"] = "device_selection"


@dataclass(frozen=True, slots=True)
class RankingWidgetView:
    """A ranking widget: one of ``utilization`` and ``errors`` is set (DASH-003, DASH-004).

    Attributes:
        title: Heading.
        ranking: ``utilization`` or ``errors``.
        criteria: Which interfaces could rank, in words.
        utilization: The top-utilization ranking, for ``utilization``.
        errors: The top-errors ranking, for ``errors``.
        kind: ``ranking``.
    """

    title: str
    ranking: str
    criteria: CriteriaView
    utilization: RankingView[UtilizationRow] | None
    errors: RankingView[ErrorRow] | None
    kind: Literal["ranking"] = "ranking"


type WidgetView = PinnedWidgetView | SelectionWidgetView | DeviceWidgetView | RankingWidgetView


@dataclass(frozen=True, slots=True)
class DashboardView:
    """A custom dashboard with every widget resolved (UI-004, DASH-T06).

    Attributes:
        dashboard_id: The dashboard.
        name: Its name.
        description: Its description.
        refresh_s: Seconds between refreshes the page should use.
        version: The definition's version (DB-003).
        widgets: One view per widget, in order.
        collector_id: The position whose current values are shown, if any (MPOS-008).
        position: Its label.
        telemetry: ``ok``, or why current values are unavailable (UI-014).
        truncated: True when the inventory was larger than one index reads (E.29).
        generated_at: When the view was computed (UTC).
    """

    dashboard_id: DashboardId
    name: str
    description: str
    refresh_s: int
    version: int
    widgets: tuple[WidgetView, ...]
    collector_id: CollectorId | None
    position: str
    telemetry: Reading
    truncated: bool
    generated_at: datetime
