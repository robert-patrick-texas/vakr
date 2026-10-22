"""Dashboard widgets: what a custom dashboard shows, and their bounds (UI-004, DASH-006, DASH-010).

A custom dashboard is an ordered list of widgets. Each kind answers one
operator question and is bounded, so no dashboard can ask the query tier
for an unbounded amount of work (DASH-010, Appendix E.31):

- ``PinnedInterfaces``: interfaces an admin chose across devices, in the
  admin's order (UI-004 "pin interfaces across devices"); a table of
  current values and, optionally, graph cards;
- ``InterfaceSelection``: interfaces matching site, tag, group, provider,
  WAN, or interest filters (UI-004 "tags/groups for simplified views",
  DASH-006);
- ``DeviceSelection``: devices at a site, with a tag, or in a group;
- ``RankingWidget``: top utilization or top errors within a selection,
  with the same exact rankings as the rankings page (DASH-003, DASH-004).

Widgets refer to inventory by UUID only, never by name, so renaming a
site or an interface never breaks a dashboard. The filters live in
``vakr.domain.dashboard_filters`` and are re-exported here.

Dependencies:
    dataclasses, enum: Frozen records and the closed vocabularies.
    vakr.domain.checks, dashboard_filters, errors, ids: Checks and filters.

Safety:
    Titles are display text checked like names; nothing here is a secret.

Example:
    >>> from vakr.domain.ids import InterfaceId, new_id
    >>> PinnedInterfaces("Uplinks", (new_id(InterfaceId),)).kind.value
    'pinned_interfaces'
"""

from dataclasses import dataclass, field
from enum import StrEnum

from vakr.domain.checks import require_bool, require_enum, require_int, require_name, require_uuid
from vakr.domain.dashboard_filters import DeviceCriteria, InterfaceCriteria
from vakr.domain.errors import ValidationError
from vakr.domain.ids import InterfaceId

__all__ = [
    "DeviceCriteria", "DeviceSelection", "InterfaceCriteria", "InterfaceSelection",
    "PinnedInterfaces", "RankingKind", "RankingWidget", "Widget", "WidgetKind",
]  # fmt: skip

MAX_TITLE_LENGTH = 80
MAX_PINNED = 50
"""Interfaces one pinned widget holds (Appendix E.31)."""
MAX_SELECTION_ROWS = 100
DEFAULT_SELECTION_ROWS = 25
"""Rows a selection widget shows; more matches are counted, not listed (E.31)."""
MAX_RANKING_ROWS = 25
DEFAULT_RANKING_ROWS = 10
RANKING_WINDOWS = ("15m", "1h", "6h", "24h")
"""The ranking windows of DASH-003, as ``vakr.query`` names them."""


class WidgetKind(StrEnum):
    """The kinds of dashboard widget (widget schema 1)."""

    PINNED_INTERFACES = "pinned_interfaces"
    INTERFACE_SELECTION = "interface_selection"
    DEVICE_SELECTION = "device_selection"
    RANKING = "ranking"


class RankingKind(StrEnum):
    """Which ranking a ranking widget shows."""

    UTILIZATION = "utilization"
    ERRORS = "errors"


def _require_title(title: object) -> None:
    """Require a widget title: a name of at most ``MAX_TITLE_LENGTH`` characters.

    Args:
        title: Candidate title.

    Returns:
        None

    Raises:
        ValidationError: If it is empty, untrimmed, multi-line, or too long.
    """
    require_name(title, "title")
    if isinstance(title, str) and len(title) > MAX_TITLE_LENGTH:
        raise ValidationError(f"title must be at most {MAX_TITLE_LENGTH} characters", field="title")


@dataclass(frozen=True, slots=True)
class PinnedInterfaces:
    """Interfaces pinned across devices, in the admin's order (UI-004).

    Attributes:
        title: Heading shown above the widget.
        interfaces: 1 to ``MAX_PINNED`` distinct interface UUIDs.
        graphs: Also show a graph card per interface (lazy, E.31's 12-card bound).
        kind: Always ``PINNED_INTERFACES``.
    """

    title: str
    interfaces: tuple[InterfaceId, ...]
    graphs: bool = True
    kind: WidgetKind = field(default=WidgetKind.PINNED_INTERFACES, init=False)

    def __post_init__(self) -> None:
        """Validate the widget.

        Args:
            None

        Returns:
            None

        Raises:
            ValidationError: If the title or the interface list breaks its bounds.
        """
        _require_title(self.title)
        if not isinstance(self.interfaces, tuple) or not 1 <= len(self.interfaces) <= MAX_PINNED:
            raise ValidationError(
                f"interfaces must hold 1 to {MAX_PINNED} interface UUIDs", field="interfaces"
            )
        for iface in self.interfaces:
            require_uuid(iface, "interfaces")
        if len(set(self.interfaces)) != len(self.interfaces):
            raise ValidationError("an interface is pinned only once", field="interfaces")
        require_bool(self.graphs, "graphs")


