"""SQLite kiosk-token repository (Appendix E.12).

Kiosk tokens live in ``kiosk_token`` (migration ``0003``), unique by ID,
digest, and label. Revocation and expiry delete the row; purges are
bounded by a ``LIMIT`` subquery.

Dependencies:
    vakr.adapters.sqlite.encoding, errors, metadata.transaction.
    vakr.domain: The kiosk record, IDs, and errors.

Safety:
    SQL is fixed text with bound values. Digests are bound, never formatted
    into messages.

Example:
    >>> SqliteKioskTokenRepository(tx).count()  # doctest: +SKIP
    0
"""

import sqlite3
from datetime import datetime

from vakr.adapters.sqlite.encoding import blob_to_uuid, decode_time, encode_time
from vakr.adapters.sqlite.errors import ErrorContext
from vakr.adapters.sqlite.metadata.transaction import SqliteTransaction
from vakr.domain.errors import DuplicateError, StorageError
from vakr.domain.ids import KioskTokenId
from vakr.domain.sessions import KioskToken

_COLUMNS = "id, token_hash, label, created_by, created_at, expires_at, last_used_at"
_SELECT = f"SELECT {_COLUMNS} FROM kiosk_token"  # noqa: S608  # constant columns


def _decode(r: sqlite3.Row) -> KioskToken:
    """Decode a kiosk-token row.

    Args:
        r: The row.

    Returns:
        KioskToken: The record.

    Raises:
        StorageError: If the row no longer forms a valid record.
    """
    try:
        used = r["last_used_at"]
        return KioskToken(
            id=KioskTokenId(blob_to_uuid(r["id"])),
            label=r["label"],
            token_hash=r["token_hash"],
            created_by=r["created_by"],
            created_at=decode_time(r["created_at"]),
            expires_at=decode_time(r["expires_at"]),
            last_used_at=None if used is None else decode_time(used),
        )
    except ValueError:
        raise StorageError("a stored kiosk token is not valid", retryable=False) from None


class SqliteKioskTokenRepository:
    """Kiosk display tokens in one unit of work, by ``(label, id)``."""

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

    def add(self, token: KioskToken) -> None:
        """Insert a new kiosk token.

        Args:
            token: The record.

        Returns:
            None

        Raises:
            DuplicateError: If the ID, digest, or label is taken.
            StorageError: If the write fails.
        """
        self._tx.begin_write()
        checks = (
            ("id", token.id.bytes, token.id),
            ("token_hash", token.token_hash, "-"),
            ("label", token.label, token.label),
        )
        for column, value, shown in checks:
            # Column names come from the constant tuple above; values are bound.
            if self._tx.query(f"SELECT 1 FROM kiosk_token WHERE {column} = ?", (value,)):  # noqa: S608
                raise DuplicateError("kiosk token", column, shown)
        used = None if token.last_used_at is None else encode_time(token.last_used_at)
        values = (
            token.id.bytes,
            token.token_hash,
            token.label,
            token.created_by,
            encode_time(token.created_at),
            encode_time(token.expires_at),
            used,
        )
        self._tx.execute(
            f"INSERT INTO kiosk_token ({_COLUMNS}) VALUES (?, ?, ?, ?, ?, ?, ?)",  # noqa: S608
            values,
            ErrorContext("kiosk token", token.id),
        )

    def get(self, token_id: KioskTokenId) -> KioskToken | None:
        """Return one kiosk token.

        Args:
            token_id: The UUID.

        Returns:
            KioskToken | None: The record, or ``None``.

        Raises:
            StorageError: If the row cannot be read or decoded.
        """
        rows = self._tx.query(f"{_SELECT} WHERE id = ?", (token_id.bytes,))
        return _decode(rows[0]) if rows else None

    def get_by_token_hash(self, token_hash: bytes) -> KioskToken | None:
        """Return the kiosk token with this digest.

        Args:
            token_hash: SHA-256 of the token.

        Returns:
            KioskToken | None: The record, or ``None``.

        Raises:
            StorageError: If the row cannot be read or decoded.
        """
        rows = self._tx.query(f"{_SELECT} WHERE token_hash = ?", (token_hash,))
        return _decode(rows[0]) if rows else None

    def list_all(self, *, limit: int) -> tuple[KioskToken, ...]:
        """List kiosk tokens by ``(label, id)``.

        Args:
            limit: Most records to return.

        Returns:
            tuple[KioskToken, ...]: The records.

        Raises:
            StorageError: If the rows cannot be read or decoded.
        """
        rows = self._tx.query(f"{_SELECT} ORDER BY label, id LIMIT ?", (max(limit, 0),))
        return tuple(_decode(r) for r in rows)

    def touch(self, token_id: KioskTokenId, at: datetime) -> None:
        """Record use of a kiosk token; a missing one is ignored.

        Args:
            token_id: The token.
            at: Time of use.

        Returns:
            None

        Raises:
            StorageError: If the write fails.
        """
        self._tx.begin_write()
        self._tx.execute(
            "UPDATE kiosk_token SET last_used_at = ? WHERE id = ?",
            (encode_time(at), token_id.bytes),
        )

    def delete(self, token_id: KioskTokenId) -> bool:
        """Delete one kiosk token.

        Args:
            token_id: The token.

        Returns:
            bool: Whether it existed.

        Raises:
            StorageError: If the write fails.
        """
        self._tx.begin_write()
        return bool(self._tx.execute("DELETE FROM kiosk_token WHERE id = ?", (token_id.bytes,)))

    def purge_expired(self, *, now: datetime, limit: int) -> int:
        """Delete up to ``limit`` expired kiosk tokens, earliest first.

        Args:
            now: Current time.
            limit: Most rows to delete.

        Returns:
            int: Tokens deleted.

        Raises:
            StorageError: If the write fails.
        """
        self._tx.begin_write()
        return self._tx.execute(
            "DELETE FROM kiosk_token WHERE id IN (SELECT id FROM kiosk_token "
            "WHERE expires_at <= ? ORDER BY expires_at, id LIMIT ?)",
            (encode_time(now), max(limit, 0)),
        )

    def count(self) -> int:
        """Return how many kiosk tokens exist.

        Args:
            None

        Returns:
            int: Count.

        Raises:
            StorageError: If the table cannot be read.
        """
        return int(self._tx.query("SELECT count(*) FROM kiosk_token")[0][0])
