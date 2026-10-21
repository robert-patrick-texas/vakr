"""Sign-in, sign-out, and "who am I" over HTTP (SEC-010, SEC-011, SEC-012, SEC-018, AUTH-002).

- ``POST /api/v1/auth/login`` with ``{"username", "password"}`` as JSON.
  Success sets the session cookie (``vakr.api.cookies``) and answers the
  session, including the CSRF token for later state changes. Every login
  starts a new session and ends the one the browser presented (rotation).
  A failure is answered 401 after the throttle delay, the same for an
  unknown account, a wrong password, and a disabled account (AUTH-004); an
  attempt inside the throttle wait is answered 429 with ``Retry-After``
  without checking the password. Plain HTTP from anywhere but loopback is
  refused (403 ``insecure_transport``): passwords and cookies cross the
  network only inside TLS (SEC-013).
- ``POST /api/v1/auth/logout`` ends the session (CSRF token required) and
  clears the cookie.
- ``GET /api/v1/auth/session``: the caller's kind, name, role, and, for a
  signed-in user, the CSRF token and the session's expiry.

The local socket needs none of this: its caller is the connecting account,
so login and logout there are refused (400 ``local_socket``).

Dependencies:
    fastapi, pydantic: Routing and the login body.
    vakr.auth: The login decision, sessions, CSRF.

Safety:
    The password is wrapped in ``Secret`` at once and goes only to the login
    service; login, failure, and logout are audited by ``vakr.auth`` with the
    client address, never the password. Validation errors never echo input.

Example:
    >>> LoginRequest(username="alice", password="x" * 20).username
    'alice'
"""

import math
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, ConfigDict, Field

from vakr.api.context import ApiContext, call, get_context
from vakr.api.cookies import clear_session_cookie, cookie_allowed, set_session_cookie
from vakr.api.errors import ApiError, ErrorEnvelope, Responses, request_id_of
from vakr.api.principals import client_view, current_principal, on_socket, session_token
from vakr.api.responses import view_response
from vakr.api.schemas import SessionInfo
from vakr.auth import LoginOutcome, Principal, PrincipalKind
from vakr.credentials.secret import Secret

router = APIRouter(tags=["auth"])
Context = Annotated[ApiContext, Depends(get_context)]
MAX_USERNAME_INPUT = 256
MAX_PASSWORD_INPUT = 1024
"""Input bounds; the services apply the real rules (E.12: 64 and 256 characters)."""
INSECURE = (
    "sign-in over plain HTTP is refused; use the HTTPS address of the reverse proxy "
    "(docs/operations/web-access.md)"
)
_ERRORS: Responses = {
    401: {"model": ErrorEnvelope, "description": "wrong username or password"},
    403: {"model": ErrorEnvelope, "description": "plain HTTP from a non-loopback client"},
    429: {"model": ErrorEnvelope, "description": "throttled; see Retry-After"},
}


class LoginRequest(BaseModel):
    """Body of ``POST /api/v1/auth/login``.

    Attributes:
        username: Username as typed (case-insensitive).
        password: Password as typed.
    """

    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)

    username: str = Field(min_length=1, max_length=MAX_USERNAME_INPUT)
    password: str = Field(min_length=1, max_length=MAX_PASSWORD_INPUT, repr=False)


def json_only(request: Request) -> None:
    """Refuse a login body that is not JSON, so no HTML form can post one cross-site.

    Args:
        request: The request.

    Returns:
        None

    Raises:
        ApiError: 415 for any other content type.
    """
    media = request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    if media != "application/json":
        raise ApiError(415, "unsupported_media_type", "send the login as application/json")


def not_on_socket(request: Request) -> None:
    """Refuse cookie sign-in on the local socket, whose caller is already known.

    Args:
        request: The request.

    Returns:
        None

    Raises:
        ApiError: 400 ``local_socket``.
    """
    if on_socket(request):
        raise ApiError(400, "local_socket", "the local socket signs you in as your account; "
                       "sign-in and sign-out are for web clients")  # fmt: skip


