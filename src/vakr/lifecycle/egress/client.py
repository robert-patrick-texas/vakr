"""The shared outbound HTTPS client: bounded GETs, streaming downloads, redirects.

Release checks, release downloads, and (later) peer replication all go
through this one client so proxies, trusted CAs, timeouts, and size limits
are configured once (EGRESS-001). GitHub release assets redirect from
``github.com`` to a ``*.githubusercontent.com`` host, so redirects are
followed, but only to other HTTPS URLs and only a bounded number of times.

Dependencies:
    http.client: HTTP/1.1 framing (chunked bodies, headers).
    vakr.lifecycle.egress.transport: Proxy-aware, verifying connections.

Safety:
    Every connection verifies the server certificate and host name with the
    caller's context. Bodies are read with a hard byte limit; downloads are
    written to a ``.part`` file and renamed only when complete, so a partial
    file is never mistaken for a finished one. No credentials are sent.

Example:
    >>> client = HttpsClient(EgressSettings(context, ProxyPolicy(None)))  # doctest: +SKIP
    >>> client.get("https://api.github.com/").status  # doctest: +SKIP
    200
"""

import http.client
import json
import ssl
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlsplit

from vakr.lifecycle.egress.errors import EgressError, ResponseTooLargeError
from vakr.lifecycle.egress.proxy import ProxyPolicy
from vakr.lifecycle.egress.transport import open_stream, tls_wrap

REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})
CHUNK_BYTES = 64 * 1024


@dataclass(frozen=True, slots=True)
class EgressSettings:
    """Everything that shapes outbound HTTPS for one process.

    Attributes:
        context: Verifying TLS context (``truststore.build_context``).
        proxies: Proxy selection policy.
        timeout: Per-operation socket timeout in seconds.
        max_body_bytes: Limit for in-memory responses such as API JSON.
        max_redirects: Redirects followed before giving up.
        user_agent: ``User-Agent`` header (GitHub requires one).
        strict_x509: Whether ``context`` enforces the strict X.509 profile.
    """

    context: ssl.SSLContext
    proxies: ProxyPolicy = field(default_factory=lambda: ProxyPolicy(None))
    timeout: float = 30.0
    max_body_bytes: int = 4 * 1024 * 1024
    max_redirects: int = 5
    user_agent: str = "lifecycle-egress"
    strict_x509: bool = True


@dataclass(frozen=True, slots=True)
class Response:
    """A complete, size-bounded HTTP response.

    Attributes:
        url: Final URL after redirects.
        status: HTTP status code.
        headers: Response headers with lower-cased names.
        body: Response body.
    """

    url: str
    status: int
    headers: Mapping[str, str]
    body: bytes

    def json(self) -> Any:
        """Decode the body as JSON.

        Args:
            None

        Returns:
            Any: The decoded document.

        Raises:
            EgressError: Step ``"http"`` when the body is not valid JSON.
        """
        try:
            return json.loads(self.body)
        except ValueError as exc:
            raise EgressError("http", f"{self.url}: response is not valid JSON") from exc


class _VerifiedConnection(http.client.HTTPSConnection):
    """``HTTPSConnection`` whose socket comes from the shared transport."""

    def __init__(self, host: str, port: int, settings: EgressSettings) -> None:
        """Prepare a connection; nothing is opened until the first request.

        Args:
            host: Destination host.
            port: Destination port.
            settings: Shared egress settings.

        Returns:
            None

        Raises:
            None
        """
        super().__init__(host, port, timeout=settings.timeout, context=settings.context)
        self._settings = settings

    def connect(self) -> None:
        """Open DNS, TCP, optional CONNECT tunnel, and verified TLS.

        Args:
            None

        Returns:
            None

        Raises:
            EgressError: From whichever step fails.
        """
        proxy = self._settings.proxies.proxy_for(self.host, self.port)
        raw = open_stream(self.host, self.port, proxy, self._settings.timeout)
        self.sock = tls_wrap(raw, self._settings.context, self.host, self.port)


