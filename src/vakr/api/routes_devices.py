"""Device list, device detail, a device's interfaces, and its reachability history (12.3, 12.4).

- ``GET /api/v1/devices``: the device list in 7.7 order (Attention, Active,
  Offline/Retired, then severity and natural name), searchable, filterable
  by bucket, evidence freshness, and collector (12.3), and, with ``site``,
  ``tag``, ``group``, ``provider``, or ``admin_state``, browsable by those
  groupings (UI-005, UI-009). Paged (API-005); ``counts`` and ``health``
  describe every matching device.
- ``GET /api/v1/devices/{device_uuid}``: the device screen: facts, warnings,
  every position's verdict, and the interface table (TUI-004..006, UI-003).
- ``GET /api/v1/device-lookup?key=``: the same screen for an exact display
  name, else a UUID, as ``vakr status DEVICE`` asks (E.29 #10, E.30 #15).
- ``GET /api/v1/devices/{device_uuid}/interfaces``: the device's interfaces,
  sticky, WAN, and high-priority first, paged, optionally of one interest
  class (UI-006, UI-016, UI-017).
- ``GET /api/v1/devices/{device_uuid}/history/reachability``: latency, loss,
  jitter, and the per-probe smoke, one series set per position, never
  merged (12.6, MPOS-008).

All need the operator role.

Dependencies:
    fastapi: Routing.
    vakr.query: ``OverviewQueries``, ``CatalogQueries``, ``HistoryQueries``.

Safety:
    Every read is bounded (E.19, E.28, E.29); telemetry trouble is reported
    inside the 200 answer, never as a device being down.

Example:
    >>> any(r.path == "/devices" for r in router.routes)
    True
"""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from fastapi.responses import JSONResponse

from vakr.api.context import ApiContext, call, get_context
from vakr.api.errors import ApiError, ErrorEnvelope, Responses
from vakr.api.paging import offset_of
from vakr.api.params import (
    DEFAULT_LIMIT,
    Collector,
    Cursor,
    Limit,
    Search,
    collector_id,
    device_scope,
    graph_request,
)
from vakr.api.params_devices import (
    DeviceFilter,
    DeviceInterfaceFilter,
    device_filter,
    device_interface_filter,
)
from vakr.api.principals import Viewer
from vakr.api.responses import next_offset, paged, view_response
from vakr.api.schemas import DeviceListPage, InterfaceListPage
from vakr.domain.ids import DeviceId
from vakr.query import (
    DeviceDetailView,
    DeviceListRequest,
    DeviceScope,
    DeviceSort,
    GraphRequest,
    InterfaceListRequest,
    InterfaceScope,
    InterfaceSort,
)
from vakr.query.latency_views import LatencyView
from vakr.query.source import MAX_DEVICE_KEY_LENGTH

router = APIRouter(tags=["devices"])
Context = Annotated[ApiContext, Depends(get_context)]
NOT_FOUND: Responses = {404: {"model": ErrorEnvelope, "description": "no such device"}}
DeviceKey = Annotated[
    str,
    Query(min_length=1, max_length=MAX_DEVICE_KEY_LENGTH,
          description="Exact display name, else device UUID (vakr status DEVICE)."),
]  # fmt: skip


def device_exists(context: ApiContext, device_id: DeviceId) -> bool:
    """Tell whether a device is in the inventory (blocking).

    Args:
        context: The services.
        device_id: The device.

    Returns:
        bool: True when it exists (retired included).

    Raises:
        StorageError: If the metadata store cannot be read.
    """
    with context.metadata() as uow:
        return uow.devices.get(device_id) is not None


def missing(device_id: UUID) -> ApiError:
    """Build the 404 for an unknown device.

    Args:
        device_id: The UUID asked for.

    Returns:
        ApiError: 404 ``not_found``.

    Raises:
        None
    """
    return ApiError(404, "not_found", f"no device {device_id}")


@router.get("/devices", response_model=DeviceListPage, summary="Device list (7.7 order)")
async def devices(
    _: Viewer,
    context: Context,
    scope: Annotated[DeviceScope | None, Depends(device_scope)],
    filters: Annotated[DeviceFilter, Depends(device_filter)],
    search: Search = "",
    sort: DeviceSort = DeviceSort.STATUS,
    limit: Limit = DEFAULT_LIMIT,
    cursor: Cursor = None,
) -> JSONResponse:
    """Return one page of the device list.

    Args:
        _: The caller (operator or admin).
        context: The services.
        scope: Site, tag, group, provider, or admin-state filter, if any.
        filters: Bucket, retired, freshness, and collector filters.
        search: Substring of name, address, site, or tag.
        sort: ``status`` (7.7), ``name``, ``site``, or ``age``.
        limit: Page size.
        cursor: ``page.next_cursor`` of the previous page.

    Returns:
        JSONResponse: ``DeviceListPage``.

    Raises:
        ValidationError: If a bound is broken (422).
    """
    request = DeviceListRequest(search, filters.buckets, sort, offset_of(cursor), limit,
                                filters.include_retired, filters.freshness,
                                filters.collector)  # fmt: skip
    if scope is None:
        view = await call(context.overview.device_list, request)
    else:
        view = await call(context.overview.browse, scope, request)
    following = next_offset(view.offset, view.limit, view.total)
    return view_response(paged(DeviceListPage, view, view.limit, view.total, following))


