"""The overview, rankings, high-priority and WAN views, and global search (12.1, 12.2, UI-008).

- ``GET /api/v1/overview``: the overview dashboard: device counts per
  bucket and status with freshness, storage and collector health,
  high-priority and WAN rows, top utilization and top errors, the event
  feeds, and quick-filter choices (12.2). Each feed says when it has no
  source instead of looking empty (UI-014).
- ``GET /api/v1/rankings/utilization`` and ``/rankings/errors``: top N by
  the higher directional p95 utilization (DASH-003) or by valid error
  deltas (DASH-004) over ``15m`` (default), ``1h``, ``6h``, or ``24h``, from
  one collector (MPOS-008), with coverage and the excluded interfaces and
  why (DASH-005).
- ``GET /api/v1/high-priority``: primary-interest interfaces (DASH-001).
- ``GET /api/v1/wan-circuits``: WAN circuits with provider, circuit, and
  contracted rate, in name order; group by ``ref.provider`` or ``ref.site``
  (DASH-002).
- ``GET /api/v1/search?q=``: plain substring search over devices,
  interfaces, sites, providers, circuits, tags, and groups; interface hits
  carry the UUIDs that open full history (UI-017).

All need the operator role.

Dependencies:
    fastapi: Routing.
    vakr.query: ``OverviewQueries`` with its catalog and ranking services.

Safety:
    Ranking windows are at most 1 day, rows at most 100, search text at most
    256 characters, hits at most 500 per kind (E.28, SEC-019).

Example:
    >>> any(r.path == "/overview" for r in router.routes)
    True
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Query
from fastapi.responses import JSONResponse

from vakr.api.context import ApiContext, call, get_context
from vakr.api.paging import offset_of
from vakr.api.params import (
    DEFAULT_LIMIT,
    Collector,
    Cursor,
    Limit,
    collector_id,
    interface_scope,
)
from vakr.api.principals import Viewer
from vakr.api.responses import paged, view_response
from vakr.api.schemas import InterfaceListPage
from vakr.domain.enums import InterestClass
from vakr.domain.paging import MAX_PAGE_LIMIT
from vakr.query import (
    InterfaceListRequest,
    InterfaceScope,
    InterfaceSort,
    RankingRequest,
    RankingWindow,
)
from vakr.query.catalog_views import SearchView
from vakr.query.history_limits import SEARCH_HITS_PER_KIND, SEARCH_TEXT_MAX, TOP_DEFAULT, TOP_MAX
from vakr.query.overview_views import OverviewView
from vakr.query.ranking_views import ErrorRow, RankingView, UtilizationRow

router = APIRouter(tags=["dashboards"])
Context = Annotated[ApiContext, Depends(get_context)]
Window = Annotated[RankingWindow, Query(description="15m (default), 1h, 6h, or 24h.")]
Top = Annotated[int, Query(ge=1, le=TOP_MAX, description="Rows, 1 to 100.")]
Scope = Annotated[InterfaceScope, Depends(interface_scope)]


@router.get("/overview", response_model=OverviewView, summary="Overview dashboard (12.2)")
async def overview(_: Viewer, context: Context, window: Window = RankingWindow.M15) -> JSONResponse:
    """Return the overview dashboard.

    Args:
        _: The caller (operator or admin).
        context: The services.
        window: Window of the ranking widgets.

    Returns:
        JSONResponse: ``OverviewView``.

    Raises:
        StorageError: If the metadata store cannot be read (503).
    """
    return view_response(await call(context.overview.overview, window))


@router.get(
    "/rankings/utilization",
    response_model=RankingView[UtilizationRow],
    summary="Top utilization (p95, DASH-003)",
)
async def top_utilization(
    _: Viewer,
    context: Context,
    scope: Scope,
    window: Window = RankingWindow.M15,
    limit: Top = TOP_DEFAULT,
    collector: Collector = None,
) -> JSONResponse:
    """Return the top-utilization ranking.

    Args:
        _: The caller.
        context: The services.
        scope: Which interfaces may rank.
        window: The ranking window.
        limit: Rows.
        collector: Rank this position's samples.

    Returns:
        JSONResponse: ``RankingView`` of ``UtilizationRow``.

    Raises:
        ValidationError: If a bound is broken (422).
    """
    request = RankingRequest(window, limit, collector_id(collector), scope)
    return view_response(await call(context.overview.rankings.top_utilization, request))


@router.get(
    "/rankings/errors", response_model=RankingView[ErrorRow], summary="Top errors (DASH-004)"
)
async def top_errors(
    _: Viewer,
    context: Context,
    scope: Scope,
    window: Window = RankingWindow.M15,
    limit: Top = TOP_DEFAULT,
    collector: Collector = None,
) -> JSONResponse:
    """Return the top-errors ranking.

    Args:
        _: The caller.
        context: The services.
        scope: Which interfaces may rank.
        window: The ranking window.
        limit: Rows.
        collector: Rank this position's samples.

    Returns:
        JSONResponse: ``RankingView`` of ``ErrorRow``.

    Raises:
        ValidationError: If a bound is broken (422).
    """
    request = RankingRequest(window, limit, collector_id(collector), scope)
    return view_response(await call(context.overview.rankings.top_errors, request))


async def _list(context: ApiContext, request: InterfaceListRequest) -> JSONResponse:
    """Answer an interface list request as a paged view.

    Args:
        context: The services.
        request: The list request.

    Returns:
        JSONResponse: ``InterfaceListPage``.

    Raises:
        StorageError: If the metadata store cannot be read.
    """
    view = await call(context.overview.catalog.interface_list, request)
    return view_response(paged(InterfaceListPage, view, view.limit, view.total, view.next_offset))


@router.get("/high-priority", response_model=InterfaceListPage, summary="High-priority interfaces")
async def high_priority(
    _: Viewer,
    context: Context,
    collector: Collector = None,
    limit: Limit = DEFAULT_LIMIT,
    cursor: Cursor = None,
) -> JSONResponse:
    """Return primary-interest interfaces in priority order (DASH-001, META-011).

    Args:
        _: The caller.
        context: The services.
        collector: Position whose current values to show.
        limit: Page size.
        cursor: ``page.next_cursor`` of the previous page.

    Returns:
        JSONResponse: ``InterfaceListPage``.

    Raises:
        ValidationError: If a bound is broken (422).
    """
    scope = InterfaceScope(interest=InterestClass.PRIMARY)
    request = InterfaceListRequest("", scope, InterfaceSort.PRIORITY, offset_of(cursor), limit,
                                   collector_id(collector))  # fmt: skip
    return await _list(context, request)


@router.get("/wan-circuits", response_model=InterfaceListPage, summary="WAN circuits")
async def wan_circuits(
    _: Viewer,
    context: Context,
    scope: Scope,
    collector: Collector = None,
    limit: Limit = MAX_PAGE_LIMIT,
    cursor: Cursor = None,
) -> JSONResponse:
    """Return WAN circuits in name order (DASH-002); group by provider or site.

    Args:
        _: The caller.
        context: The services.
        scope: Site, tag, group, provider filters (WAN only is always applied).
        collector: Position whose current values to show.
        limit: Page size (500 by default, so one page covers a typical site list).
        cursor: ``page.next_cursor`` of the previous page.

    Returns:
        JSONResponse: ``InterfaceListPage``.

    Raises:
        ValidationError: If a bound is broken (422).
    """
    wan = InterfaceScope(scope.device_id, scope.site_id, scope.tag_id, scope.group_id,
                         scope.provider, True, scope.interest, scope.lifecycle,
                         scope.include_retired)  # fmt: skip
    request = InterfaceListRequest("", wan, InterfaceSort.NAME, offset_of(cursor), limit,
                                   collector_id(collector))  # fmt: skip
    return await _list(context, request)


@router.get("/search", response_model=SearchView, summary="Global search (12.1)")
async def search(
    _: Viewer,
    context: Context,
    q: Annotated[str, Query(min_length=1, max_length=SEARCH_TEXT_MAX)],
    per_kind: Annotated[int, Query(ge=1, le=MAX_PAGE_LIMIT)] = SEARCH_HITS_PER_KIND,
) -> JSONResponse:
    """Search devices, interfaces, sites, providers, circuits, tags, and groups.

    Args:
        _: The caller.
        context: The services.
        q: Plain search text (no patterns).
        per_kind: Most hits per kind.

    Returns:
        JSONResponse: ``SearchView``.

    Raises:
        ValidationError: If a bound is broken (422).
    """
    return view_response(await call(context.overview.catalog.search, q, per_kind))
