"""Manual device entry: read a device's record, create, change, and retire devices (27.4).

- ``GET /api/v1/devices/{uuid}/record`` (operator): the editable record,
  with ``managed_by`` and ``editable`` so a form can say which file manages it.
- ``POST /api/v1/devices`` (admin): create an API-managed device; 201.
- ``PATCH /api/v1/devices/{uuid}`` (admin): change it; the body carries the
  ``version`` it is based on, and a stale one is 409 ``conflict`` (DB-003).
- ``DELETE /api/v1/devices/{uuid}`` (admin): retire it, the soft delete
  (META-009): polling stops, history stays; ``PATCH`` with
  ``admin_state = "enabled"`` restores it. An optional ``version`` query
  parameter guards it like a change.

A change to a device ``inventory.toml`` manages is 409 ``managed_by_file``
naming the file and each field (META-008). On a peer every write is 409
``peer_read_only`` naming the primary (MPOS-003, ``peer_guard``). Writes
need the CSRF token over the cookie and are audited with the caller
(SEC-018); collectors pick up the change through the metadata change
counter, as after ``vakr inventory apply``.

Dependencies:
    fastapi: Routing.
    vakr.inventory.edits.devices: ``DeviceEditor``.

Safety:
    Answers name the credential reference, never a secret (SEC-001).

Example:
    >>> sorted({r.path for r in router.routes})
    ['/devices', '/devices/{device_uuid}', '/devices/{device_uuid}/record']
"""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import JSONResponse

from vakr.api.context import ApiContext, call, get_context
from vakr.api.edit_calls import edit_call
from vakr.api.errors import ApiError, ErrorEnvelope, Responses, request_id_of
from vakr.api.principals import Admin, Viewer
from vakr.api.routes_identity import principal_actor
from vakr.api.schemas_inventory import DeviceCreate, DevicePatch, DeviceRecordOut, device_json
from vakr.domain.ids import DeviceId
from vakr.inventory.edits.devices import DeviceEditor

router = APIRouter(tags=["devices"])
Context = Annotated[ApiContext, Depends(get_context)]
NOT_FOUND: Responses = {404: {"model": ErrorEnvelope, "description": "no such device"}}
WRITES: Responses = {
    **NOT_FOUND,
    409: {"model": ErrorEnvelope, "description": "managed by a file, a stale version, or a peer"},
}


def _editor(context: ApiContext) -> DeviceEditor:
    """Return the device editor over the process's metadata store.

    Args:
        context: The services.

    Returns:
        DeviceEditor: Stateless; cheap to build per request.

    Raises:
        None
    """
    return DeviceEditor(context.metadata, context.clock, context.inventory_checks)


@router.get("/devices/{device_uuid}/record", response_model=DeviceRecordOut, responses=NOT_FOUND,
            summary="A device's editable record")  # fmt: skip
async def record(device_uuid: UUID, _: Viewer, context: Context) -> JSONResponse:
    """Return the fields an edit sets, the owner, and the version.

    Args:
        device_uuid: The device.
        _: The caller.
        context: The services.

    Returns:
        JSONResponse: ``DeviceRecordOut``.

    Raises:
        ApiError: 404 for an unknown device.
    """
    found = await call(_editor(context).get, DeviceId(device_uuid))
    if found is None:
        raise ApiError(404, "not_found", f"no device {device_uuid}")
    return JSONResponse(device_json(found))


@router.post("/devices", status_code=201, response_model=DeviceRecordOut, responses=WRITES,
             summary="Add a device (admin)")  # fmt: skip
async def create(
    request: Request, body: DeviceCreate, principal: Admin, context: Context
) -> JSONResponse:
    """Create an API-managed device (SEC-018: audited with the administrator).

    Args:
        request: The request.
        body: The ``[[devices]]`` fields.
        principal: The administrator.
        context: The services.

    Returns:
        JSONResponse: 201 ``DeviceRecordOut`` with any warnings.

    Raises:
        ApiError: 422 naming each refused field.
    """
    made = await edit_call(_editor(context).create, body.model_dump(exclude_none=True),
                           actor=principal_actor(principal),
                           request_id=request_id_of(request))  # fmt: skip
    return JSONResponse(device_json(made), status_code=201)


@router.patch("/devices/{device_uuid}", response_model=DeviceRecordOut, responses=WRITES,
              summary="Change a device (admin; carries its version)")  # fmt: skip
async def change(
    request: Request, device_uuid: UUID, body: DevicePatch, principal: Admin, context: Context
) -> JSONResponse:
    """Change a device, if a file does not manage it and nobody changed it since ``version``.

    Args:
        request: The request.
        device_uuid: The device.
        body: ``version`` and the fields that change; ``null`` clears one.
        principal: The administrator.
        context: The services.

    Returns:
        JSONResponse: ``DeviceRecordOut``; ``changes`` is empty when nothing changed.

    Raises:
        NotFoundError: An unknown device (404).
        ConflictError: A stale version (409).
        ApiError: 409 ``managed_by_file`` or 422, naming each field.
    """
    changes = body.model_dump(exclude_unset=True, exclude={"version"})
    made = await edit_call(_editor(context).update, DeviceId(device_uuid), changes,
                           version=body.version, actor=principal_actor(principal),
                           request_id=request_id_of(request))  # fmt: skip
    return JSONResponse(device_json(made))


@router.delete("/devices/{device_uuid}", response_model=DeviceRecordOut, responses=WRITES,
               summary="Retire a device (admin; soft delete)")  # fmt: skip
async def retire(
    request: Request, device_uuid: UUID, principal: Admin, context: Context,
    version: Annotated[int | None, Query(ge=1, description="Refuse if changed since.")] = None,
) -> JSONResponse:  # fmt: skip
    """Retire a device (META-009); its history and interface identities stay.

    Args:
        request: The request.
        device_uuid: The device.
        principal: The administrator.
        context: The services.
        version: The version the delete is based on, if the caller has one.

    Returns:
        JSONResponse: ``DeviceRecordOut`` of the retired device.

    Raises:
        NotFoundError: An unknown device (404).
        ConflictError: A stale version (409).
        ApiError: 409 ``managed_by_file``.
    """
    made = await edit_call(_editor(context).retire, DeviceId(device_uuid), version=version,
                           actor=principal_actor(principal),
                           request_id=request_id_of(request))  # fmt: skip
    return JSONResponse(device_json(made))
