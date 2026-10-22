"""SQLite kiosk-token repository, and the same for replication tokens (Appendix E.12, E.13).

Kiosk tokens live in ``kiosk_token`` (migration ``0003``), unique by ID,
digest, and label. Revocation and expiry delete the row; purges are
bounded by a ``LIMIT`` subquery. Replication tokens (``replication_token``,
migration ``0006``) have the same shape, so one class serves both,
bound to its table by ``TOKEN_TABLES``.

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
from vakr.domain.ids import KioskTokenId, ReplicationTokenId
from vakr.domain.replication import ReplicationToken
from vakr.domain.sessions import KioskToken

_COLUMNS = "id, token_hash, label, created_by, created_at, expires_at, last_used_at"
TOKEN_TABLES = {
    "kiosk_token": ("kiosk token", KioskToken, KioskTokenId),
    "replication_token": (
        "replication token", ReplicationToken,
        lambda u: ReplicationTokenId(KioskTokenId(u)),
    ),
}  # fmt: skip
"""Table name to (noun, record type, ID type); the only tables this class may name."""


def _decode(r: sqlite3.Row, table: str = "kiosk_token") -> KioskToken:
    """Decode a token row.

    Args:
        r: The row.
        table: Which ``TOKEN_TABLES`` entry it comes from.

    Returns:
        KioskToken: The record.

    Raises:
        StorageError: If the row no longer forms a valid record.
    """
    noun, record, id_type = TOKEN_TABLES[table]
    try:
        used = r["last_used_at"]
        return record(
            id=id_type(blob_to_uuid(r["id"])),
            label=r["label"],
            token_hash=r["token_hash"],
            created_by=r["created_by"],
            created_at=decode_time(r["created_at"]),
            expires_at=decode_time(r["expires_at"]),
            last_used_at=None if used is None else decode_time(used),
        )
    except ValueError:
        raise StorageError(f"a stored {noun} is not valid", retryable=False) from None


class SqliteKioskTokenRepository:
    """Kiosk display tokens (or replication tokens) in one unit of work, by ``(label, id)``."""

    def __init__(self, tx: SqliteTransaction, table: str = "kiosk_token") -> None:
        """Bind to a unit of work's transaction and one ``TOKEN_TABLES`` table.

        Args:
            tx: The transaction.
            table: ``kiosk_token`` or ``replication_token``.

        Returns:
            None

        Raises:
            KeyError: For any other table.
        """
        self._tx = tx
        self._noun = TOKEN_TABLES[table][0]
        self._table = table
        self._select = f"SELECT {_COLUMNS} FROM {table}"  # noqa: S608  # allowlisted table

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
            if self._tx.query(f"SELECT 1 FROM {self._table} WHERE {column} = ?", (value,)):  # noqa: S608
                raise DuplicateError(self._noun, column, shown)
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
            f"INSERT INTO {self._table} ({_COLUMNS}) VALUES (?, ?, ?, ?, ?, ?, ?)",  # noqa: S608
            values,
            ErrorContext(self._noun, token.id),
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
        rows = self._tx.query(f"{self._select} WHERE id = ?", (token_id.bytes,))
        return _decode(rows[0], self._table) if rows else None

    def get_by_token_hash(self, token_hash: bytes) -> KioskToken | None:
        """Return the kiosk token with this digest.

        Args:
            token_hash: SHA-256 of the token.

        Returns:
            KioskToken | None: The record, or ``None``.

        Raises:
            StorageError: If the row cannot be read or decoded.
        """
        rows = self._tx.query(f"{self._select} WHERE token_hash = ?", (token_hash,))
        return _decode(rows[0], self._table) if rows else None

    def list_all(self, *, limit: int) -> tuple[KioskToken, ...]:
        """List kiosk tokens by ``(label, id)``.

        Args:
            limit: Most records to return.

        Returns:
            tuple[KioskToken, ...]: The records.

        Raises:
            StorageError: If the rows cannot be read or decoded.
        """
        rows = self._tx.query(f"{self._select} ORDER BY label, id LIMIT ?", (max(limit, 0),))
        return tuple(_decode(r, self._table) for r in rows)

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
            f"UPDATE {self._table} SET last_used_at = ? WHERE id = ?",  # noqa: S608
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
        sql = f"DELETE FROM {self._table} WHERE id = ?"  # noqa: S608  # allowlisted table
        return bool(self._tx.execute(sql, (token_id.bytes,)))

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
            f"DELETE FROM {self._table} WHERE id IN (SELECT id FROM {self._table} "  # noqa: S608
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
        return int(self._tx.query(f"SELECT count(*) FROM {self._table}")[0][0])  # noqa: S608
