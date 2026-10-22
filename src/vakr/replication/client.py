"""Bounded HTTPS GETs to the partner node with its replication token (MPOS-006, ADR-024).

``PartnerClient`` is what every replication worker uses (the peer's
metadata pull, and both nodes' observation pull): one base URL, the token
this node was given by its partner, and the shared egress settings, so the
proxy and the private CAs of ``/etc/vakr/tls/ca.d`` apply (EGRESS-004).
Certificate and host-name checks are always on: the settings come from
``vakr.lifecycle.egress``, which offers no way to turn them off. Redirects
are never followed, so the token cannot travel to another host.

Dependencies:
    vakr.lifecycle.egress: Strict-TLS client, settings, and errors.

Safety:
    The token is a ``Secret``; it appears only in the ``Authorization``
    header, never in a URL, a log line, an exception, or a ``repr``.

Example:
    >>> PartnerError(503, "busy", "try later").code
    'busy'
"""

import dataclasses
from collections.abc import Mapping
from typing import Any, Protocol
from urllib.parse import urlencode, urlsplit

from vakr.credentials.secret import Secret
from vakr.lifecycle.egress.client import EgressSettings, HttpsClient
from vakr.lifecycle.egress.errors import EgressError

MAX_ANSWER_BYTES = 8 * 1024 * 1024
"""Largest answer read; a 1,000-row page of interfaces is about 1 MB (E.13)."""
TIMEOUT_S = 20.0
"""Per-operation socket timeout for a pull (E.13)."""
PREFIX = "/api/v1/replication/"


class PartnerError(Exception):
    """The partner answered, but not with a usable 200.

    Attributes:
        status: HTTP status.
        code: The error envelope's ``code``, or ``""``.
        message: The envelope's ``message``, or a short description.
    """

    def __init__(self, status: int, code: str, message: str) -> None:
        """Record the answer.

        Args:
            status: HTTP status.
            code: Envelope code.
            message: Envelope message.

        Returns:
            None

        Raises:
            None
        """
        super().__init__(f"partner answered HTTP {status} {code}: {message}".strip())
        self.status, self.code, self.message = status, code, message


class PartnerReader(Protocol):
    """What a puller needs of the partner: its address and bounded JSON reads."""

    base_url: str

    def get_json(self, path: str, query: Mapping[str, str | int] | None = None) -> Any:
        """GET one replication endpoint and return its parsed JSON.

        Args:
            path: Relative to ``/api/v1/replication/``.
            query: Its parameters.

        Returns:
            Any: The parsed answer.

        Raises:
            PartnerError, EgressError: As ``PartnerClient.get_json``.
        """
        ...


class PartnerClient:
    """GETs to one partner node's ``/api/v1/replication/*``."""

    def __init__(self, base_url: str, token: Secret, settings: EgressSettings) -> None:
        """Bind to the partner.

        Args:
            base_url: The partner's ``https://`` base URL (``node.primary_url`` on a peer).
            token: The replication token the partner issued for this node.
            settings: Shared egress settings; timeout, size, and redirects are tightened here.

        Returns:
            None

        Raises:
            ValueError: If ``base_url`` is not ``https://host[:port][/path]``.
        """
        parts = urlsplit(base_url)
        if parts.scheme != "https" or not parts.hostname or parts.query or parts.username:
            raise ValueError("the partner URL must be https://host[:port][/path]")
        self.base_url = base_url.rstrip("/")
        self._token = token
        tight = dataclasses.replace(settings, timeout=TIMEOUT_S, max_body_bytes=MAX_ANSWER_BYTES,
                                    max_redirects=0)  # fmt: skip
        self._http = HttpsClient(tight)

    def __repr__(self) -> str:
        """Show the partner, never the token.

        Args:
            None

        Returns:
            str: ``PartnerClient('https://...')``.

        Raises:
            None
        """
        return f"PartnerClient({self.base_url!r})"

    def get_json(self, path: str, query: Mapping[str, str | int] | None = None) -> Any:
        """GET one replication endpoint and decode its JSON.

        Args:
            path: Under ``/api/v1/replication/``, such as ``metadata/state``.
            query: Query parameters; ``None`` values are dropped.

        Returns:
            Any: The decoded body of a 200 answer.

        Raises:
            PartnerError: For any other status (the envelope's code and message).
            EgressError: For TLS, connection, timeout, redirect, size, or JSON failures.
        """
        params = {k: v for k, v in (query or {}).items() if v is not None}
        url = f"{self.base_url}{PREFIX}{path.lstrip('/')}"
        if params:
            url += f"?{urlencode(params)}"
        headers = {"Authorization": f"Bearer {self._token.reveal()}", "Accept": "application/json"}
        reply = self._http.get(url, headers)
        if reply.status != 200:
            raise _failure(reply.status, reply.body)
        return reply.json()


def _failure(status: int, body: bytes) -> PartnerError:
    """Read the partner's error envelope, if it sent one.

    Args:
        status: HTTP status.
        body: The body (bounded by the client).

    Returns:
        PartnerError: The failure.

    Raises:
        None
    """
    import json

    try:
        doc = json.loads(body[:4096] or b"{}")
    except ValueError, RecursionError:
        doc = {}
    doc = doc if isinstance(doc, dict) else {}
    code, message = doc.get("code"), doc.get("message")
    text = message[:300] if isinstance(message, str) else "no error envelope"
    return PartnerError(status, code if isinstance(code, str) else "", text)


__all__ = ["MAX_ANSWER_BYTES", "EgressError", "PartnerClient", "PartnerError", "PartnerReader"]
