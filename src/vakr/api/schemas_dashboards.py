"""Shapes of the dashboard endpoints: bodies, definitions, and list rows (UI-004, DASH-007).

Widget bodies are the widget JSON of ``vakr.domain.dashboard_codec``
(schema 1), declared here so OpenAPI, and the web UI's generated types,
know them; the codec stays the one place their rules live, so the API and
the stored rows can never disagree. Unknown fields are refused rather
than ignored, so a typo never silently drops a filter.

Dependencies:
    pydantic: Body models.
    vakr.api.paging: Paging facts.
    vakr.domain: Bounds and the codec.
    vakr.query.jsonable: Time formatting (TIME-004).

Safety:
    Definitions hold display names and UUIDs only.

Example:
    >>> DashboardCreate(name="NOC").refresh_s
    60
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from vakr.api.paging import PageInfo
from vakr.domain import dashboard_widgets as w
from vakr.domain import dashboards as d
from vakr.domain.dashboard_codec import widgets_to_json
from vakr.domain.dashboard_filters import MAX_PROVIDER_LENGTH
from vakr.domain.dashboards import Dashboard
from vakr.query.jsonable import iso_utc

Title = Annotated[str, Field(min_length=1, max_length=w.MAX_TITLE_LENGTH)]
_STRICT = ConfigDict(extra="forbid")


class InterfaceCriteriaIn(BaseModel):
    """Which interfaces a selection or ranking covers; absent fields do not filter."""

    model_config = _STRICT
    site_id: UUID | None = None
    tag_id: UUID | None = None
    group_id: UUID | None = None
    provider: str | None = Field(default=None, min_length=1, max_length=MAX_PROVIDER_LENGTH)
    wan_only: bool = False
    interest: Literal["primary", "secondary"] | None = None


class DeviceCriteriaIn(BaseModel):
    """Which devices a device selection covers; absent fields do not filter."""

    model_config = _STRICT
    site_id: UUID | None = None
    tag_id: UUID | None = None
    group_id: UUID | None = None


class PinnedInterfacesIn(BaseModel):
    """Interfaces pinned across devices, in this order (UI-004)."""

    model_config = _STRICT
    kind: Literal["pinned_interfaces"]
    title: Title
    interfaces: list[UUID] = Field(min_length=1, max_length=w.MAX_PINNED)
    graphs: bool = True


class InterfaceSelectionIn(BaseModel):
    """Interfaces matching filters (UI-004, DASH-006); at least one filter."""

    model_config = _STRICT
    kind: Literal["interface_selection"]
    title: Title
    criteria: InterfaceCriteriaIn
    limit: int = Field(default=w.DEFAULT_SELECTION_ROWS, ge=1, le=w.MAX_SELECTION_ROWS)


class DeviceSelectionIn(BaseModel):
    """Devices matching filters, in status order; at least one filter."""

    model_config = _STRICT
    kind: Literal["device_selection"]
    title: Title
    criteria: DeviceCriteriaIn
    limit: int = Field(default=w.DEFAULT_SELECTION_ROWS, ge=1, le=w.MAX_SELECTION_ROWS)


class RankingIn(BaseModel):
    """Top utilization or top errors within filters (DASH-003, DASH-004)."""

    model_config = _STRICT
    kind: Literal["ranking"]
    title: Title
    ranking: Literal["utilization", "errors"]
    criteria: InterfaceCriteriaIn = Field(default_factory=InterfaceCriteriaIn)
    window: Literal["15m", "1h", "6h", "24h"] = "15m"
    limit: int = Field(default=w.DEFAULT_RANKING_ROWS, ge=1, le=w.MAX_RANKING_ROWS)


WidgetIn = Annotated[
    PinnedInterfacesIn | InterfaceSelectionIn | DeviceSelectionIn | RankingIn,
    Field(discriminator="kind"),
]
Name = Annotated[str, Field(min_length=1, max_length=d.MAX_NAME_LENGTH)]
Description = Annotated[str, Field(max_length=d.MAX_DESCRIPTION_LENGTH)]
Refresh = Annotated[int, Field(ge=d.MIN_REFRESH_S, le=d.MAX_REFRESH_S)]
Widgets = Annotated[list[WidgetIn], Field(max_length=d.MAX_WIDGETS)]


class DashboardCreate(BaseModel):
    """Body of ``POST /api/v1/dashboards``."""

    model_config = _STRICT
    name: Name
    description: Description = ""
    refresh_s: Refresh = d.DEFAULT_REFRESH_S
    widgets: Widgets = Field(default_factory=list)


class DashboardPatch(BaseModel):
    """Body of ``PATCH /api/v1/dashboards/{uuid}``: ``version`` plus what changes."""

    model_config = _STRICT
    version: int = Field(ge=1, description="The version this change is based on (DB-003).")
    name: Name | None = None
    description: Description | None = None
    refresh_s: Refresh | None = None
    widgets: Widgets | None = None


class DashboardOut(BaseModel):
    """A dashboard definition, as ``GET``, ``POST``, and ``PATCH`` answer it."""

    id: UUID
    name: str
    description: str
    refresh_s: int
    widgets: list[WidgetIn]
    widget_schema: int
    managed_by: Literal["api", "file"]
    visibility: Literal["private"]
    created_by: str
    created_at: datetime
    updated_by: str
    updated_at: datetime
    version: int


def widgets_json(widgets: list[Any]) -> list[Any]:
    """Return body widgets as plain codec JSON (absent fields left out).

    Args:
        widgets: Validated body widgets.

    Returns:
        list[Any]: One object per widget.

    Raises:
        None
    """
    return [x.model_dump(mode="json", exclude_none=True) for x in widgets]


def definition(board: Dashboard) -> dict[str, Any]:
    """Return a dashboard as the ``DashboardOut`` JSON object.

    Args:
        board: The dashboard.

    Returns:
        dict[str, Any]: Its definition, times in UTC ``Z`` (TIME-004).

    Raises:
        None
    """
    return {
        "id": str(board.id), "name": board.name, "description": board.description,
        "refresh_s": board.refresh_s, "widgets": widgets_to_json(board.widgets),
        "widget_schema": d.WIDGET_SCHEMA, "managed_by": board.managed_by.kind.value,
        "visibility": board.visibility.value, "created_by": board.created_by,
        "created_at": iso_utc(board.created_at), "updated_by": board.updated_by,
        "updated_at": iso_utc(board.updated_at), "version": board.version,
    }  # fmt: skip


@dataclass(frozen=True, slots=True)
class DashboardSummary:
    """One dashboard in the list.

    Attributes:
        id: The dashboard.
        name: Its name.
        description: Its description.
        widgets: How many widgets it has.
        refresh_s: Seconds between refreshes.
        updated_by: Who changed it last.
        updated_at: When (UTC).
        version: Its version.
    """

    id: UUID
    name: str
    description: str
    widgets: int
    refresh_s: int
    updated_by: str
    updated_at: datetime
    version: int

    @classmethod
    def of(cls, board: Dashboard) -> DashboardSummary:
        """Summarise a dashboard.

        Args:
            board: The dashboard.

        Returns:
            DashboardSummary: The row.

        Raises:
            None
        """
        return cls(board.id, board.name, board.description, len(board.widgets),
                   board.refresh_s, board.updated_by, board.updated_at, board.version)  # fmt: skip


@dataclass(frozen=True, slots=True)
class DashboardPage:
    """One page of dashboards, by name (API-005).

    Attributes:
        rows: The dashboards.
        page: Paging facts.
    """

    rows: tuple[DashboardSummary, ...]
    page: PageInfo
