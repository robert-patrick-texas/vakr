"""Interface identity history, open reviews, and the administrator's decision (IFID-005, IFID-007).

- ``GET /api/v1/interfaces/{interface_uuid}/identity-history`` (operator):
  every decision that kept or changed the identity, oldest first, paged.
- ``GET /api/v1/identity-reviews`` (operator): open reviews with their
  candidates, oldest first; ``device`` narrows to one device.
- ``POST /api/v1/interfaces/{interface_uuid}/identity-resolution`` (admin,
  CSRF token for browsers): answer a review with ``map`` (the row is this
  interface; its history continues) or ``new`` (the row is a new interface).
  The decision is the same one ``vakr interface review map|new`` makes: one
  transaction that updates identities, closes the review, appends identity
  history, and writes the audit record with the administrator's name
  (SEC-018). A refused decision writes nothing and answers 409.

Dependencies:
    fastapi: Routing.
    vakr.discovery.reconcile.admin: ``map_review``, ``create_identity``.
    vakr.ports.metadata: History and review reads.

Safety:
    Telemetry and history are never deleted; only identity mapping changes.

Example:
    >>> any(r.path.endswith("/identity-resolution") for r in router.routes)
    True
"""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from fastapi.responses import JSONResponse

from vakr.api.context import ApiContext, call, get_context
from vakr.api.errors import ApiError, ErrorEnvelope, Responses
from vakr.api.paging import PageInfo
from vakr.api.params import DEFAULT_LIMIT, Cursor, Limit
from vakr.api.principals import Admin, Viewer
from vakr.api.responses import view_response
from vakr.api.schemas_identity import (
    CandidateOut,
    IdentityEntryOut,
    IdentityHistoryPage,
    ResolutionOut,
    ResolutionRequest,
    ReviewOut,
    ReviewPage,
)
from vakr.auth import Principal
from vakr.discovery.reconcile.admin import ReviewError, create_identity, map_review
from vakr.domain.control import IdentityHistoryEntry
from vakr.domain.ids import DeviceId, InterfaceId, ReviewId
from vakr.domain.paging import PageRequest
from vakr.domain.reviews import IdentityReview, ReviewState
from vakr.ports.metadata import MetadataUnitOfWork
from vakr.ports.metadata.reviews import ReviewFilter

router = APIRouter(tags=["identity"])
Context = Annotated[ApiContext, Depends(get_context)]
NOT_FOUND: Responses = {404: {"model": ErrorEnvelope, "description": "no such interface"}}


def _entry(e: IdentityHistoryEntry) -> IdentityEntryOut:
    """Copy one history entry into its API shape.

    Args:
        e: The entry.

    Returns:
        IdentityEntryOut: The shape.

    Raises:
        None
    """
    return IdentityEntryOut(e.id, e.decided_at, e.outcome.value, e.source.value,
                            e.previous_if_index, e.observed_if_index, e.score, e.runner_up_score,
                            e.evidence, e.actor)  # fmt: skip


def history_page(
    context: ApiContext, interface_id: InterfaceId, page: PageRequest
) -> IdentityHistoryPage | None:
    """Read one page of identity history (blocking).

    Args:
        context: The services.
        interface_id: The interface.
        page: Limit and cursor.

    Returns:
        IdentityHistoryPage | None: ``None`` for an unknown interface.

    Raises:
        ValidationError: If the cursor is not valid.
        StorageError: If the metadata store cannot be read.
    """
    with context.metadata() as uow:
        if uow.interfaces.get(interface_id) is None:
            return None
        found = uow.interfaces.identity_history(interface_id, page)
    items = tuple(_entry(e) for e in found.items)
    return IdentityHistoryPage(interface_id, items, PageInfo(page.limit, found.total,
                                                             found.next_cursor))  # fmt: skip


def _review(uow: MetadataUnitOfWork, r: IdentityReview) -> ReviewOut:
    """Describe one review with its candidates.

    Args:
        uow: The open unit of work.
        r: The review.

    Returns:
        ReviewOut: The shape.

    Raises:
        StorageError: If a record cannot be read.
    """
    device = uow.devices.get(r.device_id)
    candidates = []
    for candidate in r.candidates:
        i = uow.interfaces.get(candidate)
        if i is not None:
            candidates.append(CandidateOut(i.id, i.if_name or i.if_descr, i.if_index,
                                           i.lifecycle.value))  # fmt: skip
    return ReviewOut(r.id, r.device_id, device.name if device else str(r.device_id), r.opened_at,
                     r.outcome.value, r.if_index, r.if_name, r.if_descr, r.if_alias,
                     tuple(candidates), r.evidence)  # fmt: skip


