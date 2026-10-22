"""Request plumbing around every API answer: ID, proxy trust, body limit, headers, log (15.4).

One ASGI middleware wraps the whole application, so even a refused or
failed request gets the same treatment:

- a request ID (``X-Request-ID``): taken from a trusted proxy when it is
  well formed, otherwise generated, and named in every error envelope;
- the client address and scheme after trusted-proxy handling (SEC-013);
- a bounded body: ``Content-Length`` above ``api.max_body_bytes`` is refused
  with 413 before anything is read, and a chunked body is read at most to
  the limit (SEC-019);
- the security headers of 15.4 on every response: a strict same-origin
  CSP with ``frame-ancestors 'none'``, ``nosniff``, ``DENY`` framing,
  ``no-referrer``, and ``Cache-Control: no-store`` on everything under
  ``/api/``. No CORS header is ever sent (SEC-011);
- a 500 envelope, not a traceback, for an unexpected failure, and one log
  line per request.

Dependencies:
    starlette.types: ASGI type names.
    vakr.api.middleware_parts: Headers, body replay, refusals, the access log.
    vakr.api.proxy, errors, keys: Proxy trust, the envelope, scope keys.

Safety:
    Logs carry method, path, status, duration, client, and request ID only:
    never a query string, header, cookie, or body. A failure is logged by
    exception type only.

Example:
    >>> dict(SECURITY_HEADERS)[b"x-frame-options"]
    b'DENY'
"""

import logging
import re
import time
import uuid

from starlette.types import ASGIApp, Receive, Scope, Send

from vakr.api.errors import envelope_response
from vakr.api.keys import CLIENT_KEY, PEER_KEY, REQUEST_ID_KEY
from vakr.api.middleware_parts import (
    SECURITY_HEADERS,
    decorating_send,
    log_request,
    record_request,
    refuse_peer,
    replay,
    too_large,
)
from vakr.api.proxy import ClientView, resolve_client
from vakr.api.settings import Network

__all__ = ["LOCAL_CLIENT", "SECURITY_HEADERS", "ApiMiddleware", "request_headers"]
_LOG = logging.getLogger("vakr.api.access")
_REQUEST_ID = re.compile(r"[A-Za-z0-9._-]{1,64}")
LOCAL_CLIENT = ClientView("local", "http", "local", loopback=True)
"""The client of a Unix-socket request: this host, no proxy, no TLS needed."""


def request_headers(scope: Scope) -> dict[str, str]:
    """Return request headers by lower-case name, repeated ones joined by commas.

    Args:
        scope: The ASGI scope.

    Returns:
        dict[str, str]: Header values decoded as Latin-1, as HTTP defines them.

    Raises:
        None
    """
    out: dict[str, str] = {}
    for raw_name, raw_value in scope.get("headers", ()):
        name, value = raw_name.decode("latin-1").lower(), raw_value.decode("latin-1")
        out[name] = f"{out[name]},{value}" if name in out else value
    return out


class ApiMiddleware:
    """Wraps the application with the per-request rules above."""

    def __init__(self, app: ASGIApp, *, proxies: tuple[Network, ...], max_body: int) -> None:
        """Wrap an application.

        Args:
            app: The inner ASGI application.
            proxies: ``api.trusted_proxies``.
            max_body: ``api.max_body_bytes``.

        Returns:
            None

        Raises:
            None
        """
        self.app, self.proxies, self.max_body = app, proxies, max_body
        self._warned = False

    def _client(self, scope: Scope, headers: dict[str, str]) -> ClientView:
        """Work out the client of one request, warning once about untrusted forwarding.

        Args:
            scope: The ASGI scope.
            headers: Request headers.

        Returns:
            ClientView: The client.

        Raises:
            None
        """
        if PEER_KEY in scope:
            return LOCAL_CLIENT
        peer = (scope.get("client") or ("", 0))[0]
        view = resolve_client(peer, scope.get("scheme", "http"), headers, self.proxies)
        if view.ignored and not self._warned:
            self._warned = True
            _LOG.warning("ignored forwarded headers from %s, which is not in api.trusted_proxies",
                         view.peer, extra={"event_id": "api.untrusted_forwarding"})  # fmt: skip
        return view

    def _request_id(self, headers: dict[str, str], view: ClientView) -> str:
        """Choose the request ID: a trusted proxy's, if well formed, else a new one.

        Args:
            headers: Request headers.
            view: The client view (``forwarded`` means a trusted proxy spoke).

        Returns:
            str: The ID.

        Raises:
            None
        """
        offered = headers.get("x-request-id", "")
        if view.forwarded and _REQUEST_ID.fullmatch(offered):
            return offered
        return uuid.uuid4().hex

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Handle one ASGI event.

        Args:
            scope: The ASGI scope.
            receive: Receives request messages.
            send: Sends response messages.

        Returns:
            None

        Raises:
            None
        """
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        headers = request_headers(scope)
        view = self._client(scope, headers)
        rid = self._request_id(headers, view)
        scope[REQUEST_ID_KEY], scope[CLIENT_KEY] = rid, view
        state = {"status": 0}
        decorated = decorating_send(send, rid, scope.get("path", "").startswith("/api/"), state)
        started = time.monotonic()
        peer = scope.get(PEER_KEY)
        if peer is not None and getattr(peer, "principal", None) is None:
            await refuse_peer(scope, receive, decorated, rid)
        else:
            body = await self._bounded_body(headers, receive, scope, decorated, rid)
            if body is not None:
                await self._run(scope, replay(body, receive), decorated, rid, state)
        elapsed = time.monotonic() - started
        log_request(scope, state["status"], elapsed, view, rid)
        record_request(scope, state["status"], elapsed)

    async def _bounded_body(
        self, headers: dict[str, str], receive: Receive, scope: Scope, send: Send, rid: str
    ) -> bytes | None:
        """Read the request body up to the limit, answering 413 beyond it.

        Args:
            headers: Request headers.
            receive: The original receive.
            scope: The ASGI scope.
            send: The decorating send.
            rid: Request ID.

        Returns:
            bytes | None: The body, or ``None`` when 413 was already sent.

        Raises:
            None
        """
        declared = headers.get("content-length", "")
        if declared.isdigit() and int(declared) > self.max_body:
            await too_large(scope, receive, send, rid, self.max_body)
            return None
        chunks, size, more = [], 0, True
        while more:
            message = await receive()
            if message["type"] != "http.request":
                break
            chunk = message.get("body", b"")
            size += len(chunk)
            if size > self.max_body:
                await too_large(scope, receive, send, rid, self.max_body)
                return None
            chunks.append(chunk)
            more = message.get("more_body", False)
        return b"".join(chunks)

    async def _run(
        self, scope: Scope, receive: Receive, send: Send, rid: str, state: dict[str, int]
    ) -> None:
        """Run the application, answering 500 with the envelope if it fails.

        Args:
            scope: The ASGI scope.
            receive: Receives the buffered body.
            send: The decorating send.
            rid: Request ID.
            state: Holds the response status once sent.

        Returns:
            None

        Raises:
            None
        """
        try:
            await self.app(scope, receive, send)
        except Exception as exc:  # noqa: BLE001 - every failure becomes the envelope, never a traceback
            _LOG.error("request %s failed: %s", rid, type(exc).__name__,
                       extra={"event_id": "api.request_failed"})  # fmt: skip
            if not state["status"]:
                response = envelope_response(
                    500, "internal_error", "the request failed; the log names its request ID", rid
                )
                await response(scope, receive, send)
