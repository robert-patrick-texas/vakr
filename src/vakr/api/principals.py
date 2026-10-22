"""Who is asking, what they may do, and CSRF for browsers (SEC-005, SEC-006, SEC-011, AUTH-001).

Each request resolves to at most one principal, from exactly one source:

1. on the Unix socket, the connecting account (``vakr.api.peer``); cookies
   and tokens are ignored there;
2. over TCP, ``Authorization: Bearer vkk_...``: a kiosk display, always
   operator (E.12). Any other bearer value is refused;
3. otherwise a valid session cookie named for the client's scheme
   (``vakr.api.cookies``): a person;
4. otherwise a valid kiosk cookie, set by ``POST /api/v1/auth/kiosk``: a
   display, always operator. A browser with both is the person's: someone
   signed in on that browser outranks the display it is (E.30 #17);
5. otherwise anonymous.

Handlers declare what they need with ``Viewer`` (``Permission.VIEW``) or
``Admin`` (``Permission.ADMINISTER``); the decision is ``vakr.auth.allows``
on the principal's role, in the application, never by hiding a control
(3.1). Anonymous callers get 401, a role without the permission 403.

``csrf_guard`` runs for every request under ``/api/v1``. A state-changing
request (anything but GET, HEAD, OPTIONS) over TCP is refused with 403 when
the browser says it is cross-site (``Sec-Fetch-Site``) or names another
``Origin`` than the ``Host`` it was sent to, and, when a session cookie
authenticated it, unless ``X-CSRF-Token`` carries the session's token. The
login itself has no session yet; JSON-only bodies and the origin checks
protect it, as they protect the kiosk exchange and ``forget``. Socket and
bearer callers send no cookie, so CSRF cannot apply; a kiosk cookie is
``SameSite=Strict`` and its operator role changes nothing.

Dependencies:
    fastapi: Dependencies.
    vakr.auth: Principals, permissions, CSRF tokens.

Safety:
    Tokens are wrapped in ``Secret`` as soon as they are read and are never
    logged or echoed. Over-long values are refused before any lookup.

Example:
    >>> CSRF_HEADER
    'X-CSRF-Token'
"""

from collections.abc import Awaitable, Callable
from typing import Annotated
from urllib.parse import urlsplit

from fastapi import Depends, Request

from vakr.api.context import ApiContext, call, get_context
from vakr.api.cookies import MAX_TOKEN_LENGTH, cookie_name, kiosk_cookie_name
from vakr.api.errors import ApiError
from vakr.api.keys import CLIENT_KEY, PEER_KEY
from vakr.api.middleware import LOCAL_CLIENT
from vakr.api.proxy import ClientView
from vakr.auth import Permission, Principal, PrincipalKind, allows
from vakr.credentials.secret import Secret

CSRF_HEADER = "X-CSRF-Token"
SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
CSRF_EXEMPT = frozenset({"/api/v1/auth/login", "/api/v1/auth/kiosk", "/api/v1/auth/kiosk/forget"})
"""State-changing routes that need no session; the origin checks still apply."""
_SAME_SITE = frozenset({"same-origin", "none"})
UNAUTHENTICATED = {"WWW-Authenticate": 'Bearer realm="vakr"'}


def client_view(request: Request) -> ClientView:
    """Return the client as the middleware resolved it.

    Args:
        request: The request.

    Returns:
        ClientView: The client; the local view on the socket.

    Raises:
        None
    """
    view = request.scope.get(CLIENT_KEY)
    return view if isinstance(view, ClientView) else LOCAL_CLIENT


def on_socket(request: Request) -> bool:
    """Tell whether the request came over the local Unix socket.

    Args:
        request: The request.

    Returns:
        bool: True for socket requests.

    Raises:
        None
    """
    return PEER_KEY in request.scope


def session_token(request: Request) -> Secret | None:
    """Return the session cookie for the client's scheme, if one was sent.

    Args:
        request: The request.

    Returns:
        Secret | None: The cookie value, or ``None`` (also for an oversized value).

    Raises:
        None
    """
    value = request.cookies.get(cookie_name(client_view(request)), "")
    return Secret(value) if 0 < len(value) <= MAX_TOKEN_LENGTH else None


def kiosk_token(request: Request) -> Secret | None:
    """Return the kiosk cookie for the client's scheme, if one was sent.

    Args:
        request: The request.

    Returns:
        Secret | None: The cookie value, or ``None`` (also for an oversized value).

    Raises:
        None
    """
    value = request.cookies.get(kiosk_cookie_name(client_view(request)), "")
    return Secret(value) if 0 < len(value) <= MAX_TOKEN_LENGTH else None


async def _cookie(
    context: ApiContext, token: Secret | None, kind: PrincipalKind
) -> Principal | None:
    """Resolve a cookie to its principal, if it is valid and of the expected kind.

    Args:
        context: The services.
        token: The cookie value, or ``None``.
        kind: ``USER`` for the session cookie, ``KIOSK`` for the kiosk cookie.

    Returns:
        Principal | None: The caller, or ``None``.

    Raises:
        StorageError: If the metadata store cannot be read.
    """
    if token is None:
        return None
    principal = await call(context.auth.authenticate, token)
    return principal if principal is not None and principal.kind is kind else None


