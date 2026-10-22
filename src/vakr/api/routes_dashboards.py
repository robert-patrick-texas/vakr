"""Custom dashboards: list, define, change, delete, and view (UI-004, DASH-007, DASH-T06).

- ``GET /api/v1/dashboards`` (operator): dashboards by name, paged.
- ``POST /api/v1/dashboards`` (admin): create one; 201 with the definition.
- ``GET /api/v1/dashboards/{uuid}`` (operator): the definition.
- ``PATCH /api/v1/dashboards/{uuid}`` (admin): change it; the body carries
  the ``version`` the change is based on, and a stale one is 409
  ``conflict`` (DB-003): reload and apply the change again.
- ``DELETE /api/v1/dashboards/{uuid}`` (admin): delete it; 204.
- ``GET /api/v1/dashboards/{uuid}/view`` (operator, so kiosk displays too):
  every widget resolved with current values in one bounded answer, so a
  refresh is one request plus the graph cards a page shows (DASH-T06).

Writes are audited with the caller (SEC-018) and, over the cookie, need the
CSRF token like every change. Widgets naming inventory that does not exist
are refused with the widget's field path (422). ``POST
/dashboards/{uuid}/publish`` (14) is not offered: public dashboards are
deferred (27.4).

Dependencies:
    fastapi: Routing.
    vakr.dashboards: ``DashboardService``.
    vakr.query.dashboards: ``DashboardQueries``.

Safety:
    Answers carry display names, UUIDs, and measurements only.

Example:
    >>> sorted({r.path for r in router.routes})[0]
    '/dashboards'
"""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, Response

from vakr.api.context import ApiContext, call, get_context
from vakr.api.errors import ApiError, ErrorEnvelope, Responses, request_id_of
from vakr.api.paging import PageInfo, offset_cursor, offset_of
from vakr.api.params import DEFAULT_LIMIT, Collector, Cursor, Limit
from vakr.api.principals import Admin, Viewer
from vakr.api.responses import next_offset, view_response
from vakr.api.schemas_dashboards import (
    DashboardCreate,
    DashboardOut,
    DashboardPage,
    DashboardPatch,
    DashboardSummary,
    definition,
    widgets_json,
)
from vakr.dashboards import DashboardDraft, DashboardService
from vakr.domain.dashboard_codec import widgets_from_json, widgets_to_json
from vakr.domain.dashboards import WIDGET_SCHEMA, Dashboard
from vakr.domain.ids import CollectorId, DashboardId
from vakr.query.dashboard_views import DashboardView
from vakr.query.dashboards import DashboardQueries

router = APIRouter(tags=["dashboards"])
Context = Annotated[ApiContext, Depends(get_context)]
NOT_FOUND: Responses = {404: {"model": ErrorEnvelope, "description": "no such dashboard"}}
WRITES: Responses = {
    **NOT_FOUND,
    409: {"model": ErrorEnvelope, "description": "the name is taken, or a stale version"},
}


def _service(context: ApiContext) -> DashboardService:
    """Return the dashboard writer over the process's metadata store.

    Args:
        context: The services.

    Returns:
        DashboardService: A stateless service; cheap to build per request.

    Raises:
        None
    """
    return DashboardService(context.metadata, context.clock)


def _missing(dashboard_uuid: UUID) -> ApiError:
    """Return the 404 for an unknown dashboard.

    Args:
        dashboard_uuid: The UUID asked for.

    Returns:
        ApiError: 404 ``not_found``.

    Raises:
        None
    """
    return ApiError(404, "not_found", f"no dashboard {dashboard_uuid}")


def _draft(body: DashboardCreate | DashboardPatch, stored: Dashboard | None) -> DashboardDraft:
    """Merge a body with the stored dashboard into the full new content.

    Args:
        body: A create body, or a patch whose absent fields keep ``stored``'s.
        stored: The current dashboard, for a patch.

    Returns:
        DashboardDraft: Name, description, refresh, and decoded widgets.

    Raises:
        ValidationError: If a widget breaks a codec rule (422 with its path).
    """
    base = stored
    raw = widgets_json(body.widgets) if body.widgets is not None else (
        widgets_to_json(base.widgets) if base is not None else [])  # fmt: skip
    return DashboardDraft(
        name=body.name if body.name is not None else base.name if base else "",
        description=body.description if body.description is not None
        else base.description if base else "",
        refresh_s=body.refresh_s if body.refresh_s is not None
        else base.refresh_s if base else 60,
        widgets=widgets_from_json(WIDGET_SCHEMA, raw),
    )  # fmt: skip


