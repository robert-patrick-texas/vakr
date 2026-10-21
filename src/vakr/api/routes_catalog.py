"""Sites, tags, groups, and collectors with their health (META-005, META-006, MPOL-007, OBS-002).

- ``GET /api/v1/sites``, ``/tags``, ``/groups``: name-ordered pages with
  stable keyset cursors (API-005); use the UUIDs as ``site``, ``tag``, and
  ``group`` filters on the device and interface lists (UI-009).
- ``GET /api/v1/collectors``: every polling position with its latest
  self-report: state, report age, and spool level. A collector whose report
  is older than its E.3 threshold reads as ``unavailable`` (E.29 #4).
- ``GET /api/v1/collectors/{collector_uuid}/health``: one collector.

All need the operator role.

Dependencies:
    fastapi: Routing.
    vakr.ports.metadata: Catalog repositories.
    vakr.query: The collector health view.

Safety:
    Read-only and bounded by the page size (E.19).

Example:
    >>> sorted(r.path for r in router.routes)[:2]
    ['/collectors', '/collectors/{collector_uuid}/health']
"""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse

from vakr.api.context import ApiContext, call, get_context
from vakr.api.errors import ApiError, ErrorEnvelope
from vakr.api.paging import PageInfo
from vakr.api.params import DEFAULT_LIMIT, Cursor, Limit
from vakr.api.principals import Viewer
from vakr.api.responses import view_response
from vakr.api.schemas import (
    CollectorOut,
    CollectorPage,
    LabelOut,
    LabelPage,
    SiteOut,
    SitePage,
)
from vakr.domain.ids import CollectorId
from vakr.domain.paging import PageRequest
from vakr.query import DeviceListRequest

router = APIRouter(tags=["catalog"])
Context = Annotated[ApiContext, Depends(get_context)]


def sites_page(context: ApiContext, page: PageRequest) -> SitePage:
    """Read one page of sites (blocking).

    Args:
        context: The services.
        page: Limit and cursor.

    Returns:
        SitePage: The sites.

    Raises:
        ValidationError: If the cursor is not valid.
        StorageError: If the metadata store cannot be read.
    """
    with context.metadata() as uow:
        found = uow.sites.list_page(page)
    items = tuple(SiteOut(s.id, s.name, s.code, s.region, s.timezone, s.description)
                  for s in found.items)  # fmt: skip
    return SitePage(items, PageInfo(page.limit, found.total, found.next_cursor))


def labels_page(context: ApiContext, kind: str, page: PageRequest) -> LabelPage:
    """Read one page of tags or groups (blocking).

    Args:
        context: The services.
        kind: ``tags`` or ``groups``.
        page: Limit and cursor.

    Returns:
        LabelPage: The labels.

    Raises:
        ValidationError: If the cursor is not valid.
        StorageError: If the metadata store cannot be read.
    """
    with context.metadata() as uow:
        found = getattr(uow, kind).list_page(page)
    items = tuple(LabelOut(x.id, x.name, x.description) for x in found.items)
    return LabelPage(items, PageInfo(page.limit, found.total, found.next_cursor))


def collectors_page(context: ApiContext, page: PageRequest) -> CollectorPage:
    """Read one page of collectors with their latest health (blocking).

    Args:
        context: The services.
        page: Limit and cursor.

    Returns:
        CollectorPage: The collectors.

    Raises:
        ValidationError: If the cursor is not valid.
        StorageError: If the metadata store cannot be read.
    """
    with context.metadata() as uow:
        found = uow.collectors.list_page(page)
    health = context.overview.device_list(DeviceListRequest(limit=1)).health.collectors
    by_id = {h.collector_id: h for h in health}
    items = tuple(CollectorOut(c.id, c.name, c.position, c.enabled, by_id.get(c.id))
                  for c in found.items)  # fmt: skip
    return CollectorPage(items, PageInfo(page.limit, found.total, found.next_cursor))


