"""View models of the authenticated overview dashboard (12.2, UI-008, UI-014, UI-015).

The overview gathers the 12.2 widgets in one immutable answer: device
state counts with freshness, high-priority interfaces, WAN circuits, top
utilization and top errors, collector/spool/database health, recent
events, and the quick-filter choices. An event source that does not exist
yet is an ``EventFeed`` with ``available=False`` and the reason, never an
empty list that would read as "nothing happened" (UI-014).

Dependencies:
    vakr.query.views, catalog_views, ranking_views: Reused views.

Safety:
    Names, identifiers, counts, and measurements only.

Example:
    >>> EventFeed("rediscovery", False, "not recorded yet", (), 0).available
    False
"""

from dataclasses import dataclass
from datetime import datetime

from vakr.domain.ids import DeviceId
from vakr.query.catalog_views import InterfaceListView
from vakr.query.ranking_views import ErrorRow, RankingView, UtilizationRow
from vakr.query.views import BucketCounts, HealthView


@dataclass(frozen=True, slots=True)
class StateCount:
    """How many devices show one status label (E.2).

    Attributes:
        label: Status text, such as ``Down`` or ``Unknown — collector unavailable``.
        bucket: ``attention``, ``active``, or ``offline``.
        color: Display colour; never shown without the label and icon (12.8).
        icon: Shape that differs per meaning.
        count: Devices with this label.
    """

    label: str
    bucket: str
    color: str
    icon: str
    count: int


@dataclass(frozen=True, slots=True)
class DeviceSummary:
    """Device state counts with freshness (12.2 widget 1, TIME-005).

    Attributes:
        total: Devices counted (retired ones excluded).
        counts: Per bucket.
        by_status: Per status label, most severe first.
        fresh: Devices whose newest evidence is fresh (E.3).
        stale: Devices whose newest evidence is stale.
        never: Devices with no evidence at all.
        truncated: True when more devices exist than one overview reads (E.29).
    """

    total: int
    counts: BucketCounts
    by_status: tuple[StateCount, ...]
    fresh: int
    stale: int
    never: int
    truncated: bool


@dataclass(frozen=True, slots=True)
class EventRow:
    """One recent event.

    Attributes:
        kind: ``restart``, ``rediscovery``, ``identity_review``, or ``telemetry_loss``.
        at: When it happened or was detected (UTC).
        subject: What it concerns, such as a device name or a spool.
        device_id: The device, when the event concerns one.
        position: The position that saw it, when known.
        text: Operator words.
    """

    kind: str
    at: datetime
    subject: str
    device_id: DeviceId | None
    position: str
    text: str


@dataclass(frozen=True, slots=True)
class EventFeed:
    """Recent events of one kind, or why they cannot be shown (12.2 widget 7).

    Attributes:
        kind: The event kind.
        available: False when no store records this kind of event yet.
        reason: Why not, or what the feed covers.
        rows: Newest first, at most ``EVENTS_SHOWN``.
        total: Events found before the cap.
    """

    kind: str
    available: bool
    reason: str | None
    rows: tuple[EventRow, ...]
    total: int


@dataclass(frozen=True, slots=True)
class FilterOptions:
    """Quick-filter choices (12.2 widget 8, UI-009).

    Attributes:
        providers: Provider names on interfaces, in natural order.
        sites: ``(site UUID, name)`` pairs.
        tags: ``(tag UUID, name)`` pairs.
        groups: ``(group UUID, name)`` pairs.
        wan_circuits: How many interfaces are WAN circuits.
    """

    providers: tuple[str, ...]
    sites: tuple[tuple[str, str], ...]
    tags: tuple[tuple[str, str], ...]
    groups: tuple[tuple[str, str], ...]
    wan_circuits: int


@dataclass(frozen=True, slots=True)
class OverviewView:
    """The authenticated overview (12.2).

    Attributes:
        devices: Device state summary with freshness.
        health: Metadata, telemetry, and per-collector health (spool fill included).
        high_priority: Primary-interest interfaces with current values.
        wan: WAN circuits with provider, circuit, contracted rate, and state.
        top_utilization: Top utilization for the selected window.
        top_errors: Top errors for the selected window.
        events: Restart, rediscovery, identity-review, and telemetry-loss feeds.
        filters: Quick-filter choices.
        generated_at: When the view was computed (UTC).
    """

    devices: DeviceSummary
    health: HealthView
    high_priority: InterfaceListView
    wan: InterfaceListView
    top_utilization: RankingView[UtilizationRow]
    top_errors: RankingView[ErrorRow]
    events: tuple[EventFeed, ...]
    filters: FilterOptions
    generated_at: datetime
