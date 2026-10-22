"""In-memory session repository (SEC-010, Appendix E.12).

The reference implementation of ``SessionRepository``: unversioned rows
staged in the unit of work's transaction, found by full scans, and deleted
with tombstones.

Dependencies:
    vakr.adapters.memory.metadata_store: The transaction.
    vakr.domain: Session and kiosk records and errors.

Safety:
    Digests are compared, never logged or echoed in errors.

Example:
    >>> from vakr.adapters.memory.metadata_store import MemoryMetadataStore, Transaction
    >>> MemorySessionRepository(Transaction(MemoryMetadataStore())).get_by_token_hash(bytes(32))
"""

from dataclasses import replace
from datetime import datetime

from vakr.adapters.memory.metadata_store import Transaction
from vakr.domain.errors import DuplicateError, NotFoundError
from vakr.domain.ids import SessionId, UserId
from vakr.domain.sessions import Session
from vakr.domain.timestamps import to_epoch_us


class MemorySessionRepository:
    """Server-side sessions."""

    def __init__(self, tx: Transaction) -> None:
        """Bind the repository to a transaction.

        Args:
            tx: The unit of work's transaction.

        Returns:
            None

        Raises:
            None
        """
        self._tx = tx

    def add(self, session: Session) -> None:
        """Stage a new session.

        Args:
            session: The session.

        Returns:
            None

        Raises:
            DuplicateError, NotFoundError: As the port documents.
        """
        if self._tx.get("session", session.id) is not None:
            raise DuplicateError("session", "id", session.id)
        if self.get_by_token_hash(session.token_hash) is not None:
            raise DuplicateError("session", "token", "-")
        if self._tx.get("user", session.user_id) is None:
            raise NotFoundError("user", session.user_id)
        self._tx.put("session", session, None)

    def get(self, session_id: SessionId) -> Session | None:
        """Return one session.

        Args:
            session_id: The UUID.

        Returns:
            Session | None: The session, or ``None``.

        Raises:
            None
        """
        return self._tx.get("session", session_id)

    def get_by_token_hash(self, token_hash: bytes) -> Session | None:
        """Return the session with this token digest.

        Args:
            token_hash: SHA-256 of the token.

        Returns:
            Session | None: The session, or ``None``.

        Raises:
            None
        """
        return next((s for s in self._tx.records("session") if s.token_hash == token_hash), None)

    def list_for_user(self, user_id: UserId, *, limit: int) -> tuple[Session, ...]:
        """List one user's sessions, oldest first.

        Args:
            user_id: The account.
            limit: Most sessions to return.

        Returns:
            tuple[Session, ...]: The sessions.

        Raises:
            None
        """
        mine = [s for s in self._tx.records("session") if s.user_id == user_id]
        mine.sort(key=lambda s: (to_epoch_us(s.created_at), s.id.int))
        return tuple(mine[: max(limit, 0)])

    def touch(self, session_id: SessionId, at: datetime) -> None:
        """Record use of a session; a missing one is ignored.

        Args:
            session_id: The session.
            at: Time of use.

        Returns:
            None

        Raises:
            None
        """
        if (stored := self.get(session_id)) is not None:
            self._tx.put("session", replace(stored, last_seen_at=at), None)

    def delete(self, session_id: SessionId) -> bool:
        """Delete one session.

        Args:
            session_id: The session.

        Returns:
            bool: Whether it existed.

        Raises:
            None
        """
        if self.get(session_id) is None:
            return False
        self._tx.delete("session", session_id)
        return True

    def delete_for_user(self, user_id: UserId) -> int:
        """Delete every session of one user.

        Args:
            user_id: The account.

        Returns:
            int: Sessions deleted.

        Raises:
            None
        """
        mine = [s.id for s in self._tx.records("session") if s.user_id == user_id]
        for key in mine:
            self._tx.delete("session", key)
        return len(mine)

    def purge_expired(self, *, now: datetime, idle_cutoff: datetime, limit: int) -> int:
        """Delete up to ``limit`` expired or idle sessions, earliest expiry first.

        Args:
            now: Current time.
            idle_cutoff: ``now - idle``.
            limit: Most rows to delete.

        Returns:
            int: Sessions deleted.

        Raises:
            None
        """
        dead = sorted(
            (
                s
                for s in self._tx.records("session")
                if s.expires_at <= now or s.last_seen_at <= idle_cutoff
            ),
            key=lambda s: (to_epoch_us(s.expires_at), s.id.int),
        )[: max(limit, 0)]
        for session in dead:
            self._tx.delete("session", session.id)
        return len(dead)

    def count(self) -> int:
        """Return how many sessions are stored (E.33).

        Args:
            None

        Returns:
            int: Count.

        Raises:
            None
        """
        return len(self._tx.records("session"))