async def _bearer(context: ApiContext, header: str) -> Principal:
    """Resolve ``Authorization: Bearer`` to a kiosk principal.

    Args:
        context: The services.
        header: The header value.

    Returns:
        Principal: The kiosk display.

    Raises:
        ApiError: 401 for any other scheme, an unknown, expired, or revoked
            token, or a non-kiosk token.
    """
    scheme, _, value = header.strip().partition(" ")
    value = value.strip()
    if scheme.lower() == "bearer" and value.startswith("vkr_"):  # a partner node, ADR-024
        raise ApiError(
            403, "replication_only", "a replication token reaches only /api/v1/replication/"
        )
    if scheme.lower() == "bearer" and value.startswith("vkk_") and len(value) <= MAX_TOKEN_LENGTH:
        principal = await call(context.auth.authenticate, Secret(value))
        if principal is not None and principal.kind is PrincipalKind.KIOSK:
            return principal
    raise ApiError(401, "unauthenticated", "the token is not a valid kiosk token",
                   headers=UNAUTHENTICATED)  # fmt: skip


async def current_principal(
    request: Request, context: Annotated[ApiContext, Depends(get_context)]
) -> Principal | None:
    """Resolve the caller (cached per request by FastAPI).

    Args:
        request: The request.
        context: The services.

    Returns:
        Principal | None: The caller, or ``None`` for anonymous.

    Raises:
        ApiError: 401 for an invalid bearer token.
        StorageError: If the metadata store cannot be read.
    """
    if on_socket(request):
        return getattr(request.scope[PEER_KEY], "principal", None)
    header = request.headers.get("authorization")
    if header:
        return await _bearer(context, header)
    person = await _cookie(context, session_token(request), PrincipalKind.USER)
    return person or await _cookie(context, kiosk_token(request), PrincipalKind.KIOSK)


def require(permission: Permission) -> Callable[..., Awaitable[Principal]]:
    """Build a dependency that admits only principals whose role grants ``permission``.

    Args:
        permission: What the endpoint needs.

    Returns:
        Callable: The dependency, returning the ``Principal``.

    Raises:
        None
    """

    async def dependency(
        principal: Annotated[Principal | None, Depends(current_principal)],
    ) -> Principal:
        """Admit or refuse the caller.

        Args:
            principal: The caller.

        Returns:
            Principal: The admitted caller.

        Raises:
            ApiError: 401 when anonymous, 403 when the role lacks the permission.
        """
        if principal is None:
            raise ApiError(401, "unauthenticated", "sign in first", headers=UNAUTHENTICATED)
        if not allows(principal.role, permission):
            raise ApiError(403, "forbidden", f"this needs the {_ROLE_FOR[permission]} role")
        return principal

    return dependency


_ROLE_FOR = {Permission.VIEW: "operator", Permission.ADMINISTER: "admin"}
Viewer = Annotated[Principal, Depends(require(Permission.VIEW))]
Admin = Annotated[Principal, Depends(require(Permission.ADMINISTER))]


def _cross_site(request: Request) -> bool:
    """Tell whether the browser reports a cross-site or cross-origin request.

    Args:
        request: The request.

    Returns:
        bool: True when ``Sec-Fetch-Site`` is cross-site or same-site, or
        ``Origin`` names another host than ``Host``.

    Raises:
        None
    """
    site = request.headers.get("sec-fetch-site")
    if site is not None and site.lower() not in _SAME_SITE:
        return True
    origin = request.headers.get("origin")
    if origin is None:
        return False
    host = request.headers.get("host", "").lower()
    return origin == "null" or urlsplit(origin).netloc.lower() != host


async def csrf_guard(request: Request) -> None:
    """Refuse cross-site and token-less state changes from browsers (SEC-011, AUTH-003).

    Runs for every ``/api/v1`` route. It touches the database only for a
    state-changing request that carries a session cookie, which is the one
    case CSRF is about; reads and socket, kiosk, and anonymous calls pass
    through untouched.

    Args:
        request: The request.

    Returns:
        None

    Raises:
        ApiError: 403 ``cross_site`` or ``csrf_failed``.
    """
    if request.method in SAFE_METHODS or on_socket(request):
        return
    if _cross_site(request):
        raise ApiError(403, "cross_site", "cross-site requests may not change anything")
    token = session_token(request)
    if request.url.path in CSRF_EXEMPT or token is None or request.headers.get("authorization"):
        return  # no cookie authenticates this request, so there is nothing to forge
    principal = await call(get_context(request).auth.authenticate, token)
    if principal is None or principal.kind is not PrincipalKind.USER or not principal.session_id:
        return  # an invalid cookie authenticates nothing; the route answers 401 if it needs a user
    if not get_context(request).auth.csrf.verify(principal.session_id,
                                                 request.headers.get(CSRF_HEADER)):  # fmt: skip
        raise ApiError(403, "csrf_failed",
                       f"the {CSRF_HEADER} header is missing or does not match the session; "
                       "read it from GET /api/v1/auth/session")  # fmt: skip
