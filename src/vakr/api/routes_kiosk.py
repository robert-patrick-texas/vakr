"""Kiosk displays: set one up, forget it, and manage kiosk tokens (UI-013, E.12, E.30 #17).

- ``POST /api/v1/auth/kiosk`` with ``{"token": "vkk_..."}`` as JSON
  (anyone): exchanges a valid kiosk token for the kiosk cookie
  (``vakr.api.cookies``), so a wall display's browser stays signed in
  across reboots without page script ever holding the token. Plain HTTP
  from anywhere but loopback is refused (403 ``insecure_transport``), as
  for sign-in; so are cross-site requests and non-JSON bodies. A refused
  token is answered 401 after a fixed delay, and at most ``KIOSK_ACTIVE``
  exchanges run at once (E.30).
- ``POST /api/v1/auth/kiosk/forget`` (anyone): clears this browser's kiosk
  cookie. The token stays valid for any other display that holds it; an
  admin revokes it.
- ``GET /api/v1/kiosk-tokens`` (admin): the token records by label, paged.
- ``POST /api/v1/kiosk-tokens`` with ``{"label", "lifetime"?}`` (admin):
  creates a token and answers it once, 201.
- ``DELETE /api/v1/kiosk-tokens/{kiosk_uuid}`` (admin): revokes a token;
  every display that holds it is signed out at its next request.

Dependencies:
    fastapi: Routing.
    vakr.auth: ``KioskService`` and the label and lifetime rules.

Safety:
    The token is wrapped in ``Secret`` at once; it appears only in the 201
    answer of its creation and in the ``Set-Cookie`` header of an exchange,
    both ``no-store``. Logs and audit records name the label and the
    client address, never the token.

Example:
    >>> sorted(r.path for r in router.routes)[0]
    '/auth/kiosk'
"""

import logging
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, Response

from vakr.api.context import ApiContext, call, get_context
from vakr.api.cookies import clear_kiosk_cookie, cookie_allowed, set_kiosk_cookie
from vakr.api.errors import ApiError, ErrorEnvelope, Responses, request_id_of
from vakr.api.paging import PageInfo, offset_cursor, offset_of
from vakr.api.params import DEFAULT_LIMIT, Cursor, Limit
from vakr.api.principals import Admin, client_view
from vakr.api.responses import next_offset, view_response
from vakr.api.routes_auth import json_only, not_on_socket
from vakr.api.schemas import SessionInfo
from vakr.api.schemas_kiosk import (
    KioskCreate,
    KioskExchange,
    KioskIssued,
    KioskTokenOut,
    KioskTokenPage,
)
from vakr.auth.kiosk_rules import parse_lifetime
from vakr.credentials.secret import Secret
from vakr.domain.ids import KioskTokenId

_LOG = logging.getLogger("vakr.api.kiosk")
router = APIRouter(tags=["kiosk"])
Context = Annotated[ApiContext, Depends(get_context)]
REFUSED_DELAY_S = 1.0
"""Seconds before a refused exchange is answered: cheap to wait, slow to script (E.30 #17)."""
INSECURE = (
    "a kiosk display is set up over HTTPS only; open the reverse proxy's HTTPS address "
    "(docs/operations/web-access.md)"
)
_EXCHANGE: Responses = {
    401: {"model": ErrorEnvelope, "description": "not a live kiosk token"},
    403: {"model": ErrorEnvelope, "description": "plain HTTP from a non-loopback client"},
    429: {"model": ErrorEnvelope, "description": "too many exchanges at once"},
}
NOT_FOUND: Responses = {404: {"model": ErrorEnvelope, "description": "no such kiosk token"}}