def review_page(context: ApiContext, device: DeviceId | None, page: PageRequest) -> ReviewPage:
    """Read one page of open reviews (blocking).

    Args:
        context: The services.
        device: Only this device's reviews, or ``None``.
        page: Limit and cursor.

    Returns:
        ReviewPage: The reviews.

    Raises:
        ValidationError: If the cursor is not valid.
        StorageError: If the metadata store cannot be read.
    """
    with context.metadata() as uow:
        found = uow.reviews.list_page(page, ReviewFilter(device_id=device, state=ReviewState.OPEN))
        items = tuple(_review(uow, r) for r in found.items)
    return ReviewPage(items, PageInfo(page.limit, found.total, found.next_cursor))


@router.get(
    "/interfaces/{interface_uuid}/identity-history",
    response_model=IdentityHistoryPage,
    responses=NOT_FOUND,
    summary="Identity decisions of one interface",
)
async def identity_history(
    interface_uuid: UUID, _: Viewer, context: Context, limit: Limit = DEFAULT_LIMIT,
    cursor: Cursor = None,
) -> JSONResponse:  # fmt: skip
    """Return a page of the interface's identity history.

    Args:
        interface_uuid: The interface.
        _: The caller (operator or admin).
        context: The services.
        limit: Page size.
        cursor: ``page.next_cursor`` of the previous page.

    Returns:
        JSONResponse: ``IdentityHistoryPage``.

    Raises:
        ApiError: 404 for an unknown interface.
    """
    found = await call(history_page, context, InterfaceId(interface_uuid),
                       PageRequest(limit, cursor))  # fmt: skip
    if found is None:
        raise ApiError(404, "not_found", f"no interface {interface_uuid}")
    return view_response(found)


@router.get("/identity-reviews", response_model=ReviewPage, summary="Open identity reviews")
async def identity_reviews(
    _: Viewer,
    context: Context,
    device: Annotated[UUID | None, Query(description="Only this device's reviews.")] = None,
    limit: Limit = DEFAULT_LIMIT,
    cursor: Cursor = None,
) -> JSONResponse:
    """Return a page of open identity reviews.

    Args:
        _: The caller.
        context: The services.
        device: Only this device's reviews.
        limit: Page size.
        cursor: ``page.next_cursor`` of the previous page.

    Returns:
        JSONResponse: ``ReviewPage``.

    Raises:
        ValidationError: If the cursor is not valid (422).
    """
    chosen = DeviceId(device) if device else None
    return view_response(await call(review_page, context, chosen, PageRequest(limit, cursor)))


def resolve(
    context: ApiContext, interface_id: InterfaceId, body: ResolutionRequest, actor: str
) -> ResolutionOut:
    """Apply an administrator's decision (blocking, one transaction).

    Args:
        context: The services.
        interface_id: The interface in the path.
        body: The review and decision.
        actor: The administrator, for identity history and audit.

    Returns:
        ResolutionOut: The result.

    Raises:
        ApiError: 404 for an unknown interface, 409 when the review does not allow it.
    """
    review_id = ReviewId(body.review_id)
    with context.metadata() as uow:
        target, review = uow.interfaces.get(interface_id), uow.reviews.get(review_id)
    if target is None:
        raise ApiError(404, "not_found", f"no interface {interface_id}")
    if review is not None and review.device_id != target.device_id:
        raise ApiError(409, "conflict", f"interface {interface_id} is not on the review's device")
    try:
        if body.decision == "map":
            result = map_review(context.metadata, review_id, interface_id, actor, context.clock)
        else:
            result = create_identity(context.metadata, review_id, actor, context.clock)
    except ReviewError as exc:
        raise ApiError(409, "conflict", str(exc)) from None
    return ResolutionOut(review_id, body.decision, result.id, result.if_index)


@router.post(
    "/interfaces/{interface_uuid}/identity-resolution",
    response_model=ResolutionOut,
    responses={**NOT_FOUND, 409: {"model": ErrorEnvelope, "description": "review not open"}},
    summary="Resolve an identity review (admin)",
)
async def identity_resolution(
    interface_uuid: UUID, body: ResolutionRequest, principal: Admin, context: Context
) -> JSONResponse:
    """Map a review's row to this interface, or give it a new identity (IFID-007, SEC-018).

    Args:
        interface_uuid: The interface.
        body: The review and decision.
        principal: The administrator.
        context: The services.

    Returns:
        JSONResponse: ``ResolutionOut``.

    Raises:
        ApiError: 404 unknown interface, 409 refused decision.
    """
    actor = principal_actor(principal)
    return view_response(await call(resolve, context, InterfaceId(interface_uuid), body, actor))


def principal_actor(principal: Principal) -> str:
    """Name the administrator in identity history and audit records.

    Args:
        principal: The caller.

    Returns:
        str: The username, ``local:<account>``, or ``kiosk:<label>``.

    Raises:
        None
    """
    return principal.name
