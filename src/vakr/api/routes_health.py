"""``version`` and the three health endpoints (SVC-004, OBS-002, 14).

- ``GET /api/v1/health/live`` (anyone): the process answers. No database
  is touched, so a slow store never makes a supervisor kill a working API.
- ``GET /api/v1/health/ready`` (anyone): 200 ``ready`` when the metadata
  database answers; 200 ``degraded`` when telemetry is unavailable, because
  inventory and current-state pages still work (13.4); 503 ``not_ready``
  when metadata cannot be read. Anonymous callers see component names and
  states only. ``vakr check health`` probes this over the local socket.
- ``GET /api/v1/health/detail`` (operator): the same with reasons, the
  release, the start time, and every collector's latest self-report.
  Collector and storage trouble is reported here as their own condition,
  never as targets being down (invariant 2).
- ``GET /api/v1/version`` (operator): the release and API version.

Dependencies:
    fastapi: Routing.
    vakr.api.context, principals, schemas: Services, access, shapes.

Safety:
    Reasons name databases and states, never paths' contents or secrets.

Example:
    >>> [r.path for r in router.routes][:2]
    ['/version', '/health/live']
"""

from typing import Annotated

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse

from vakr.api.context import ApiContext, call, get_context
from vakr.api.principals import Viewer
from vakr.api.responses import view_response
from vakr.api.schemas import CheckInfo, HealthInfo, VersionInfo
from vakr.domain.errors import StorageError
from vakr.query import DeviceListRequest
from vakr.version import get_version

router = APIRouter(tags=["health"])
Context = Annotated[ApiContext, Depends(get_context)]


@router.get("/version", response_model=VersionInfo, summary="Running release")
async def version(_: Viewer) -> JSONResponse:
    """Return the running release (operator).

    Args:
        _: The caller (operator or admin).

    Returns:
        JSONResponse: ``VersionInfo``.

    Raises:
        None
    """
    return view_response(VersionInfo(get_version()))


@router.get("/health/live", response_model=HealthInfo, summary="Liveness (anyone)")
async def live() -> JSONResponse:
    """Say the process is alive; touches no database.

    Args:
        None

    Returns:
        JSONResponse: ``{"status": "ok"}``.

    Raises:
        None
    """
    return view_response(HealthInfo("ok"))


def _components(context: ApiContext) -> tuple[CheckInfo, ...]:
    """Check the metadata and telemetry stores (blocking).

    Args:
        context: The services.

    Returns:
        tuple[CheckInfo, ...]: ``metadata`` and ``telemetry`` with reasons.

    Raises:
        None
    """
    try:
        with context.metadata() as uow:
            uow.change_counter()
        metadata = CheckInfo("metadata", "ok", "metadata database answers")
    except StorageError as exc:
        metadata = CheckInfo("metadata", "unavailable", f"metadata database: {exc}")
    if context.telemetry == "ok":
        telemetry = CheckInfo("telemetry", "ok", "telemetry database open for reading")
    else:
        telemetry = CheckInfo("telemetry", "unavailable", context.telemetry)
    return metadata, telemetry


def _overall(checks: tuple[CheckInfo, ...]) -> tuple[str, int]:
    """Summarise components into a readiness word and HTTP status.

    Args:
        checks: The components.

    Returns:
        tuple[str, int]: ``ready``/``degraded`` with 200, or ``not_ready`` with 503.

    Raises:
        None
    """
    states = {c.name: c.status for c in checks}
    if states.get("metadata") != "ok":
        return "not_ready", 503
    return ("ready" if all(s == "ok" for s in states.values()) else "degraded"), 200


@router.get(
    "/health/ready",
    response_model=HealthInfo,
    responses={503: {"model": HealthInfo, "description": "metadata database unavailable"}},
    summary="Readiness (anyone)",
)
async def ready(context: Context) -> JSONResponse:
    """Say whether the API can serve requests.

    Args:
        context: The services.

    Returns:
        JSONResponse: ``HealthInfo`` with names and states only; 503 when not ready.

    Raises:
        None
    """
    checks = await call(_components, context)
    word, status = _overall(checks)
    bare = tuple(CheckInfo(c.name, c.status) for c in checks)
    return view_response(HealthInfo(word, bare), status)


@router.get("/health/detail", response_model=HealthInfo, summary="Component health (operator)")
async def detail(_: Viewer, context: Context) -> JSONResponse:
    """Report every component with reasons and the collectors' self-reports.

    Args:
        _: The caller (operator or admin).
        context: The services.

    Returns:
        JSONResponse: ``HealthInfo``; 503 when the metadata database is unavailable.

    Raises:
        None
    """
    checks = await call(_components, context)
    word, status = _overall(checks)
    collectors = ()
    if status == 200:
        try:
            view = await call(context.overview.device_list, DeviceListRequest(limit=1))
            collectors = view.health.collectors
        except StorageError:
            word, status = "not_ready", 503
    auth = CheckInfo("auth", "ok", "sessions, kiosk tokens, and CSRF keys loaded")
    info = HealthInfo(word, (*checks, auth), get_version(), context.started_at, collectors)
    return view_response(info, status)
