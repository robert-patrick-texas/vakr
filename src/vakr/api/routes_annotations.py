"""Interface annotations and vakr tags: read, change one, tag many (META-007, META-011, META-013).

- ``GET /api/v1/interfaces/{uuid}/annotation`` (operator): the owned
  fields, vakr tags apart from description tags, whether the interface is
  still "not yet classified", the owner, and the version.
- ``PATCH /api/v1/interfaces/{uuid}/annotation`` (admin): change them; the
  body carries the ``version`` it is based on (DB-003). ``high_priority``
  is accepted as the alias of ``interest = "primary"`` (META-011).
- ``POST /api/v1/interfaces/tags`` (admin): add and remove vakr tags on up
  to 500 interfaces at once, all or nothing: the web UI's bulk
  select-and-tag (META-013).

An interface ``inventory.toml`` governs (claimed, E.19) is refused with 409
``managed_by_file`` naming the file and each field (META-008); a peer
refuses every write with 409 ``peer_read_only`` (MPOS-003). Writes need
the CSRF token over the cookie and are audited (SEC-018).

Dependencies:
    fastapi: Routing.
    vakr.inventory.edits.annotations: ``AnnotationEditor``.

Safety:
    Bounded: one interface, or at most 500 in one unit of work.

Example:
    >>> sorted({r.path for r in router.routes})
    ['/interfaces/tags', '/interfaces/{interface_uuid}/annotation']
"""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from vakr.api.context import ApiContext, call, get_context
from vakr.api.edit_calls import edit_call
from vakr.api.errors import ApiError, ErrorEnvelope, Responses, request_id_of
from vakr.api.principals import Admin, Viewer
from vakr.api.routes_identity import principal_actor
from vakr.api.schemas_inventory import (
    AnnotationOut,
    AnnotationPatch,
    TagChange,
    TagChangeOut,
    annotation_json,
)
from vakr.domain.ids import InterfaceId
from vakr.inventory.edits.annotations import AnnotationEditor

router = APIRouter(tags=["interfaces"])
Context = Annotated[ApiContext, Depends(get_context)]
NOT_FOUND: Responses = {404: {"model": ErrorEnvelope, "description": "no such interface"}}
WRITES: Responses = {
    **NOT_FOUND,
    409: {"model": ErrorEnvelope, "description": "managed by a file, a stale version, or a peer"},
}


def _editor(context: ApiContext) -> AnnotationEditor:
    """Return the annotation editor over the process's metadata store.

    Args:
        context: The services.

    Returns:
        AnnotationEditor: Stateless; cheap to build per request.

    Raises:
        None
    """
    return AnnotationEditor(context.metadata, context.clock)


@router.get("/interfaces/{interface_uuid}/annotation", response_model=AnnotationOut,
            responses=NOT_FOUND, summary="An interface's annotation and vakr tags")  # fmt: skip
async def annotation(interface_uuid: UUID, _: Viewer, context: Context) -> JSONResponse:
    """Return an interface's owned fields.

    Args:
        interface_uuid: The interface.
        _: The caller.
        context: The services.

    Returns:
        JSONResponse: ``AnnotationOut``.

    Raises:
        ApiError: 404 for an unknown interface.
    """
    found = await call(_editor(context).get, InterfaceId(interface_uuid))
    if found is None:
        raise ApiError(404, "not_found", f"no interface {interface_uuid}")
    return JSONResponse(annotation_json(found))


@router.patch("/interfaces/{interface_uuid}/annotation", response_model=AnnotationOut,
              responses=WRITES,
              summary="Annotate an interface (admin; carries its version)")  # fmt: skip
async def annotate(
    request: Request, interface_uuid: UUID, body: AnnotationPatch, principal: Admin,
    context: Context,
) -> JSONResponse:  # fmt: skip
    """Change an interface's annotation, if nobody changed it since ``version`` (DB-003).

    Args:
        request: The request.
        interface_uuid: The interface.
        body: ``version`` and the fields that change.
        principal: The administrator.
        context: The services.

    Returns:
        JSONResponse: ``AnnotationOut``; ``changes`` is empty when nothing changed.

    Raises:
        NotFoundError: An unknown interface (404).
        ConflictError: A stale version (409).
        ApiError: 409 ``managed_by_file`` or 422, naming each field.
    """
    changes = body.model_dump(mode="json", exclude_unset=True, exclude={"version"})
    made = await edit_call(_editor(context).annotate, InterfaceId(interface_uuid), changes,
                           version=body.version, actor=principal_actor(principal),
                           request_id=request_id_of(request))  # fmt: skip
    return JSONResponse(annotation_json(made))


@router.post("/interfaces/tags", response_model=TagChangeOut, responses=WRITES,
             summary="Add and remove vakr tags on many interfaces (admin)")  # fmt: skip
async def tag(request: Request, body: TagChange, principal: Admin, context: Context
              ) -> JSONResponse:  # fmt: skip
    """Add and remove vakr tags on up to 500 interfaces, all or nothing (META-013).

    Args:
        request: The request.
        body: The interfaces and the tags to add and remove.
        principal: The administrator.
        context: The services.

    Returns:
        JSONResponse: ``TagChangeOut``.

    Raises:
        NotFoundError: An unknown interface (404).
        ValidationError: Nothing to do, a repeat, or a tag both added and removed (422).
        ApiError: 409 ``managed_by_file`` naming ``interfaces[n]``, or 422 for a tag name.
    """
    done = await edit_call(_editor(context).tag, [InterfaceId(i) for i in body.interfaces],
                           add=body.add, remove=body.remove, actor=principal_actor(principal),
                           request_id=request_id_of(request))  # fmt: skip
    return JSONResponse({"changed": [str(i) for i in done.changed],
                         "unchanged": [str(i) for i in done.unchanged]})  # fmt: skip