@router.get(
    "/devices/{device_uuid}",
    response_model=DeviceDetailView,
    responses=NOT_FOUND,
    summary="Device screen",
)
async def device(device_uuid: UUID, _: Viewer, context: Context) -> JSONResponse:
    """Return the device screen.

    Args:
        device_uuid: The device.
        _: The caller.
        context: The services.

    Returns:
        JSONResponse: ``DeviceDetailView``.

    Raises:
        ApiError: 404 for an unknown device.
    """
    view = await call(context.overview.device_detail, str(device_uuid))
    if view is None:
        raise missing(device_uuid)
    return view_response(view)


@router.get(
    "/device-lookup",
    response_model=DeviceDetailView,
    responses=NOT_FOUND,
    summary="Device screen by exact name or UUID",
)
async def device_lookup(key: DeviceKey, _: Viewer, context: Context) -> JSONResponse:
    """Return the device screen of the device named ``key``, else with that UUID.

    The local status views take a name or a UUID; the lookup is the
    in-process one, so both data paths find the same device (E.29 #10).

    Args:
        key: Display name (compared exactly) or device UUID.
        _: The caller.
        context: The services.

    Returns:
        JSONResponse: ``DeviceDetailView``.

    Raises:
        ApiError: 404 when nothing matches; the key is not repeated back.
    """
    view = await call(context.overview.device_detail, key)
    if view is None:
        raise ApiError(404, "not_found", "no device has this name or UUID")
    return view_response(view)


@router.get(
    "/devices/{device_uuid}/interfaces",
    response_model=InterfaceListPage,
    responses=NOT_FOUND,
    summary="A device's interfaces, primary interest first",
)
async def device_interfaces(
    device_uuid: UUID,
    _: Viewer,
    context: Context,
    filters: Annotated[DeviceInterfaceFilter, Depends(device_interface_filter)],
    search: Search = "",
    sort: InterfaceSort = InterfaceSort.PRIORITY,
    collector: Collector = None,
    limit: Limit = DEFAULT_LIMIT,
    cursor: Cursor = None,
) -> JSONResponse:
    """Return one page of a device's interfaces.

    Args:
        device_uuid: The device.
        _: The caller.
        context: The services.
        filters: Retired interfaces and interest class (UI-017).
        search: Substring of interface name, alias, description, provider, or circuit.
        sort: ``priority``, ``name``, ``traffic``, or ``utilization``.
        collector: Position whose current values to show.
        limit: Page size.
        cursor: ``page.next_cursor`` of the previous page.

    Returns:
        JSONResponse: ``InterfaceListPage``.

    Raises:
        ApiError: 404 for an unknown device.
    """
    device_id = DeviceId(device_uuid)
    if not await call(device_exists, context, device_id):
        raise missing(device_uuid)
    scope = InterfaceScope(device_id=device_id, include_retired=filters.include_retired,
                           interest=filters.interest)  # fmt: skip
    request = InterfaceListRequest(search, scope, sort, offset_of(cursor), limit,
                                   collector_id(collector))  # fmt: skip
    view = await call(context.overview.catalog.interface_list, request)
    return view_response(paged(InterfaceListPage, view, view.limit, view.total, view.next_offset))


@router.get(
    "/devices/{device_uuid}/history/reachability",
    response_model=LatencyView,
    responses=NOT_FOUND,
    summary="Latency, loss, jitter, and smoke per position",
)
async def reachability(
    device_uuid: UUID,
    _: Viewer,
    context: Context,
    request: Annotated[GraphRequest, Depends(graph_request)],
) -> JSONResponse:
    """Return the device's reachability history (12.6, MPOS-008).

    Args:
        device_uuid: The device.
        _: The caller.
        context: The services.
        request: Range, source, point cap, and optional position.

    Returns:
        JSONResponse: ``LatencyView`` with the actual resolution and zone (API-006).

    Raises:
        ApiError: 404 for an unknown device.
    """
    view = await call(context.history.device_latency, DeviceId(device_uuid), request)
    if view is None:
        raise missing(device_uuid)
    return view_response(view)
