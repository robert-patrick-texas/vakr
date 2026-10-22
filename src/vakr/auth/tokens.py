"""Opaque session and kiosk tokens: creation and digests (SEC-010, Appendix E.12).

A token is 256 random bits from ``secrets``, URL-safe base64, behind a
short prefix: ``vks_`` for browser sessions, ``vkk_`` for kiosk displays,
``vkr_`` for a partner node that replicates from this one (E.13).
The prefix tells the server which table to look in and lets secret
scanners recognise a leaked token. The server stores only the SHA-256
digest; the token is shown to the client once and never again.

Dependencies:
    hashlib, secrets: Digests and randomness.
    vakr.credentials.secret: ``Secret``, which keeps tokens out of logs and reprs.

Safety:
    Tokens travel as ``Secret`` everywhere. Oversized or non-ASCII input is
    refused before hashing, so a hostile client cannot make the server hash
    megabytes.

Example:
    >>> token = new_token(SESSION_PREFIX)
    >>> token, token_kind(token), len(token_digest(token) or b"")
    (Secret('***'), 'session', 32)
"""

import hashlib
import secrets

from vakr.credentials.secret import Secret

SESSION_PREFIX = "vks_"
KIOSK_PREFIX = "vkk_"
REPLICATION_PREFIX = "vkr_"
TOKEN_BYTES = 32  # 256 bits: twice the 128-bit floor, at no measurable cost
MAX_TOKEN_LENGTH = 128
_KINDS = {SESSION_PREFIX: "session", KIOSK_PREFIX: "kiosk", REPLICATION_PREFIX: "replication"}


def new_token(prefix: str) -> Secret:
    """Create a fresh opaque token.

    Args:
        prefix: ``SESSION_PREFIX``, ``KIOSK_PREFIX``, or ``REPLICATION_PREFIX``.

    Returns:
        Secret: The token; show it to the client once.

    Raises:
        ValueError: If ``prefix`` is not one of them.
    """
    if prefix not in _KINDS:
        raise ValueError("unknown token prefix")
    return Secret(prefix + secrets.token_urlsafe(TOKEN_BYTES))


def token_kind(token: Secret) -> str | None:
    """Name the kind of a presented token from its prefix.

    Args:
        token: The presented token.

    Returns:
        str | None: ``"session"``, ``"kiosk"``, ``"replication"``, or ``None``.

    Raises:
        None
    """
    return _KINDS.get(token.reveal()[:4])


def token_digest(token: Secret) -> bytes | None:
    """Return the SHA-256 digest under which a token is stored.

    Args:
        token: The presented token.

    Returns:
        bytes | None: 32 bytes, or ``None`` for empty, oversized, or
        non-ASCII input, which no issued token can be.

    Raises:
        None
    """
    text = token.reveal()
    if not text or len(text) > MAX_TOKEN_LENGTH or not text.isascii():
        return None
    return hashlib.sha256(text.encode("ascii")).digest()
