"""The global interface list, interface detail, and interface history (12.5, 12.6, API-006).

- ``GET /api/v1/interfaces``: every interface across devices, filterable by
  site, tag, group, provider, WAN, interest, and lifecycle, sorted by
  priority (sticky, WAN, high priority first), name, traffic, or
  utilization, paged (API-005, UI-017).
- ``GET /api/v1/interfaces/{interface_uuid}``: identity, annotations, and
  current values from one position.
- ``GET /api/v1/interfaces/{interface_uuid}/history/{traffic|packets|errors|utilization}``:
  a graph over a preset or custom range, with one series per position and
  direction, never merged (MPOS-008). Each answer says which tiers it read
  (``resolutions``: native or 15-minute rollups, and the bucket width), the
  display timezone, per-point quality flags and gap markers, and whether a
  series was truncated (API-006, UI-010). A missing value is ``null``.
- ``GET /api/v1/interfaces/{interface_uuid}/history/status``: the admin/oper
  timeline of the last 7 days of the range (12.5).

All need the operator role.

Dependencies:
    fastapi: Routing.
    vakr.query: ``CatalogQueries``, ``HistoryQueries``.

Safety:
    Ranges are 1 minute to 400 days, ending at most 5 minutes ahead; at
    most 1,200 points per series (E.19, E.28, SEC-019).

Example:
    >>> any(r.path.endswith("/history/traffic") for r in router.routes)
    True
"""

from collections.abc import Awaitable, Callable
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse

from vakr.api.context import ApiContext, call, get_context
from vakr.api.errors import ApiError, ErrorEnvelope, Responses
from vakr.api.interface_detail import interface_detail
from vakr.api.paging import offset_of
from vakr.api.params import (
    DEFAULT_LIMIT,
    Collector,
    Cursor,
    Limit,
    Search,
    collector_id,
    graph_request,
    interface_scope,
)
from vakr.api.principals import Viewer
from vakr.api.responses import paged, view_response
from vakr.api.schemas import InterfaceDetail, InterfaceListPage
from vakr.domain.ids import InterfaceId
from vakr.query import GraphKind, GraphRequest, InterfaceListRequest, InterfaceScope, InterfaceSort
from vakr.query.history_views import GraphView
from vakr.query.latency_views import TimelineView

router = APIRouter(tags=["interfaces"])
Context = Annotated[ApiContext, Depends(get_context)]
Graph = Annotated[GraphRequest, Depends(graph_request)]
NOT_FOUND: Responses = {404: {"model": ErrorEnvelope, "description": "no such interface"}}


def missing(interface_uuid: UUID) -> ApiError:
    """Build the 404 for an unknown interface.

    Args:
        interface_uuid: The UUID asked for.

    Returns:
        ApiError: 404 ``not_found``.

    Raises:
        None
    """
    return ApiError(404, "not_found", f"no interface {interface_uuid}")


@router.get("/interfaces", response_model=InterfaceListPage, summary="All interfaces")
async def interfaces(
    _: Viewer,
    context: Context,
    scope: Annotated[InterfaceScope, Depends(interface_scope)],
    search: Search = "",
    sort: InterfaceSort = InterfaceSort.PRIORITY,
    collector: Collector = None,
    limit: Limit = DEFAULT_LIMIT,
    cursor: Cursor = None,
) -> JSONResponse:
    """Return one page of the global interface list.

    Args:
        _: The caller (operator or admin).
        context: The services.
        scope: Site, tag, group, provider, WAN, interest, lifecycle filters.
        search: Substring of device, interface, alias, description, provider, circuit, or site.
        sort: ``priority``, ``name``, ``traffic``, or ``utilization``.
        collector: Position whose current values to show.
        limit: Page size.
        cursor: ``page.next_cursor`` of the previous page.

    Returns:
        JSONResponse: ``InterfaceListPage``.

    Raises:
        ValidationError: If a bound is broken (422).
    """
    request = InterfaceListRequest(search, scope, sort, offset_of(cursor), limit,
                                   collector_id(collector))  # fmt: skip
    view = await call(context.overview.catalog.interface_list, request)
    return view_response(paged(InterfaceListPage, view, view.limit, view.total, view.next_offset))


@router.get(
    "/interfaces/{interface_uuid}",
    response_model=InterfaceDetail,
    responses=NOT_FOUND,
    summary="Interface identity, annotations, and current values",
)
async def interface(
    interface_uuid: UUID, _: Viewer, context: Context, collector: Collector = None
) -> JSONResponse:
    """Return one interface.

    Args:
        interface_uuid: The interface.
        _: The caller.
        context: The services.
        collector: Position whose current values to show.

    Returns:
        JSONResponse: ``InterfaceDetail``.

    Raises:
        ApiError: 404 for an unknown interface.
    """
    found = await call(interface_detail, context, InterfaceId(interface_uuid),
                       collector_id(collector))  # fmt: skip
    if found is None:
        raise missing(interface_uuid)
    return view_response(found)


@router.get(
    "/interfaces/{interface_uuid}/history/status",
    response_model=TimelineView,
    responses=NOT_FOUND,
    summary="Admin/oper status timeline (last 7 days of the range)",
)
async def status_history(
    interface_uuid: UUID, _: Viewer, context: Context, request: Graph
) -> JSONResponse:
    """Return the interface's status timeline from one position.

    Args:
        interface_uuid: The interface.
        _: The caller.
        context: The services.
        request: Range and position.

    Returns:
        JSONResponse: ``TimelineView``.

    Raises:
        ApiError: 404 for an unknown interface.
    """
    view = await call(context.history.interface_timeline, InterfaceId(interface_uuid), request)
    if view is None:
        raise missing(interface_uuid)
    return view_response(view)


def _graph_route(kind: GraphKind) -> Callable[..., Awaitable[JSONResponse]]:
    """Build the history endpoint of one graph kind.

    Args:
        kind: Traffic, packets, errors, or utilization.

    Returns:
        Callable: The endpoint.

    Raises:
        None
    """

    async def graph(
        interface_uuid: UUID, _: Viewer, context: Context, request: Graph
    ) -> JSONResponse:
        """Return one graph of the interface (API-006, MPOS-008).

        Args:
            interface_uuid: The interface.
            _: The caller.
            context: The services.
            request: Range, source, point cap, and optional position.

        Returns:
            JSONResponse: ``GraphView`` with resolutions, zone, quality, truncation.

        Raises:
            ApiError: 404 for an unknown interface.
        """
        view = await call(context.history.interface_graph, InterfaceId(interface_uuid), kind,
                          request)  # fmt: skip
        if view is None:
            raise missing(interface_uuid)
        return view_response(view)

    graph.__name__ = f"{kind.value}_history"
    return graph


for _kind in GraphKind:
    router.add_api_route(
        f"/interfaces/{{interface_uuid}}/history/{_kind.value}",
        _graph_route(_kind),
        methods=["GET"],
        response_model=GraphView,
        responses=NOT_FOUND,
        summary=f"{_kind.value.capitalize()} graph, one series per position",
    )
