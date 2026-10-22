"""``GET /api/v1/metrics``: vakr's self-metrics in Prometheus text format (OBS-005, E.33).

The endpoint needs the operator role, so it is never anonymous: on the
node, scrapers use the Unix socket (root, ``vakr-view``, or ``vakr``);
through the HTTPS reverse proxy, Prometheus sends a kiosk token as a
bearer credential. Being under ``/api/``, it carries ``Cache-Control:
no-store`` and the security headers of every API answer, and the proxy
exposes nothing new without a credential (OBS-T02).

The answer covers the API process, the collector (from its snapshot
file), the inventory, and the databases; ``docs/operations/metrics.md``
lists every family. A part that cannot be read is absent, with an ``up``
gauge saying why, and the answer is still 200: missing is never zero
(invariant 1).

Dependencies:
    fastapi: Routing.
    vakr.api.context, principals: Services and the viewer requirement.
    vakr.metrics.exposition: The content type.

Safety:
    No label names a device, interface, user, address, path, or token.

Example:
    >>> router.routes[0].path
    '/metrics'
"""

from typing import Annotated

from fastapi import APIRouter, Depends
from fastapi.responses import Response

from vakr.api.context import ApiContext, call, get_context
from vakr.api.principals import Viewer
from vakr.metrics.exposition import CONTENT_TYPE

router = APIRouter(tags=["health"])
Context = Annotated[ApiContext, Depends(get_context)]
_EXAMPLE = "# HELP vakr_build_info Always 1; ...\n# TYPE vakr_build_info gauge\n"


@router.get(
    "/metrics",
    response_class=Response,
    responses={200: {"content": {CONTENT_TYPE: {"example": _EXAMPLE}},
                     "description": "Prometheus text exposition 0.0.4"}},
    summary="Self-metrics (Prometheus text format; operator)",
)  # fmt: skip
async def metrics(_: Viewer, context: Context) -> Response:
    """Answer the self-metrics.

    Args:
        _: The caller (operator or admin, on the socket or with a credential).
        context: The services.

    Returns:
        Response: The exposition text.

    Raises:
        None
    """
    service = context.metrics
    live = service.live()  # on the loop: the request counters it mutates
    text = await call(service.render, live)
    return Response(text, media_type=CONTENT_TYPE)
