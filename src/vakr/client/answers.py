"""What an answer from ``vakr-api`` means to its local clients (API-007, 13.4, E.29 #10).

The API answers failures with its error envelope ``{code, message,
request_id, fields}``. ``failure`` turns one into the exception the
in-process path would have raised, so ``vakr status`` exits, and the TUI
reports, exactly as they do on the local databases:

- 422: ``ValidationError`` with the API's message (``vakr status`` exits 2);
- 503 ``storage_unavailable``: ``StorageError`` (exit 3, "cannot read the
  metadata database");
- 403 ``peer_refused``: ``SourceUnavailableError`` saying which accounts the
  socket serves and how to join ``vakr``;
- anything else: ``SourceUnavailableError`` with the status, code, and
  request ID, and where to look.

Dependencies:
    json: Bodies.
    vakr.client.transport: ``HINT``.
    vakr.domain.errors, vakr.query.source: The exceptions.

Safety:
    Envelopes are reduced to code, message (at most 512 characters), and
    request ID; nothing else from an answer reaches a message.

Example:
    >>> envelope({"code": "not_found", "message": "no device", "request_id": "ab12"})
    ('not_found', 'no device', 'ab12')
"""

import json

from vakr.client.transport import HINT
from vakr.domain.errors import StorageError, ValidationError
from vakr.query.source import SourceUnavailableError

_MESSAGE_MAX = 512
_ID_MAX = 64
REFUSED = ("this account may not use the vakr-api socket {path}: only root and members of "
           "vakr-admin or vakr may; add the account to the vakr group (sudo usermod -aG vakr "
           "NAME, then log in again), run the command with sudo, or add --local")  # fmt: skip

type Envelope = tuple[str, str, str]
"""Code, message, and request ID of an error answer; empty strings when absent."""


def _text(doc: dict[str, object], key: str, limit: int) -> str:
    """Return one text member of an object, cut to ``limit``, or ``""``.

    Args:
        doc: The object.
        key: The member.
        limit: Most characters kept.

    Returns:
        str: The text.

    Raises:
        None
    """
    value = doc.get(key)
    return value[:limit] if isinstance(value, str) else ""


def envelope(body: dict[str, object] | None) -> Envelope:
    """Read an error envelope's code, message, and request ID.

    Args:
        body: The parsed answer, or ``None`` when it was not a JSON object.

    Returns:
        Envelope: The three texts; empty when absent.

    Raises:
        None
    """
    if body is None:
        return "", "", ""
    return (_text(body, "code", _ID_MAX), _text(body, "message", _MESSAGE_MAX),
            _text(body, "request_id", _ID_MAX))  # fmt: skip


def parse_object(raw: bytes) -> dict[str, object] | None:
    """Parse a body that should be one JSON object.

    Args:
        raw: The body.

    Returns:
        dict | None: The object, or ``None`` when the body is not one.

    Raises:
        None
    """
    try:
        doc = json.loads(raw)
    except ValueError:
        return None
    return doc if isinstance(doc, dict) else None


def failure(status: int, error: Envelope, path: str) -> Exception:
    """Map an answer other than 200 to the exception the in-process path would raise.

    Args:
        status: HTTP status.
        error: The envelope's code, message, and request ID.
        path: The socket, for messages.

    Returns:
        Exception: ``ValidationError``, ``StorageError``, or ``SourceUnavailableError``.

    Raises:
        None
    """
    code, message, rid = error
    request = f"request {rid}" if rid else "no request ID"
    if status == 422:
        return ValidationError(message or "vakr-api refused the request")
    if status == 503 and code == "storage_unavailable":
        return StorageError(f"{message} (vakr-api, {request})", retryable=True)
    if status == 403 and code == "peer_refused":
        return SourceUnavailableError(REFUSED.format(path=path))
    said = f": {message}" if message else ""
    return SourceUnavailableError(
        f"vakr-api answered HTTP {status} {code or 'without an error code'}{said} ({request}); "
        f"see `journalctl -u vakr-api`; {HINT}"
    )
