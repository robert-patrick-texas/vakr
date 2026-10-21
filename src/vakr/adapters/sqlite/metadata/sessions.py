"""SQLite session repository (SEC-010, Appendix E.12).

Sessions live in ``auth_session`` (migration ``0003``). A request finds its
session by the token digest through the ``UNIQUE`` index; a touch rewrites
one column; logout, revocation, and expiry delete the row. Expiry purges
delete through a bounded ``LIMIT`` subquery, because ``DELETE ... LIMIT``
is a compile-time option SQLite builds usually lack.

Dependencies:
    vakr.adapters.sqlite.encoding, errors, metadata.transaction.
    vakr.domain: The session record, IDs, and errors.

Safety:
    SQL is fixed text with bound values. Digests are bound, never formatted
    into messages.

Example:
    >>> SqliteSessionRepository(tx).get_by_token_hash(bytes(32))  # doctest: +SKIP
"""

import sqlite3
from datetime import datetime

from vakr.adapters.sqlite.encoding import blob_to_uuid, decode_time, encode_time
from vakr.adapters.sqlite.errors import ErrorContext
from vakr.adapters.sqlite.metadata.transaction import SqliteTransaction
from vakr.domain.errors import DuplicateError, StorageError
from vakr.domain.ids import SessionId, UserId
from vakr.domain.sessions import Session

_COLUMNS = "id, token_hash, user_id, created_at, last_seen_at, expires_at, source"
_SELECT = f"SELECT {_COLUMNS} FROM auth_session"  # noqa: S608  # constant columns


def _decode(r: sqlite3.Row) -> Session:
    """Decode a session row.

    Args:
        r: The row.

    Returns:
        Session: The session.

    Raises:
        StorageError: If the row no longer forms a valid session.
    """
    try:
        return Session(
            id=SessionId(blob_to_uuid(r["id"])),
            user_id=UserId(blob_to_uuid(r["user_id"])),
            token_hash=r["token_hash"],
            created_at=decode_time(r["created_at"]),
            last_seen_at=decode_time(r["last_seen_at"]),
            expires_at=decode_time(r["expires_at"]),
            source=r["source"],
        )
    except ValueError:
        raise StorageError("a stored session is not valid", retryable=False) from None


class SqliteSessionRepository:
    """Server-side sessions in one unit of work."""

    def __init__(self, tx: SqliteTransaction) -> None:
        """Bind to a unit of work's transaction.

        Args:
            tx: The transaction.

        Returns:
            None

        Raises:
            None
        """
        self._tx = tx

    def add(self, session: Session) -> None:
        """Insert a new session.

        Args:
            session: The session.

        Returns:
            None

        Raises:
            DuplicateError: If the ID or digest is taken.
            NotFoundError: If the user does not exist (foreign key).
            StorageError: If the write fails.
        """
        self._tx.begin_write()
        if self._tx.query("SELECT 1 FROM auth_session WHERE id = ?", (session.id.bytes,)):
            raise DuplicateError("session", "id", session.id)
        if self._tx.query("SELECT 1 FROM auth_session WHERE token_hash = ?", (session.token_hash,)):
            raise DuplicateError("session", "token", "-")
        values = (
            session.id.bytes,
            session.token_hash,
            session.user_id.bytes,
            encode_time(session.created_at),
            encode_time(session.last_seen_at),
            encode_time(session.expires_at),
            session.source,
        )
        self._tx.execute(
            f"INSERT INTO auth_session ({_COLUMNS}) VALUES (?, ?, ?, ?, ?, ?, ?)",  # noqa: S608
            values,
            ErrorContext("session", session.id),
        )

    def get(self, session_id: SessionId) -> Session | None:
        """Return one session.

        Args:
            session_id: The UUID.

        Returns:
            Session | None: The session, or ``None``.

        Raises:
            StorageError: If the row cannot be read or decoded.
        """
        rows = self._tx.query(f"{_SELECT} WHERE id = ?", (session_id.bytes,))
        return _decode(rows[0]) if rows else None

    def get_by_token_hash(self, token_hash: bytes) -> Session | None:
        """Return the session with this token digest.

        Args:
            token_hash: SHA-256 of the token.

        Returns:
            Session | None: The session, or ``None``.

        Raises:
            StorageError: If the row cannot be read or decoded.
        """
        rows = self._tx.query(f"{_SELECT} WHERE token_hash = ?", (token_hash,))
        return _decode(rows[0]) if rows else None

    def list_for_user(self, user_id: UserId, *, limit: int) -> tuple[Session, ...]:
        """List one user's sessions, oldest first.

        Args:
            user_id: The account.
            limit: Most sessions to return.

        Returns:
            tuple[Session, ...]: The sessions.

        Raises:
            StorageError: If the rows cannot be read or decoded.
        """
        rows = self._tx.query(
            f"{_SELECT} WHERE user_id = ? ORDER BY created_at, id LIMIT ?",
            (user_id.bytes, max(limit, 0)),
        )
        return tuple(_decode(r) for r in rows)

    def touch(self, session_id: SessionId, at: datetime) -> None:
        """Record use of a session; a missing one is ignored.

        Args:
            session_id: The session.
            at: Time of use.

        Returns:
            None

        Raises:
            StorageError: If the write fails.
        """
        self._tx.begin_write()
        self._tx.execute(
            "UPDATE auth_session SET last_seen_at = ? WHERE id = ?",
            (encode_time(at), session_id.bytes),
        )

    def delete(self, session_id: SessionId) -> bool:
        """Delete one session.

        Args:
            session_id: The session.

        Returns:
            bool: Whether it existed.

        Raises:
            StorageError: If the write fails.
        """
        self._tx.begin_write()
        return bool(self._tx.execute("DELETE FROM auth_session WHERE id = ?", (session_id.bytes,)))

    def delete_for_user(self, user_id: UserId) -> int:
        """Delete every session of one user.

        Args:
            user_id: The account.

        Returns:
            int: Sessions deleted.

        Raises:
            StorageError: If the write fails.
        """
        self._tx.begin_write()
        return self._tx.execute("DELETE FROM auth_session WHERE user_id = ?", (user_id.bytes,))

    def purge_expired(self, *, now: datetime, idle_cutoff: datetime, limit: int) -> int:
        """Delete up to ``limit`` expired or idle sessions, earliest expiry first.

        Args:
            now: Current time.
            idle_cutoff: ``now - idle``.
            limit: Most rows to delete.

        Returns:
            int: Sessions deleted.

        Raises:
            StorageError: If the write fails.
        """
        self._tx.begin_write()
        return self._tx.execute(
            "DELETE FROM auth_session WHERE id IN (SELECT id FROM auth_session "
            "WHERE expires_at <= ? OR last_seen_at <= ? ORDER BY expires_at, id LIMIT ?)",
            (encode_time(now), encode_time(idle_cutoff), max(limit, 0)),
        )
