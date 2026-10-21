"""The pieces of the API middleware: security headers, body replay, refusals, access log (15.4).

``vakr.api.middleware`` wraps every request; this module holds what it
sends and logs: the header set of 15.4 added to every response start, the
replay of a body read up to its limit, the 413 and socket-refusal answers,
and the one access-log line per request.

Dependencies:
    starlette.types: ASGI type names.
    vakr.api.errors, vakr.api.proxy: The envelope and the client view.

Safety:
    The access log carries method, path, status, duration, client, and
    request ID only: never a query string, header, cookie, or body.

Example:
    >>> dict(SECURITY_HEADERS)[b"x-frame-options"]
    b'DENY'
"""

import logging
from collections.abc import Awaitable, Callable, Sequence

from starlette.types import Message, Receive, Scope, Send

from vakr.api.errors import envelope_response
from vakr.api.proxy import ClientView

_LOG = logging.getLogger("vakr.api.access")

_CSP_DIRECTIVES = (
    ("default-src", "'self'"), ("script-src", "'self'"), ("style-src", "'self'"),
    ("img-src", "'self' data:"), ("font-src", "'self'"), ("connect-src", "'self'"),
    ("object-src", "'none'"), ("base-uri", "'none'"), ("form-action", "'self'"),
    ("frame-ancestors", "'none'"),
)  # fmt: skip


def content_security_policy(script_hashes: Sequence[str] = ()) -> str:
    """Return the CSP, allowing extra inline scripts only by their SHA-256 hash (ADR-035 #13).

    Args:
        script_hashes: ``sha256-<base64>`` sources of the web build's inline
            bootstrap scripts; never ``unsafe-inline`` or ``unsafe-eval``.

    Returns:
        str: The header value.

    Raises:
        None
    """
    extra = "".join(f" '{h}'" for h in script_hashes)
    parts = [f"{name} {value}{extra if name == 'script-src' else ''}"
             for name, value in _CSP_DIRECTIVES]  # fmt: skip
    return "; ".join(parts)


CSP = content_security_policy()
"""Same-origin only: no inline script, no CDN (UI-012), no framing (15.4)."""
SECURITY_HEADERS: tuple[tuple[bytes, bytes], ...] = (
    (b"content-security-policy", CSP.encode("ascii")),
    (b"x-content-type-options", b"nosniff"),
    (b"x-frame-options", b"DENY"),
    (b"referrer-policy", b"no-referrer"),
    (b"cross-origin-opener-policy", b"same-origin"),
    (b"cross-origin-resource-policy", b"same-origin"),
    (b"permissions-policy", b"camera=(), microphone=(), geolocation=(), payment=(), usb=()"),
)
NO_STORE = (b"cache-control", b"no-store")


def decorating_send(send: Send, rid: str, api: bool, state: dict[str, int]) -> Send:
    """Return a ``send`` that adds the request ID and security headers.

    Args:
        send: The server's send.
        rid: Request ID.
        api: Whether the path is under ``/api/`` (gets ``no-store``).
        state: Records the status.

    Returns:
        Send: The wrapper.

    Raises:
        None
    """

    async def decorated(message: Message) -> None:
        """Add headers to the response start, then pass every message on.

        Args:
            message: An ASGI message.

        Returns:
            None

        Raises:
            None
        """
        if message["type"] == "http.response.start":
            state["status"] = int(message["status"])
            present = {name.lower() for name, _ in message.get("headers", ())}
            extra = [h for h in SECURITY_HEADERS if h[0] not in present]
            if api and NO_STORE[0] not in present:
                extra.append(NO_STORE)
            extra.append((b"x-request-id", rid.encode("ascii")))
            message = {**message, "headers": [*message.get("headers", ()), *extra]}
        await send(message)

    return decorated


def replay(body: bytes, receive: Receive) -> Callable[[], Awaitable[Message]]:
    """Return a ``receive`` that yields the buffered body once, then defers to the server.

    Args:
        body: The whole request body.
        receive: The server's receive, for ``http.disconnect`` afterwards.

    Returns:
        Callable: The replaying receive.

    Raises:
        None
    """
    pending = [{"type": "http.request", "body": body, "more_body": False}]

    async def replay() -> Message:
        """Return the body first, then whatever the server sends.

        Args:
            None

        Returns:
            Message: An ASGI message.

        Raises:
            None
        """
        return pending.pop() if pending else await receive()

    return replay


async def too_large(scope: Scope, receive: Receive, send: Send, rid: str, limit: int) -> None:
    """Answer 413 with the envelope.

    Args:
        scope: The ASGI scope.
        receive: Receive callable.
        send: The decorating send.
        rid: Request ID.
        limit: The body limit.

    Returns:
        None

    Raises:
        None
    """
    message = f"request bodies are limited to {limit} bytes (api.max_body_bytes)"
    await envelope_response(413, "body_too_large", message, rid)(scope, receive, send)


async def refuse_peer(scope: Scope, receive: Receive, send: Send, rid: str) -> None:
    """Answer 403 to a socket peer that is not root and in no vakr group (E.12).

    Args:
        scope: The ASGI scope.
        receive: Receive callable.
        send: The decorating send.
        rid: Request ID.

    Returns:
        None

    Raises:
        None
    """
    message = ("this account may not use the local API socket: only root and members of "
               "vakr-admin (admin), vakr-view, or vakr (operator) may")  # fmt: skip
    await envelope_response(403, "peer_refused", message, rid)(scope, receive, send)


def log_request(scope: Scope, status: int, elapsed: float, view: ClientView, rid: str) -> None:
    """Write the access log line: errors and changes at INFO, reads at DEBUG.

    Args:
        scope: The ASGI scope.
        status: Response status (0 when the client went away first).
        elapsed: Seconds taken.
        view: The client.
        rid: Request ID.

    Returns:
        None

    Raises:
        None
    """
    method = scope.get("method", "?")
    level = logging.INFO if status >= 400 or method not in ("GET", "HEAD") else logging.DEBUG
    if _LOG.isEnabledFor(level):
        _LOG.log(level, "%s %s %d %.0f ms client %s request %s", method, scope.get("path", ""),
                 status, elapsed * 1000, view.address, rid,
                 extra={"event_id": "api.request"})  # fmt: skip
