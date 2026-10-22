"""Manual rediscovery and admin operations (DISC-003, DISC-010, API-011, Appendix E.32).

- ``POST /api/v1/devices/{device_uuid}/rediscovery`` (admin, CSRF token for
  browsers): asks a collector to rediscover the device. The answer comes at
  once, never after the discovery (DISC-010): 202 with the new ``queued``
  operation and ``Location: /api/v1/operations/{id}``, or 200 with the
  operation already queued or running for that device, so a second click
  never makes a second walk. A device no collector would discover (disabled,
  retired, ``icmp_only``, no SNMP credential) or a full queue answers 409
  ``refused`` with the reason; an unknown device 404. Every request is
  audited with the administrator (SEC-018).
- ``GET /api/v1/operations/{operation_uuid}`` (operator): one operation, its
  progress and result.
- ``GET /api/v1/operations?device=&state=`` (operator): operations newest
  first, paged (API-005).

Dependencies:
    fastapi: Routing.
    vakr.operations: Requests and reads.

Safety:
    Operation records hold no secret; errors are operator words.

Example:
    >>> sorted(r.path for r in router.routes)[0]
    '/devices/{device_uuid}/rediscovery'
"""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import JSONResponse

from vakr.api.context import ApiContext, call, get_context
from vakr.api.errors import ApiError, ErrorEnvelope, Responses, request_id_of
from vakr.api.paging import PageInfo
from vakr.api.params import DEFAULT_LIMIT, Cursor, Limit
from vakr.api.principals import Admin, Viewer
from vakr.api.responses import view_response
from vakr.api.schemas_operations import OperationOut, OperationPage
from vakr.domain.ids import DeviceId, OperationId
from vakr.domain.operations import Operation, OperationState
from vakr.domain.paging import PageRequest
from vakr.operations import OperationService, RefusedError, Requested
from vakr.ports.metadata import OperationFilter

router = APIRouter(tags=["operations"])
Context = Annotated[ApiContext, Depends(get_context)]
_REQUEST: Responses = {
    200: {"model": OperationOut, "description": "the operation already queued or running"},
    404: {"model": ErrorEnvelope, "description": "no such device"},
    409: {"model": ErrorEnvelope, "description": "no collector would rediscover it, or full"},
}
_ONE: Responses = {404: {"model": ErrorEnvelope, "description": "no such operation"}}
States = Annotated[list[OperationState], Query(max_length=5, description="Repeatable.")]


def service_of(context: ApiContext) -> OperationService:
    """Return the process's operation service.

    Args:
        context: The services.

    Returns:
        OperationService: The configured service, or one with the default strategy.

    Raises:
        None
    """
    if context.operations is None:
        context.operations = OperationService(context.metadata, context.clock)
    return context.operations


def _described(context: ApiContext, ops: tuple[Operation, ...]) -> tuple[OperationOut, ...]:
    """Describe operations with their devices' current names (blocking).

    Args:
        context: The services.
        ops: The operations, at most one page.

    Returns:
        tuple[OperationOut, ...]: The shapes.

    Raises:
        StorageError: If the metadata store cannot be read.
    """
    names: dict[DeviceId, str | None] = {}
    with context.metadata() as uow:
        for op in ops:
            if op.device_id not in names:
                device = uow.devices.get(op.device_id)
                names[op.device_id] = None if device is None else device.name
    return tuple(OperationOut.of(op, names[op.device_id]) for op in ops)


@router.post(
    "/devices/{device_uuid}/rediscovery",
    status_code=202,
    response_model=OperationOut,
    responses=_REQUEST,
    summary="Rediscover a device now (admin; asynchronous)",
)
async def rediscovery(
    request: Request, device_uuid: UUID, principal: Admin, context: Context
) -> JSONResponse:
    """Queue a manual rediscovery and answer at once with the operation (DISC-003, DISC-010).

    Args:
        request: The request, for its ID.
        device_uuid: The device.
        principal: The administrator.
        context: The services.

    Returns:
        JSONResponse: 202 with a new operation, 200 with the active one.

    Raises:
        ApiError: 409 when refused; NotFoundError becomes 404.
    """
    service = service_of(context)
    try:
        asked: Requested = await call(service.request_rediscovery, DeviceId(device_uuid),
                                      actor=principal.name,
                                      request_id=request_id_of(request))  # fmt: skip
    except RefusedError as exc:
        raise ApiError(409, "refused", str(exc)) from None
    (out,) = await call(_described, context, (asked.operation,))
    response = view_response(out, 202 if asked.created else 200)
    response.headers["Location"] = f"/api/v1/operations/{asked.operation.id}"
    return response


@router.get(
    "/operations/{operation_uuid}",
    response_model=OperationOut,
    responses=_ONE,
    summary="One admin operation: progress and result",
)
async def operation(operation_uuid: UUID, _: Viewer, context: Context) -> JSONResponse:
    """Return one operation.

    Args:
        operation_uuid: The operation.
        _: The caller (operator or admin).
        context: The services.

    Returns:
        JSONResponse: ``OperationOut``.

    Raises:
        ApiError: 404 for an unknown operation.
    """
    op = await call(service_of(context).get, OperationId(operation_uuid))
    if op is None:
        raise ApiError(404, "not_found", f"no operation {operation_uuid}")
    (out,) = await call(_described, context, (op,))
    return view_response(out)


@router.get("/operations", response_model=OperationPage, summary="Admin operations, newest first")
async def operations(
    _: Viewer,
    context: Context,
    device: Annotated[UUID | None, Query(description="Only this device's.")] = None,
    state: States = [],  # noqa: B006 - FastAPI copies it
    limit: Limit = DEFAULT_LIMIT,
    cursor: Cursor = None,
) -> JSONResponse:
    """List operations newest first.

    Args:
        _: The caller.
        context: The services.
        device: Only this device's, if given.
        state: Only these states, if given.
        limit: Page size.
        cursor: ``page.next_cursor`` of the previous page.

    Returns:
        JSONResponse: ``OperationPage``.

    Raises:
        ValidationError: If the cursor is invalid (422).
    """
    where = OperationFilter(None if device is None else DeviceId(device), frozenset(state))
    found = await call(service_of(context).page, PageRequest(limit, cursor), where)
    rows = await call(_described, context, found.items)
    return view_response(OperationPage(rows, PageInfo(limit, found.total, found.next_cursor)))