def session_info(context: ApiContext, principal: Principal) -> SessionInfo:
    """Describe a principal: CSRF token and expiry for a user, expiry for a kiosk (blocking).

    Args:
        context: The services.
        principal: The caller.

    Returns:
        SessionInfo: The description.

    Raises:
        StorageError: If the sessions cannot be read.
    """
    base = SessionInfo(principal.kind.value, principal.name, principal.role.value)
    if principal.kind is PrincipalKind.KIOSK and principal.kiosk_id is not None:
        kiosk = context.auth.kiosk.get(principal.kiosk_id)  # the display shows when it expires
        return SessionInfo(base.kind, base.name, base.role, expires_at=kiosk.expires_at
                           if kiosk is not None else None)  # fmt: skip
    if principal.kind is not PrincipalKind.USER or principal.session_id is None:
        return base
    expires = None
    if principal.user_id is not None:
        found = [s for s in context.auth.sessions.sessions_of(principal.user_id)
                 if s.id == principal.session_id]  # fmt: skip
        expires = found[0].expires_at if found else None
    idle = int(context.auth.settings.session_idle.total_seconds())
    return SessionInfo(base.kind, base.name, base.role, context.auth.csrf.token_for(
        principal.session_id), expires, idle, principal.user_id)  # fmt: skip


@router.post(
    "/auth/login",
    response_model=SessionInfo,
    responses=_ERRORS,
    dependencies=[Depends(not_on_socket), Depends(json_only)],
    summary="Sign in (sets the session cookie)",
)
async def login(request: Request, body: LoginRequest, context: Context) -> Response:
    """Decide one login attempt and answer it.

    Args:
        request: The request.
        body: Username and password.
        context: The services.

    Returns:
        Response: 200 with the session and cookie.

    Raises:
        ApiError: 401 failed, 403 insecure transport, 429 throttled or too
            many logins at once, 503 busy.
    """
    view = client_view(request)
    if not cookie_allowed(view):
        raise ApiError(403, "insecure_transport", INSECURE)
    async with context.logins.slot():
        result = await call(context.auth.login.login, body.username, Secret(body.password),
                            source=view.address, request_id=request_id_of(request),
                            replaces=session_token(request))  # fmt: skip
    if result.outcome is LoginOutcome.THROTTLED:
        wait = str(max(math.ceil(result.retry_after_s), 1))
        raise ApiError(429, "throttled", "too many failed sign-ins; wait and try again",
                       headers={"Retry-After": wait})  # fmt: skip
    if result.outcome is not LoginOutcome.SUCCESS or result.issued is None:
        await context.sleep(result.delay_s)
        raise ApiError(401, "login_failed", "the username or password is not right")
    issued = result.issued
    principal = await call(context.auth.authenticate, issued.token)
    if principal is None:  # disabled between login and now
        raise ApiError(401, "login_failed", "the username or password is not right")
    response = view_response(await call(session_info, context, principal))
    set_session_cookie(response, issued.token, view, issued.session.expires_at,
                       context.clock.now_utc())  # fmt: skip
    return response


@router.post(
    "/auth/logout",
    status_code=204,
    dependencies=[Depends(not_on_socket)],
    summary="Sign out (ends the session, clears the cookie)",
)
async def logout(request: Request, context: Context) -> Response:
    """End the presented session and clear the cookie.

    Args:
        request: The request (CSRF checked by the router's guard).
        context: The services.

    Returns:
        Response: 204.

    Raises:
        StorageError: If the session cannot be ended.
    """
    token = session_token(request)
    if token is not None:
        await call(context.auth.sessions.revoke, token, request_id=request_id_of(request))
    response = Response(status_code=204)
    clear_session_cookie(response, client_view(request))
    return response


@router.get(
    "/auth/session",
    response_model=SessionInfo,
    responses={401: {"model": ErrorEnvelope, "description": "not signed in"}},
    summary="Who am I (role, CSRF token, expiry)",
)
async def session(
    context: Context, principal: Annotated[Principal | None, Depends(current_principal)]
) -> JSONResponse:
    """Describe the caller.

    Args:
        context: The services.
        principal: The caller.

    Returns:
        JSONResponse: ``SessionInfo``.

    Raises:
        ApiError: 401 when anonymous.
    """
    if principal is None:
        raise ApiError(401, "unauthenticated", "sign in first",
                       headers={"WWW-Authenticate": 'Bearer realm="vakr"'})  # fmt: skip
    return view_response(await call(session_info, context, principal))