@router.get("/sites", response_model=SitePage, summary="Sites")
async def sites(
    _: Viewer, context: Context, limit: Limit = DEFAULT_LIMIT, cursor: Cursor = None
) -> JSONResponse:
    """Return a page of sites.

    Args:
        _: The caller (operator or admin).
        context: The services.
        limit: Page size.
        cursor: ``page.next_cursor`` of the previous page.

    Returns:
        JSONResponse: ``SitePage``.

    Raises:
        ValidationError: If the cursor is not valid (422).
    """
    return view_response(await call(sites_page, context, PageRequest(limit, cursor)))


@router.get("/tags", response_model=LabelPage, summary="Tags")
async def tags(
    _: Viewer, context: Context, limit: Limit = DEFAULT_LIMIT, cursor: Cursor = None
) -> JSONResponse:
    """Return a page of tags.

    Args:
        _: The caller.
        context: The services.
        limit: Page size.
        cursor: ``page.next_cursor`` of the previous page.

    Returns:
        JSONResponse: ``LabelPage``.

    Raises:
        ValidationError: If the cursor is not valid (422).
    """
    return view_response(await call(labels_page, context, "tags", PageRequest(limit, cursor)))


@router.get("/groups", response_model=LabelPage, summary="Groups")
async def groups(
    _: Viewer, context: Context, limit: Limit = DEFAULT_LIMIT, cursor: Cursor = None
) -> JSONResponse:
    """Return a page of groups.

    Args:
        _: The caller.
        context: The services.
        limit: Page size.
        cursor: ``page.next_cursor`` of the previous page.

    Returns:
        JSONResponse: ``LabelPage``.

    Raises:
        ValidationError: If the cursor is not valid (422).
    """
    return view_response(await call(labels_page, context, "groups", PageRequest(limit, cursor)))


@router.get("/collectors", response_model=CollectorPage, summary="Collectors and their health")
async def collectors(
    _: Viewer, context: Context, limit: Limit = DEFAULT_LIMIT, cursor: Cursor = None
) -> JSONResponse:
    """Return a page of collectors.

    Args:
        _: The caller.
        context: The services.
        limit: Page size.
        cursor: ``page.next_cursor`` of the previous page.

    Returns:
        JSONResponse: ``CollectorPage``.

    Raises:
        ValidationError: If the cursor is not valid (422).
    """
    return view_response(await call(collectors_page, context, PageRequest(limit, cursor)))


def one_collector(context: ApiContext, collector_uuid: UUID) -> CollectorOut | None:
    """Read one collector with its health (blocking).

    Args:
        context: The services.
        collector_uuid: The collector.

    Returns:
        CollectorOut | None: ``None`` for an unknown collector.

    Raises:
        StorageError: If the metadata store cannot be read.
    """
    with context.metadata() as uow:
        found = uow.collectors.get(CollectorId(collector_uuid))
    if found is None:
        return None
    health = context.overview.device_list(DeviceListRequest(limit=1)).health.collectors
    mine = next((h for h in health if h.collector_id == found.id), None)
    return CollectorOut(found.id, found.name, found.position, found.enabled, mine)


@router.get(
    "/collectors/{collector_uuid}/health",
    response_model=CollectorOut,
    responses={404: {"model": ErrorEnvelope, "description": "no such collector"}},
    summary="One collector's health",
)
async def collector_health(collector_uuid: UUID, _: Viewer, context: Context) -> JSONResponse:
    """Return one collector with its latest self-report.

    Args:
        collector_uuid: The collector.
        _: The caller.
        context: The services.

    Returns:
        JSONResponse: ``CollectorOut``.

    Raises:
        ApiError: 404 for an unknown collector.
    """
    found = await call(one_collector, context, collector_uuid)
    if found is None:
        raise ApiError(404, "not_found", f"no collector {collector_uuid}")
    return view_response(found)
