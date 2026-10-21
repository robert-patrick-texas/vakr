"""Repository protocols for login sessions and kiosk tokens (SEC-010, Appendix E.12).

Sessions and kiosk tokens are looked up by the SHA-256 digest of the token
a client presents; the token itself is never stored. Unlike inventory
records they are deleted: logout, revocation, and expiry remove the row, so
a revoked token can never match again. Every backend must reproduce, and
``tests/contract/contract_auth_sessions.py`` checks:

- ``add`` raises ``DuplicateError`` for a reused ID, token digest, or kiosk
  label, and ``NotFoundError`` for a session of a missing user;
- ``touch`` changes only the last-use time and ignores a missing row;
- ``purge_expired`` deletes at most ``limit`` rows per call, so a large
  backlog is cleared in bounded steps;
- per-user and kiosk lists are bounded by ``limit`` and ordered by
  ``(created_at, id)`` and ``(label, id)``.

Dependencies:
    vakr.domain: Session and kiosk records and IDs.

Safety:
    Implementations never log digests. A digest lookup is an indexed
    equality match on a value the client cannot choose a preimage for, so
    it leaks nothing useful through timing.

Example:
    >>> sorted(n for n in dir(SessionRepository) if not n.startswith("_"))[:3]
    ['add', 'delete', 'delete_for_user']
"""

from datetime import datetime
from typing import Protocol

from vakr.domain.ids import KioskTokenId, SessionId, UserId
from vakr.domain.sessions import KioskToken, Session


class SessionRepository(Protocol):
    """Server-side sessions (SEC-010)."""

    def add(self, session: Session) -> None:
        """Store a new session.

        Args:
            session: The session.

        Returns:
            None

        Raises:
            DuplicateError: If the ID or token digest is already used.
            NotFoundError: If the user does not exist.
        """
        ...

    def get(self, session_id: SessionId) -> Session | None:
        """Return one session.

        Args:
            session_id: The session's UUID.

        Returns:
            Session | None: The session, or ``None``.

        Raises:
            None
        """
        ...

    def get_by_token_hash(self, token_hash: bytes) -> Session | None:
        """Return the session whose token has this digest.

        Args:
            token_hash: SHA-256 of the presented token.

        Returns:
            Session | None: The session, or ``None``.

        Raises:
            None
        """
        ...

    def list_for_user(self, user_id: UserId, *, limit: int) -> tuple[Session, ...]:
        """List a user's sessions, oldest first ``(created_at, id)``.

        Args:
            user_id: The account.
            limit: Most sessions to return.

        Returns:
            tuple[Session, ...]: The sessions.

        Raises:
            None
        """
        ...

    def touch(self, session_id: SessionId, at: datetime) -> None:
        """Record use of a session.

        Args:
            session_id: The session.
            at: Time of use (UTC), stored as ``last_seen_at``.

        Returns:
            None

        Raises:
            None
        """
        ...

    def delete(self, session_id: SessionId) -> bool:
        """Delete one session (logout or revocation).

        Args:
            session_id: The session.

        Returns:
            bool: Whether a session was deleted.

        Raises:
            None
        """
        ...

    def delete_for_user(self, user_id: UserId) -> int:
        """Delete every session of one user.

        Args:
            user_id: The account.

        Returns:
            int: Sessions deleted.

        Raises:
            None
        """
        ...

    def purge_expired(self, *, now: datetime, idle_cutoff: datetime, limit: int) -> int:
        """Delete sessions past their absolute expiry or idle since ``idle_cutoff``.

        Args:
            now: Current time; rows with ``expires_at <= now`` go.
            idle_cutoff: ``now - idle``; rows with ``last_seen_at <= idle_cutoff`` go.
            limit: Most rows to delete in this call.

        Returns:
            int: Sessions deleted.

        Raises:
            None
        """
        ...


class KioskTokenRepository(Protocol):
    """Kiosk display tokens (E.12), listed by ``(label, id)``."""

    def add(self, token: KioskToken) -> None:
        """Store a new kiosk token.

        Args:
            token: The token record.

        Returns:
            None

        Raises:
            DuplicateError: If the ID, digest, or label is already used.
        """
        ...

    def get(self, token_id: KioskTokenId) -> KioskToken | None:
        """Return one kiosk token.

        Args:
            token_id: Its UUID.

        Returns:
            KioskToken | None: The record, or ``None``.

        Raises:
            None
        """
        ...

    def get_by_token_hash(self, token_hash: bytes) -> KioskToken | None:
        """Return the kiosk token with this digest.

        Args:
            token_hash: SHA-256 of the presented token.

        Returns:
            KioskToken | None: The record, or ``None``.

        Raises:
            None
        """
        ...

    def list_all(self, *, limit: int) -> tuple[KioskToken, ...]:
        """List kiosk tokens by ``(label, id)``.

        Args:
            limit: Most records to return.

        Returns:
            tuple[KioskToken, ...]: The records.

        Raises:
            None
        """
        ...

    def touch(self, token_id: KioskTokenId, at: datetime) -> None:
        """Record use of a kiosk token.

        Args:
            token_id: The token.
            at: Time of use (UTC).

        Returns:
            None

        Raises:
            None
        """
        ...

    def delete(self, token_id: KioskTokenId) -> bool:
        """Delete (revoke) one kiosk token.

        Args:
            token_id: The token.

        Returns:
            bool: Whether a token was deleted.

        Raises:
            None
        """
        ...

    def purge_expired(self, *, now: datetime, limit: int) -> int:
        """Delete kiosk tokens with ``expires_at <= now``.

        Args:
            now: Current time (UTC).
            limit: Most rows to delete in this call.

        Returns:
            int: Tokens deleted.

        Raises:
            None
        """
        ...

    def count(self) -> int:
        """Return how many kiosk tokens exist.

        Args:
            None

        Returns:
            int: Count.

        Raises:
            None
        """
        ...
