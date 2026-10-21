"""Query parameters shared by the list, history, and ranking endpoints (API-005, API-006, SEC-019).

Every parameter is bounded where it is declared, so FastAPI refuses an
oversized page, cursor, search text, or point count with the 422 envelope
before any service runs. The services check the same bounds again
(``ValidationError``), which covers combinations: a custom range shorter
than a minute, longer than 400 days, ending in the future, or without a
timezone is refused, never silently widened (API-T04).

Dependencies:
    fastapi: ``Query`` declarations.
    vakr.query: Request types, scopes, and enums.

Safety:
    Search text is a plain substring of at most 256 characters, never a
    pattern (SEC-019).

Example:
    >>> graph_request(range_=None, start=None, end=None).max_points
    1200
"""

from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import Query

from vakr.api.paging import DEFAULT_PAGE_LIMIT, MAX_CURSOR_LENGTH, MAX_PAGE_LIMIT
from vakr.domain.enums import AdminState, InterestClass, InterfaceLifecycle
from vakr.domain.ids import CollectorId, GroupId, SiteId, TagId
from vakr.domain.series import MAX_SERIES_POINTS
from vakr.query import (
    DeviceScope,
    GraphRequest,
    InterfaceScope,
    RangePreset,
    SourceChoice,
    TimeRange,
)
from vakr.query.history_limits import SEARCH_TEXT_MAX

Limit = Annotated[int, Query(ge=1, le=MAX_PAGE_LIMIT, description="Page size, 1 to 500.")]
Cursor = Annotated[
    str | None,
    Query(max_length=MAX_CURSOR_LENGTH, description="`page.next_cursor` of the previous page."),
]
Search = Annotated[
    str, Query(max_length=SEARCH_TEXT_MAX, description="Case-insensitive substring.")
]
Collector = Annotated[UUID | None, Query(description="Show this collector's (position's) values.")]
DEFAULT_LIMIT = DEFAULT_PAGE_LIMIT


def collector_id(value: UUID | None) -> CollectorId | None:
    """Type a collector parameter.

    Args:
        value: The UUID, if given.

    Returns:
        CollectorId | None: The typed ID.

    Raises:
        None
    """
    return CollectorId(value) if value is not None else None


def graph_request(
    range_: Annotated[
        RangePreset | None, Query(alias="range", description="Preset; 24h by default.")
    ] = None,
    start: Annotated[datetime | None, Query(description="Custom start, ISO 8601.")] = None,
    end: Annotated[datetime | None, Query(description="Custom end, ISO 8601.")] = None,
    source: Annotated[SourceChoice, Query(description="auto (10.6), native, or rollup.")] = (
        SourceChoice.AUTO
    ),
    max_points: Annotated[int, Query(ge=1, le=MAX_SERIES_POINTS)] = MAX_SERIES_POINTS,
    collector: Collector = None,
) -> GraphRequest:
    """Build a graph request from query parameters (API-006).

    Args:
        range_: A preset (``range=7d``); ignored when ``start`` and ``end`` are given.
        start: Custom range start.
        end: Custom range end.
        source: Tier choice.
        max_points: Points per series, 1 to 1,200.
        collector: Only this position's series.

    Returns:
        GraphRequest: The validated request.

    Raises:
        ValidationError: If ``max_points`` or ``source`` is out of range.
    """
    return GraphRequest(
        range=TimeRange(range_, start, end),
        source=source,
        collector_id=collector_id(collector),
        max_points=max_points,
    )


def interface_scope(
    site: Annotated[UUID | None, Query(description="Site UUID.")] = None,
    tag: Annotated[UUID | None, Query(description="Tag UUID.")] = None,
    group: Annotated[UUID | None, Query(description="Group UUID.")] = None,
    provider: Annotated[str | None, Query(max_length=SEARCH_TEXT_MAX)] = None,
    wan: Annotated[bool, Query(description="Only WAN circuits.")] = False,
    interest: Annotated[InterestClass | None, Query(description="primary = high priority.")] = None,
    lifecycle: InterfaceLifecycle | None = None,
    include_retired: bool = False,
) -> InterfaceScope:
    """Build an interface scope from query parameters (12.5, UI-009).

    Args:
        site: Only interfaces at this site.
        tag: Only interfaces with this tag, or on a device with it.
        group: Only interfaces of devices in this group.
        provider: Only circuits of this provider.
        wan: Only WAN circuits.
        interest: Only this interest class.
        lifecycle: Only this lifecycle state.
        include_retired: Include retired interfaces and devices.

    Returns:
        InterfaceScope: The scope.

    Raises:
        None
    """
    return InterfaceScope(
        site_id=SiteId(site) if site else None,
        tag_id=TagId(tag) if tag else None,
        group_id=GroupId(group) if group else None,
        provider=provider or None,
        wan_only=wan,
        interest=interest,
        lifecycle=lifecycle,
        include_retired=include_retired,
    )


def device_scope(
    site: Annotated[UUID | None, Query(description="Site UUID.")] = None,
    tag: Annotated[UUID | None, Query(description="Tag UUID.")] = None,
    group: Annotated[UUID | None, Query(description="Group UUID.")] = None,
    provider: Annotated[str | None, Query(max_length=SEARCH_TEXT_MAX)] = None,
    admin_state: AdminState | None = None,
) -> DeviceScope | None:
    """Build a device scope (UI-009), or ``None`` when no filter is given.

    Args:
        site: Only devices at this site.
        tag: Only devices with this tag.
        group: Only devices in this group.
        provider: Only devices with an interface of this provider.
        admin_state: Only devices in this admin state.

    Returns:
        DeviceScope | None: The scope, or ``None`` for the plain device list.

    Raises:
        None
    """
    if not (site or tag or group or provider or admin_state):
        return None
    return DeviceScope(
        site_id=SiteId(site) if site else None,
        tag_id=TagId(tag) if tag else None,
        group_id=GroupId(group) if group else None,
        provider=provider or None,
        admin_state=admin_state,
    )