@router.get("/dashboards", response_model=DashboardPage, summary="Custom dashboards")
async def dashboards(
    _: Viewer, context: Context, limit: Limit = DEFAULT_LIMIT, cursor: Cursor = None
) -> JSONResponse:
    """List dashboards by name.

    Args:
        _: The caller (operator, admin, or kiosk).
        context: The services.
        limit: Page size.
        cursor: ``page.next_cursor`` of the previous page.

    Returns:
        JSONResponse: ``DashboardPage``.

    Raises:
        ValidationError: If the cursor is not valid (422).
    """
    start = offset_of(cursor)
    every = await call(_service(context).list)  # at most MAX_DASHBOARDS (E.31)
    rows = tuple(DashboardSummary.of(b) for b in every[start : start + limit])
    following = offset_cursor(next_offset(start, limit, len(every)))
    return view_response(DashboardPage(rows, PageInfo(limit, len(every), following)))


@router.post("/dashboards", status_code=201, response_model=DashboardOut, responses=WRITES,
             summary="Create a dashboard (admin)")  # fmt: skip
async def create(
    request: Request, body: DashboardCreate, principal: Admin, context: Context
) -> JSONResponse:
    """Create a dashboard (SEC-018: audited with the administrator).

    Args:
        request: The request.
        body: Name, description, refresh, and widgets.
        principal: The administrator.
        context: The services.

    Returns:
        JSONResponse: 201 ``DashboardOut``.

    Raises:
        ValidationError: Bounds, missing inventory, or the dashboard limit (422).
        DuplicateError: The name is taken (409).
    """
    board = await call(_service(context).create, _draft(body, None), actor=principal.name,
                       request_id=request_id_of(request))  # fmt: skip
    return JSONResponse(definition(board), status_code=201)


@router.get("/dashboards/{dashboard_uuid}", response_model=DashboardOut, responses=NOT_FOUND,
            summary="A dashboard's definition")  # fmt: skip
async def dashboard(dashboard_uuid: UUID, _: Viewer, context: Context) -> JSONResponse:
    """Return a dashboard's definition.

    Args:
        dashboard_uuid: The dashboard.
        _: The caller.
        context: The services.

    Returns:
        JSONResponse: ``DashboardOut``.

    Raises:
        ApiError: 404 for an unknown dashboard.
    """
    board = await call(_service(context).get, DashboardId(dashboard_uuid))
    if board is None:
        raise _missing(dashboard_uuid)
    return JSONResponse(definition(board))


@router.patch("/dashboards/{dashboard_uuid}", response_model=DashboardOut, responses=WRITES,
              summary="Change a dashboard (admin; carries its version)")  # fmt: skip
async def change(
    request: Request, dashboard_uuid: UUID, body: DashboardPatch, principal: Admin,
    context: Context,
) -> JSONResponse:  # fmt: skip
    """Change a dashboard, if nobody changed it since ``body.version`` (DB-003).

    Args:
        request: The request.
        dashboard_uuid: The dashboard.
        body: ``version`` and the fields that change.
        principal: The administrator.
        context: The services.

    Returns:
        JSONResponse: ``DashboardOut``, one version later.

    Raises:
        ApiError: 404 for an unknown dashboard.
        ConflictError: A stale version (409).
        ValidationError, DuplicateError: As for ``POST`` (422, 409).
    """
    service = _service(context)
    stored = await call(service.get, DashboardId(dashboard_uuid))
    if stored is None:
        raise _missing(dashboard_uuid)
    board = await call(service.update, stored.id, _draft(body, stored), version=body.version,
                       actor=principal.name, request_id=request_id_of(request))  # fmt: skip
    return JSONResponse(definition(board))


@router.delete("/dashboards/{dashboard_uuid}", status_code=204, responses=NOT_FOUND,
               summary="Delete a dashboard (admin)")  # fmt: skip
async def delete(
    request: Request, dashboard_uuid: UUID, principal: Admin, context: Context
) -> Response:
    """Delete a dashboard; kiosk displays showing it say so at their next refresh.

    Args:
        request: The request.
        dashboard_uuid: The dashboard.
        principal: The administrator.
        context: The services.

    Returns:
        Response: 204.

    Raises:
        NotFoundError: An unknown dashboard (404).
    """
    await call(_service(context).delete, DashboardId(dashboard_uuid), actor=principal.name,
               request_id=request_id_of(request))  # fmt: skip
    return Response(status_code=204)


@router.get("/dashboards/{dashboard_uuid}/view", response_model=DashboardView,
            responses=NOT_FOUND, summary="A dashboard with current values")  # fmt: skip
async def view(
    dashboard_uuid: UUID, _: Viewer, context: Context, collector: Collector = None
) -> JSONResponse:
    """Return every widget resolved with current values, in one bounded answer.

    Args:
        dashboard_uuid: The dashboard.
        _: The caller (operator, admin, or kiosk).
        context: The services.
        collector: Show this position's values; the local one by default.

    Returns:
        JSONResponse: ``DashboardView``.

    Raises:
        ApiError: 404 for an unknown dashboard.
    """
    queries = DashboardQueries(context.metadata, context.overview, context.clock)
    position = CollectorId(collector) if collector is not None else None
    answer = await call(queries.view, DashboardId(dashboard_uuid), position)
    if answer is None:
        raise _missing(dashboard_uuid)
    return view_response(answer)
