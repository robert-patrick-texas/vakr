"""The web UI's pages and assets, served same-origin outside ``/api/`` (ADR-035 #3, #4, #13).

``web_mount(build)`` is the ``create_app(context, web=...)`` hook (E.30 #14):
one GET/HEAD route answers every path that is not under ``/api/``.

- A file of the build is served from memory. Content-hashed files under
  ``/_app/immutable/`` are ``Cache-Control: public, max-age=31536000,
  immutable``; everything else, HTML included, is ``no-cache`` and
  revalidates with its ``ETag`` (304).
- Any other path outside ``/_app/`` gets ``index.html`` with status 200,
  so deep links such as ``/devices/<uuid>`` reach the client router (the
  single-page-app fallback). A missing ``/_app/`` file is a 404.
- Paths under ``/api/`` never match this route, so an unknown API path
  keeps the JSON error envelope.
- HTML carries the page CSP: the API's same-origin policy with the build's
  inline bootstrap allowed by its SHA-256 hash only (no ``unsafe-inline``,
  no ``unsafe-eval``). The middleware adds the other 15.4 headers.
- A client that accepts ``br`` or ``gzip`` gets the build's precompressed
  variant, with ``Vary: Accept-Encoding``.

``mount_web(directory)`` loads the build and returns the hook, or ``None``
after logging once (``api.web_unavailable``) when there is no usable build:
the API then serves without pages.

Dependencies:
    starlette: Routing and responses.
    vakr.api.web_files: The in-memory build.
    vakr.api.middleware_parts: The CSP.

Safety:
    Request paths are looked up in the in-memory index and never joined to
    a filesystem path, so traversal is impossible. Pages never change state;
    every change goes through the JSON API with its CSRF check.

Example:
    >>> accepted("gzip, deflate, br;q=0.5")
    ('br', 'gzip')
"""

import logging
from pathlib import Path

from fastapi import FastAPI
from starlette.exceptions import HTTPException
from starlette.requests import Request
from starlette.responses import Response
from starlette.routing import Match, Route
from starlette.types import Scope

from vakr.api.app import WebMount
from vakr.api.middleware_parts import content_security_policy
from vakr.api.web_files import IMMUTABLE_PREFIX, WebBuild, WebBuildError, WebFile, load_web_build

_LOG = logging.getLogger(__name__)
IMMUTABLE = "public, max-age=31536000, immutable"
REVALIDATE = "no-cache"
_PREFERRED = ("br", "gzip")


def accepted(header: str) -> tuple[str, ...]:
    """Return the content codings a client accepts, in vakr's order of preference.

    Args:
        header: ``Accept-Encoding``.

    Returns:
        tuple[str, ...]: ``br`` and/or ``gzip``; a coding with ``q=0`` is refused.

    Raises:
        None
    """
    offered: set[str] = set()
    for item in header.split(","):
        name, _, params = item.strip().partition(";")
        refused = params.replace(" ", "").lower() in {"q=0", "q=0.0", "q=0.00", "q=0.000"}
        if name.strip().lower() and not refused:
            offered.add(name.strip().lower())
    return tuple(c for c in _PREFERRED if c in offered or "*" in offered)


class _PageRoute(Route):
    """A catch-all route that never matches a path under ``/api/``."""

    def matches(self, scope: Scope) -> tuple[Match, Scope]:
        """Refuse API paths so they keep the API's 404 and 405 envelopes.

        Args:
            scope: The ASGI scope.

        Returns:
            tuple[Match, Scope]: No match for ``/api`` and below, else the usual result.

        Raises:
            None
        """
        path = scope.get("path", "")
        if scope.get("type") == "http" and (path == "/api" or path.startswith("/api/")):
            return Match.NONE, {}
        return super().matches(scope)


def _answer(request: Request, file: WebFile, cache: str, csp: str | None) -> Response:
    """Build the response for one file: coding, validators, cache, and CSP headers.

    Args:
        request: The request.
        file: The file.
        cache: ``Cache-Control`` value.
        csp: Page CSP for HTML, else ``None`` (the middleware's default applies).

    Returns:
        Response: 200, or 304 when ``If-None-Match`` names this version.

    Raises:
        None
    """
    coding = next((c for c in accepted(request.headers.get("accept-encoding", ""))
                   if c in file.encoded), None)  # fmt: skip
    etag = file.etag if coding is None else f'{file.etag[:-1]}-{coding}"'
    headers = {"cache-control": cache, "etag": etag}
    if file.encoded:
        headers["vary"] = "Accept-Encoding"
    if csp is not None:
        headers["content-security-policy"] = csp
    if etag in {t.strip() for t in request.headers.get("if-none-match", "").split(",")}:
        return Response(status_code=304, headers=headers)
    if coding is not None:
        headers["content-encoding"] = coding
    body = file.body if coding is None else file.encoded[coding]
    return Response(body, media_type=file.content_type, headers=headers)


def web_mount(build: WebBuild) -> WebMount:
    """Return the hook that adds the page route to the application.

    Args:
        build: The loaded web build.

    Returns:
        WebMount: Adds one catch-all GET/HEAD route outside ``/api/``.

    Raises:
        None
    """
    csp = content_security_policy(build.script_hashes)

    async def page(request: Request) -> Response:
        """Serve a build file, or the fallback page for a client-side route.

        Args:
            request: The request.

        Returns:
            Response: The file or ``index.html``.

        Raises:
            HTTPException: 404 for a missing ``/_app/`` file.
        """
        rel = request.url.path.lstrip("/")
        file = build.files.get(rel)
        if file is not None:
            cache = IMMUTABLE if rel.startswith(IMMUTABLE_PREFIX) else REVALIDATE
            html = file.content_type.startswith("text/html")
            return _answer(request, file, cache, csp if html else None)
        if rel.startswith("_app/"):
            raise HTTPException(404)
        return _answer(request, build.index, REVALIDATE, csp)

    def attach(app: FastAPI) -> None:
        """Append the route after the API's, so API routes always win.

        Args:
            app: The application.

        Returns:
            None

        Raises:
            None
        """
        app.router.routes.append(_PageRoute("/{path:path}", page, methods=["GET", "HEAD"]))

    return attach


def mount_web(directory: Path) -> WebMount | None:
    """Load the build in ``directory`` and return its hook, or log once why not.

    Args:
        directory: The build directory.

    Returns:
        WebMount | None: The hook, or ``None`` when there is no usable build.

    Raises:
        None
    """
    try:
        build = load_web_build(directory)
    except WebBuildError as exc:
        _LOG.warning("web UI not served: %s; the API serves without pages", exc,
                     extra={"event_id": "api.web_unavailable"})  # fmt: skip
        return None
    _LOG.info("web UI: %d files from %s", len(build.files), directory,
              extra={"event_id": "api.web_ready"})  # fmt: skip
    return web_mount(build)
