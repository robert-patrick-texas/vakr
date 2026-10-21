"""The FastAPI application of ``vakr-api``: v1 routes, errors, middleware, web hook (14, API-004).

``create_app`` assembles everything under ``/api/v1``: health and version,
auth, devices, interfaces and their history, identity, catalog,
dashboards views, and exports. The middleware (request IDs, trusted
proxies, body limit, security headers) wraps it all, and every error uses
the envelope of ``vakr.api.errors``. Interactive documentation pages are
off: they load scripts from a CDN (UI-012). The OpenAPI document is served
at ``/api/v1/openapi.json`` and checked in as ``docs/api/openapi.json``.

**The web front end attaches at ``web``.** It is the static SvelteKit
build of ADR-035, attached by ``vakr.api.web.web_mount``: a function that
receives the app and adds its routes outside ``/api/``. It inherits the
security headers and must not change state on GET; every state change
goes through the JSON API, which enforces CSRF.

Dependencies:
    fastapi: The application.
    vakr.api.*: Routes, middleware, errors.

Safety:
    CORS is never enabled (SEC-011): no ``Access-Control-Allow-*`` header is
    sent, so other origins cannot read answers.

Example:
    >>> create_app().title
    'vakr API'
"""

from collections.abc import Callable

from fastapi import APIRouter, Depends, FastAPI

from vakr.api import (
    routes_auth,
    routes_catalog,
    routes_devices,
    routes_export,
    routes_health,
    routes_identity,
    routes_interfaces,
    routes_kiosk,
    routes_views,
)
from vakr.api.context import ApiContext
from vakr.api.error_handlers import install_handlers
from vakr.api.errors import ErrorEnvelope, Responses
from vakr.api.middleware import ApiMiddleware
from vakr.api.openapi import ANONYMOUS_PATHS, describe_security
from vakr.api.principals import csrf_guard
from vakr.api.settings import ApiSettings

type WebMount = Callable[[FastAPI], None]
"""Attaches a web front end to the app (routes or a static mount outside ``/api/``)."""

API_PREFIX = "/api/v1"
API_VERSION = "1"
"""The OpenAPI ``info.version``: the API major version, never the release (VER-001)."""
DESCRIPTION = (
    "JSON API of vakr, the network performance and reachability monitor. "
    "Browsers authenticate with the session cookie from `POST /api/v1/auth/login` and send "
    "`X-CSRF-Token` (from `GET /api/v1/auth/session`) on every state-changing request. "
    "Kiosk displays exchange their token once at `POST /api/v1/auth/kiosk` for an `HttpOnly` "
    "kiosk cookie, or send `Authorization: Bearer vkk_...` (operator, read-only). "
    "On the monitoring node, the Unix socket `/run/vakr/api.sock` authorizes by the "
    "connecting account (root and vakr-admin: admin; vakr-view and vakr: operator). "
    "Errors use one envelope: `code`, `message`, `request_id`, `fields`. "
    "Lists page with `limit` and `cursor` and answer `page.next_cursor`. "
    "Times are UTC ISO 8601 with `Z`; a missing value is `null`, never 0."
)
TAGS = [
    {"name": "health", "description": "Liveness, readiness, component health, version."},
    {"name": "auth", "description": "Sign-in, sign-out, and the current session."},
    {"name": "kiosk", "description": "Kiosk display setup and kiosk tokens (admin)."},
    {"name": "devices", "description": "Device list, device screen, reachability history."},
    {"name": "interfaces", "description": "Interface list, detail, and history graphs."},
    {"name": "identity", "description": "Interface identity history and reviews."},
    {"name": "catalog", "description": "Sites, tags, groups, and collectors."},
    {"name": "dashboards", "description": "Overview, rankings, WAN, high priority, search."},
    {"name": "exports", "description": "Bounded CSV and JSON downloads."},
]
COMMON_ERRORS: Responses = {
    401: {"model": ErrorEnvelope, "description": "not signed in"},
    403: {"model": ErrorEnvelope, "description": "role, CSRF, or socket peer refused"},
    422: {"model": ErrorEnvelope, "description": "a parameter is out of bounds"},
    503: {"model": ErrorEnvelope, "description": "metadata database unavailable"},
}
_ROUTERS = (routes_health, routes_auth, routes_kiosk, routes_devices, routes_interfaces,
            routes_identity, routes_catalog, routes_views, routes_export)  # fmt: skip


def create_app(context: ApiContext | None = None, *, web: WebMount | None = None) -> FastAPI:
    """Build the application.

    Args:
        context: The process's services; ``None`` builds the app for its schema only.
        web: Attaches the web front end, when there is one.

    Returns:
        FastAPI: The application.

    Raises:
        None
    """
    app = FastAPI(title="vakr API", version=API_VERSION, description=DESCRIPTION,
                  openapi_url=f"{API_PREFIX}/openapi.json", docs_url=None, redoc_url=None,
                  openapi_tags=TAGS)  # fmt: skip
    install_handlers(app)
    v1 = APIRouter(prefix=API_PREFIX, dependencies=[Depends(csrf_guard)], responses=COMMON_ERRORS)
    for module in _ROUTERS:
        v1.include_router(module.router)
    app.include_router(v1)
    if web is not None:
        web(app)
    app.state.vakr = context
    settings = context.settings if context is not None else ApiSettings()
    app.add_middleware(ApiMiddleware, proxies=settings.trusted_proxies,
                       max_body=settings.max_body_bytes)  # fmt: skip
    base_openapi = app.openapi

    def openapi() -> dict[str, object]:
        """Return the schema with the security schemes described (API-008).

        Args:
            None

        Returns:
            dict[str, object]: The OpenAPI document.

        Raises:
            None
        """
        if app.openapi_schema is None:
            app.openapi_schema = describe_security(base_openapi(), API_PREFIX, ANONYMOUS_PATHS)
        return app.openapi_schema

    app.openapi = openapi  # ty: ignore[invalid-assignment]
    return app