@router.post(
    "/auth/kiosk",
    response_model=SessionInfo,
    responses=_EXCHANGE,
    dependencies=[Depends(not_on_socket), Depends(json_only)],
    summary="Set up a kiosk display (sets the kiosk cookie)",
)
async def exchange(request: Request, body: KioskExchange, context: Context) -> Response:
    """Exchange a kiosk token for the kiosk cookie.

    Args:
        request: The request.
        body: The token.
        context: The services.

    Returns:
        Response: 200 with the display's identity and expiry, and the cookie.

    Raises:
        ApiError: 401 refused token, 403 insecure transport, 429 too many at once.
    """
    view = client_view(request)
    if not cookie_allowed(view):
        raise ApiError(403, "insecure_transport", INSECURE)
    token = Secret(body.token)
    async with context.kiosk_exchanges.slot():
        found = await call(context.auth.kiosk.exchange, token, source=view.address,
                           request_id=request_id_of(request))  # fmt: skip
    if found is None:
        _LOG.info("kiosk setup refused from %s: not a live kiosk token", view.address,
                  extra={"event_id": "api.kiosk_refused"})  # fmt: skip
        await context.sleep(REFUSED_DELAY_S)
        raise ApiError(401, "kiosk_refused", "this is not a live kiosk token: it is mistyped, "
                       "expired, or revoked; ask an admin for a new one")  # fmt: skip
    principal, kiosk = found
    _LOG.info("kiosk display %s set up from %s", kiosk.label, view.address,
              extra={"event_id": "api.kiosk_setup"})  # fmt: skip
    info = SessionInfo(principal.kind.value, principal.name, principal.role.value,
                       expires_at=kiosk.expires_at)  # fmt: skip
    response = view_response(info)
    set_kiosk_cookie(response, token, view, kiosk.expires_at, context.clock.now_utc())
    return response


@router.post(
    "/auth/kiosk/forget",
    status_code=204,
    dependencies=[Depends(not_on_socket)],
    summary="Forget this kiosk display (clears the kiosk cookie)",
)
async def forget(request: Request) -> Response:
    """Clear the kiosk cookie of this browser.

    Args:
        request: The request (origin checked by the router's guard).

    Returns:
        Response: 204.

    Raises:
        None
    """
    response = Response(status_code=204)
    clear_kiosk_cookie(response, client_view(request))
    return response


@router.get("/kiosk-tokens", response_model=KioskTokenPage, summary="Kiosk tokens (admin)")
async def tokens(
    _: Admin, context: Context, limit: Limit = DEFAULT_LIMIT, cursor: Cursor = None
) -> JSONResponse:
    """List kiosk token records by label (never the tokens).

    Args:
        _: The administrator.
        context: The services.
        limit: Page size.
        cursor: ``page.next_cursor`` of the previous page.

    Returns:
        JSONResponse: ``KioskTokenPage``.

    Raises:
        ValidationError: If the cursor is not valid (422).
    """
    start = offset_of(cursor)
    every = await call(context.auth.kiosk.tokens)  # at most max_kiosk_tokens (E.12)
    now = context.clock.now_utc()
    rows = tuple(KioskTokenOut.of(t, now) for t in every[start : start + limit])
    following = offset_cursor(next_offset(start, limit, len(every)))
    return view_response(KioskTokenPage(rows, PageInfo(limit, len(every), following)))


@router.post(
    "/kiosk-tokens",
    status_code=201,
    response_model=KioskIssued,
    responses={409: {"model": ErrorEnvelope, "description": "the label is taken"}},
    summary="Create a kiosk token (admin; the token is answered once)",
)
async def create(
    request: Request, body: KioskCreate, principal: Admin, context: Context
) -> JSONResponse:
    """Create a kiosk token for a display (SEC-018: audited with the administrator).

    Args:
        request: The request.
        body: Label and optional lifetime.
        principal: The administrator.
        context: The services.

    Returns:
        JSONResponse: 201 ``KioskIssued``; the token is in no other answer.

    Raises:
        ValidationError: Label, lifetime, or the token limit (422).
        DuplicateError: The label is taken (409).
    """
    lifetime = parse_lifetime(body.lifetime)
    issued = await call(context.auth.kiosk.create, body.label, actor=principal.name,
                        request_id=request_id_of(request), lifetime=lifetime)  # fmt: skip
    out = KioskTokenOut.of(issued.record, context.clock.now_utc())
    return view_response(KioskIssued(out, issued.token.reveal()), 201)


@router.delete(
    "/kiosk-tokens/{kiosk_uuid}",
    status_code=204,
    responses=NOT_FOUND,
    summary="Revoke a kiosk token (admin)",
)
async def revoke(
    request: Request, kiosk_uuid: UUID, principal: Admin, context: Context
) -> Response:
    """Revoke a kiosk token; displays holding it are refused from their next request.

    Args:
        request: The request.
        kiosk_uuid: The token record.
        principal: The administrator.
        context: The services.

    Returns:
        Response: 204.

    Raises:
        ApiError: 404 for an unknown token.
    """
    done = await call(context.auth.kiosk.revoke, KioskTokenId(kiosk_uuid), actor=principal.name,
                      request_id=request_id_of(request))  # fmt: skip
    if not done:
        raise ApiError(404, "not_found", f"no kiosk token {kiosk_uuid}")
    return Response(status_code=204)