class HttpsClient:
    """Bounded HTTPS GET and download over the shared egress settings.

    Instances hold no connection state between calls and are safe to reuse
    sequentially; they are not designed for concurrent use from threads.
    """

    def __init__(self, settings: EgressSettings) -> None:
        """Bind the client to its settings.

        Args:
            settings: Shared egress settings.

        Returns:
            None

        Raises:
            None
        """
        self.settings = settings

    def get(self, url: str, headers: Mapping[str, str] | None = None) -> Response:
        """Fetch ``url`` into memory, following redirects.

        Args:
            url: HTTPS URL.
            headers: Extra request headers.

        Returns:
            Response: Final response of any status; callers decide what a
            non-2xx status means.

        Raises:
            EgressError: On connection, TLS, redirect, or protocol failures.
            ResponseTooLargeError: When the body exceeds ``max_body_bytes``.
        """
        reply, conn, final = self._open(url, headers or {})
        limit = self.settings.max_body_bytes
        try:
            body = reply.read(limit + 1)
        except (OSError, http.client.HTTPException) as exc:
            raise EgressError("http", f"{final}: {exc}") from exc
        finally:
            conn.close()
        if len(body) > limit:
            raise ResponseTooLargeError(final, limit)
        return Response(final, reply.status, _lower_headers(reply), body)

    def download(
        self, url: str, dest: Path, max_bytes: int, headers: Mapping[str, str] | None = None
    ) -> int:
        """Stream ``url`` to ``dest`` without holding it in memory.

        Args:
            url: HTTPS URL; redirects are followed.
            dest: Final file path; written via ``dest.part`` then renamed.
            max_bytes: Hard size limit for the file.
            headers: Extra request headers.

        Returns:
            int: Number of bytes written.

        Raises:
            EgressError: On transport failure or a non-200 status.
            ResponseTooLargeError: When the file exceeds ``max_bytes``.
            OSError: When ``dest`` cannot be written.
        """
        reply, conn, final = self._open(url, headers or {})
        if reply.status != 200:
            conn.close()
            raise EgressError("http", f"HTTP {reply.status} downloading {final}")
        part = dest.with_name(f"{dest.name}.part")
        try:
            written = _stream_to(reply, part, max_bytes, final)
        except BaseException:
            part.unlink(missing_ok=True)
            raise
        finally:
            conn.close()
        part.replace(dest)
        return written

    def _open(
        self, url: str, headers: Mapping[str, str]
    ) -> tuple[http.client.HTTPResponse, _VerifiedConnection, str]:
        """Send GET requests along the redirect chain until a final response.

        Args:
            url: Starting HTTPS URL.
            headers: Extra request headers.

        Returns:
            tuple: The unread final response, its open connection, and the
            final URL.

        Raises:
            EgressError: For non-HTTPS URLs, too many redirects, or I/O errors.
        """
        for _ in range(self.settings.max_redirects + 1):
            parts = urlsplit(url)
            if parts.scheme != "https" or not parts.hostname:
                raise EgressError("http", f"refusing non-HTTPS URL {url!r}")
            conn = _VerifiedConnection(parts.hostname, parts.port or 443, self.settings)
            target = parts.path or "/"
            target += f"?{parts.query}" if parts.query else ""
            sent = {"User-Agent": self.settings.user_agent, **headers}
            try:
                conn.request("GET", target, headers=sent)
                reply = conn.getresponse()
            except (OSError, http.client.HTTPException) as exc:
                conn.close()
                raise EgressError("http", f"GET {url}: {exc}") from exc
            location = reply.getheader("Location")
            if reply.status not in REDIRECT_STATUSES or not location:
                return reply, conn, url
            conn.close()
            url = urljoin(url, location)
        raise EgressError("http", f"too many redirects (>{self.settings.max_redirects})")


def _stream_to(reply: http.client.HTTPResponse, part: Path, max_bytes: int, url: str) -> int:
    """Copy a response body to ``part`` in chunks, enforcing ``max_bytes``.

    Args:
        reply: Unread response.
        part: Temporary destination file.
        max_bytes: Hard size limit.
        url: URL for error messages.

    Returns:
        int: Bytes written.

    Raises:
        ResponseTooLargeError: When the body exceeds ``max_bytes``.
        EgressError: When the connection fails mid-body.
    """
    written = 0
    with part.open("wb") as out:
        while True:
            try:
                chunk = reply.read(CHUNK_BYTES)
            except (OSError, http.client.HTTPException) as exc:
                raise EgressError("http", f"{url}: download interrupted: {exc}") from exc
            if not chunk:
                return written
            written += len(chunk)
            if written > max_bytes:
                raise ResponseTooLargeError(url, max_bytes)
            out.write(chunk)


def _lower_headers(reply: http.client.HTTPResponse) -> dict[str, str]:
    """Return response headers keyed by lower-cased name.

    Args:
        reply: Response whose headers to copy.

    Returns:
        dict[str, str]: Header map; repeated headers keep the last value.

    Raises:
        None
    """
    return {name.lower(): value for name, value in reply.getheaders()}