@dataclass(frozen=True, slots=True)
class InterfaceSelection:
    """Interfaces matching criteria, in priority order (UI-004, DASH-006).

    Attributes:
        title: Heading shown above the widget.
        criteria: The filters; at least one must be set.
        limit: Rows shown, 1 to ``MAX_SELECTION_ROWS``.
        kind: Always ``INTERFACE_SELECTION``.
    """

    title: str
    criteria: InterfaceCriteria
    limit: int = DEFAULT_SELECTION_ROWS
    kind: WidgetKind = field(default=WidgetKind.INTERFACE_SELECTION, init=False)

    def __post_init__(self) -> None:
        """Validate the widget.

        Args:
            None

        Returns:
            None

        Raises:
            ValidationError: If a field breaks its rule, or no filter is set.
        """
        _require_title(self.title)
        if not isinstance(self.criteria, InterfaceCriteria):
            raise ValidationError("criteria must be interface criteria", field="criteria")
        if self.criteria == InterfaceCriteria():
            raise ValidationError(
                "an interface selection needs at least one filter; "
                "the interfaces page lists every interface",
                field="criteria",
            )
        require_int(self.limit, "limit", minimum=1, maximum=MAX_SELECTION_ROWS)


@dataclass(frozen=True, slots=True)
class DeviceSelection:
    """Devices matching criteria, in status order (7.7).

    Attributes:
        title: Heading shown above the widget.
        criteria: The filters; at least one must be set.
        limit: Rows shown, 1 to ``MAX_SELECTION_ROWS``.
        kind: Always ``DEVICE_SELECTION``.
    """

    title: str
    criteria: DeviceCriteria
    limit: int = DEFAULT_SELECTION_ROWS
    kind: WidgetKind = field(default=WidgetKind.DEVICE_SELECTION, init=False)

    def __post_init__(self) -> None:
        """Validate the widget.

        Args:
            None

        Returns:
            None

        Raises:
            ValidationError: If a field breaks its rule, or no filter is set.
        """
        _require_title(self.title)
        if not isinstance(self.criteria, DeviceCriteria):
            raise ValidationError("criteria must be device criteria", field="criteria")
        if self.criteria == DeviceCriteria():
            raise ValidationError(
                "a device selection needs at least one filter; the devices page lists every device",
                field="criteria",
            )
        require_int(self.limit, "limit", minimum=1, maximum=MAX_SELECTION_ROWS)


@dataclass(frozen=True, slots=True)
class RankingWidget:
    """Top utilization or top errors within a selection (DASH-003, DASH-004).

    Attributes:
        title: Heading shown above the widget.
        ranking: ``UTILIZATION`` or ``ERRORS``.
        criteria: Which interfaces may rank; empty criteria rank every interface.
        window: One of ``RANKING_WINDOWS``.
        limit: Rows, 1 to ``MAX_RANKING_ROWS``.
        kind: Always ``RANKING``.
    """

    title: str
    ranking: RankingKind
    criteria: InterfaceCriteria = field(default_factory=InterfaceCriteria)
    window: str = "15m"
    limit: int = DEFAULT_RANKING_ROWS
    kind: WidgetKind = field(default=WidgetKind.RANKING, init=False)

    def __post_init__(self) -> None:
        """Validate the widget.

        Args:
            None

        Returns:
            None

        Raises:
            ValidationError: If a field breaks its rule.
        """
        _require_title(self.title)
        require_enum(self.ranking, RankingKind, "ranking")
        if not isinstance(self.criteria, InterfaceCriteria):
            raise ValidationError("criteria must be interface criteria", field="criteria")
        if self.window not in RANKING_WINDOWS:
            raise ValidationError(
                f"window must be one of {', '.join(RANKING_WINDOWS)}", field="window"
            )
        require_int(self.limit, "limit", minimum=1, maximum=MAX_RANKING_ROWS)


type Widget = PinnedInterfaces | InterfaceSelection | DeviceSelection | RankingWidget
WIDGET_TYPES = (PinnedInterfaces, InterfaceSelection, DeviceSelection, RankingWidget)
