"""Bounded CSV and JSON downloads of a graph or a ranking (API-012, API-T09).

- ``GET /api/v1/exports/interfaces/{interface_uuid}/history/{kind}``
- ``GET /api/v1/exports/devices/{device_uuid}/history/reachability``
- ``GET /api/v1/exports/rankings/{utilization|errors}``

Each takes the same parameters as the view it exports plus ``format``
(``csv`` or ``json``), and answers a file with ``Content-Disposition:
attachment``. An export is exactly what the reader can already see, so it
needs the same role (operator) and has the same bounds (1,200 points per
series); it is refused with 422 above 20,000 rows or 4 MiB (E.28). Exports
are synchronous within those bounds; asynchronous bulk export, with
operation IDs, is deferred past the MVP (27.4, API-011).

Dependencies:
    fastapi: Routing.
    vakr.query.export, vakr.api.export_rankings: Encoders.

Safety:
    Filenames use safe characters only; timestamps are UTC with the display
    timezone in its own column; a missing value is an empty cell.

Example:
    >>> sorted(r.path for r in router.routes)[0]
    '/exports/devices/{device_uuid}/history/reachability'
"""

from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from fastapi.responses import Response

from vakr.api.context import ApiContext, call, get_context
from vakr.api.errors import ApiError, ErrorEnvelope, Responses
from vakr.api.export_rankings import export_ranking
from vakr.api.params import Collector, collector_id, graph_request, interface_scope
from vakr.api.principals import Viewer
from vakr.domain.ids import DeviceId, InterfaceId
from vakr.query import (
    ExportFormat,
    GraphKind,
    GraphRequest,
    InterfaceScope,
    RankingRequest,
    RankingWindow,
    export_view,
)
from vakr.query.export import ExportFile
from vakr.query.history_limits import TOP_DEFAULT, TOP_MAX

router = APIRouter(tags=["exports"])
Context = Annotated[ApiContext, Depends(get_context)]
Format = Annotated[ExportFormat, Query(alias="format", description="csv or json.")]
Graph = Annotated[GraphRequest, Depends(graph_request)]
FILE: Responses = {
    200: {"content": {"text/csv": {}, "application/json": {}}, "description": "the file"},
    404: {"model": ErrorEnvelope, "description": "no such object"},
    422: {"model": ErrorEnvelope, "description": "out of bounds or too large"},
}


def file_response(found: ExportFile) -> Response:
    """Answer an export as a download.

    Args:
        found: The encoded export.

    Returns:
        Response: The file with ``Content-Disposition: attachment``.

    Raises:
        None
    """
    disposition = f'attachment; filename="{found.filename}"'
    return Response(found.body, media_type=found.media_type,
                    headers={"Content-Disposition": disposition})  # fmt: skip


@router.get(
    "/exports/interfaces/{interface_uuid}/history/{kind}",
    response_class=Response,
    responses=FILE,
    summary="Download an interface graph",
)
async def export_interface(
    interface_uuid: UUID, kind: GraphKind, _: Viewer, context: Context, request: Graph,
    fmt: Format = ExportFormat.CSV,
) -> Response:  # fmt: skip
    """Export one interface graph.

    Args:
        interface_uuid: The interface.
        kind: Traffic, packets, errors, or utilization.
        _: The caller (operator or admin).
        context: The services.
        request: Range, source, point cap, position.
        fmt: ``csv`` or ``json``.

    Returns:
        Response: The file.

    Raises:
        ApiError: 404 for an unknown interface.
        ValidationError: If the export is too large (422).
    """
    view = await call(context.history.interface_graph, InterfaceId(interface_uuid), kind, request)
    if view is None:
        raise ApiError(404, "not_found", f"no interface {interface_uuid}")
    return file_response(await call(export_view, view, fmt))


@router.get(
    "/exports/devices/{device_uuid}/history/reachability",
    response_class=Response,
    responses=FILE,
    summary="Download a device's latency and loss",
)
async def export_reachability(
    device_uuid: UUID, _: Viewer, context: Context, request: Graph,
    fmt: Format = ExportFormat.CSV,
) -> Response:  # fmt: skip
    """Export one device's latency, loss, and jitter per position.

    Args:
        device_uuid: The device.
        _: The caller.
        context: The services.
        request: Range, source, point cap, position.
        fmt: ``csv`` or ``json``.

    Returns:
        Response: The file.

    Raises:
        ApiError: 404 for an unknown device.
        ValidationError: If the export is too large (422).
    """
    view = await call(context.history.device_latency, DeviceId(device_uuid), request)
    if view is None:
        raise ApiError(404, "not_found", f"no device {device_uuid}")
    return file_response(await call(export_view, view, fmt))


@router.get(
    "/exports/rankings/{kind}",
    response_class=Response,
    responses=FILE,
    summary="Download a ranking",
)
async def export_rankings(
    kind: Literal["utilization", "errors"],
    _: Viewer,
    context: Context,
    scope: Annotated[InterfaceScope, Depends(interface_scope)],
    window: RankingWindow = RankingWindow.M15,
    limit: Annotated[int, Query(ge=1, le=TOP_MAX)] = TOP_DEFAULT,
    collector: Collector = None,
    fmt: Format = ExportFormat.CSV,
) -> Response:
    """Export a top-utilization or top-errors ranking.

    Args:
        kind: ``utilization`` or ``errors``.
        _: The caller.
        context: The services.
        scope: Which interfaces may rank.
        window: The ranking window.
        limit: Rows.
        collector: Rank this position's samples.
        fmt: ``csv`` or ``json``.

    Returns:
        Response: The file.

    Raises:
        ValidationError: If a bound is broken (422).
    """
    request = RankingRequest(window, limit, collector_id(collector), scope)
    rankings = context.overview.rankings
    if kind == "utilization":
        view = await call(rankings.top_utilization, request)
    else:
        view = await call(rankings.top_errors, request)
    return file_response(await call(export_ranking, view, fmt))
