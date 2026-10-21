"""CSRF tokens bound to a session (SEC-011, blueprint 15.4).

Every state-changing browser request must carry a token that a forged
cross-site request cannot know. vakr derives it instead of storing it:

    csrf_token = base64url(HMAC-SHA-256(csrf_key, "vakr-csrf-v1" || session_id))

``csrf_key`` comes from the installation key in ``/var/lib/vakr/auth.key``
(``vakr.auth.keys``). The server can render the token into any page from
the session alone, a new session (every login, every rotation) gets a new
token, and a token from one session is useless in another. A leaked token
reveals neither the session cookie nor the key.

Dependencies:
    base64, hashlib, hmac: Derivation and constant-time comparison.

Safety:
    ``verify`` compares with ``hmac.compare_digest`` and rejects oversized
    input before comparing, so timing reveals nothing about the expected
    token.

Example:
    >>> from uuid import UUID
    >>> guard = CsrfGuard(bytes(32))
    >>> sid = UUID(int=1)
    >>> guard.verify(sid, guard.token_for(sid)), guard.verify(sid, "forged")
    (True, False)
"""

import base64
import hashlib
import hmac
from uuid import UUID

MAX_CSRF_LENGTH = 128
_LABEL = b"vakr-csrf-v1\x00"


class CsrfGuard:
    """Issues and checks CSRF tokens for sessions."""

    __slots__ = ("_key",)

    def __init__(self, key: bytes) -> None:
        """Create a guard with the derived CSRF key.

        Args:
            key: ``AuthKeys.csrf``.

        Returns:
            None

        Raises:
            ValueError: If the key is shorter than 32 bytes.
        """
        if len(key) < 32:
            raise ValueError("the CSRF key must be at least 32 bytes")
        self._key = key

    def __repr__(self) -> str:
        """Render without the key.

        Args:
            None

        Returns:
            str: ``CsrfGuard(...)``.

        Raises:
            None
        """
        return "CsrfGuard(...)"

    def token_for(self, session_id: UUID) -> str:
        """Return the CSRF token of a session.

        Args:
            session_id: The session's UUID.

        Returns:
            str: 43 URL-safe characters, for a hidden form field or a header.

        Raises:
            None
        """
        mac = hmac.digest(self._key, _LABEL + session_id.bytes, hashlib.sha256)
        return base64.urlsafe_b64encode(mac).rstrip(b"=").decode("ascii")

    def verify(self, session_id: UUID, presented: str | None) -> bool:
        """Check a presented CSRF token in constant time.

        Args:
            session_id: The session the request authenticated as.
            presented: Token from the form field or header, or ``None``.

        Returns:
            bool: ``True`` only for the session's own token.

        Raises:
            None
        """
        if not presented or len(presented) > MAX_CSRF_LENGTH or not presented.isascii():
            return False
        expected = self.token_for(session_id)
        return hmac.compare_digest(expected.encode("ascii"), presented.encode("ascii"))
