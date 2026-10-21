"""SQLite user repository (SEC-005, SEC-009).

Accounts live in ``local_user`` (migration ``0003``). Every write first
calls ``begin_write``, so the checks that follow read the newest committed
data under SQLite's write lock: the version check on ``update`` and the
compare-and-set of the hash in ``record_login`` are atomic.

Dependencies:
    vakr.adapters.sqlite.encoding, errors, metadata.paging, transaction.
    vakr.domain: User and failure records, IDs, and errors.

Safety:
    SQL is fixed text with bound values. Password hashes are bound, never
    formatted into messages; a row that no longer decodes is reported
    without its contents.

Example:
    >>> SqliteUserRepository(tx).get_by_username("alice")  # doctest: +SKIP
"""

import sqlite3
from dataclasses import replace
from datetime import datetime

from vakr.adapters.sqlite.encoding import blob_to_uuid, decode_time, encode_time
from vakr.adapters.sqlite.errors import ErrorContext
from vakr.adapters.sqlite.metadata.paging import Clause, KeyOrder, Listing, Part, every, fetch_page
from vakr.adapters.sqlite.metadata.transaction import SqliteTransaction
from vakr.domain.errors import (
    ConflictError,
    DuplicateError,
    NotFoundError,
    StorageError,
    ValidationError,
)
from vakr.domain.ids import UserId
from vakr.domain.paging import Page, PageRequest
from vakr.domain.users import Role, User, canonical_username
from vakr.ports.metadata.users import UserFilter

_COLUMNS = (
    "id",
    "username",
    "display_name",
    "role",
    "enabled",
    "password_hash",
    "created_at",
    "updated_at",
    "password_changed_at",
    "last_login_at",
    "version",
)
_LISTING = Listing(
    "local_user", ", ".join(_COLUMNS), KeyOrder(("username", "id"), (Part.TEXT, Part.UUID))
)
_SELECT = f"SELECT {_LISTING.select} FROM local_user"  # noqa: S608  # constant columns
_INSERT = (
    f"INSERT INTO local_user ({_LISTING.select}) "  # noqa: S608  # constant columns
    f"VALUES ({', '.join('?' for _ in _COLUMNS)})"
)
_UPDATE = (
    "UPDATE local_user SET display_name = ?, role = ?, enabled = ?, password_hash = ?, "
    "updated_at = ?, password_changed_at = ?, version = ? WHERE id = ?"
)
_FIXED = ("id", "username", "created_at")


def _decode(r: sqlite3.Row) -> User:
    """Decode an account row.

    Args:
        r: The row.

    Returns:
        User: The account.

    Raises:
        StorageError: If the row no longer forms a valid account.
    """
    try:
        last = r["last_login_at"]
        return User(
            id=UserId(blob_to_uuid(r["id"])),
            username=r["username"],
            display_name=r["display_name"],
            role=Role(r["role"]),
            password_hash=r["password_hash"],
            created_at=decode_time(r["created_at"]),
            updated_at=decode_time(r["updated_at"]),
            password_changed_at=decode_time(r["password_changed_at"]),
            enabled=bool(r["enabled"]),
            last_login_at=None if last is None else decode_time(last),
            version=r["version"],
        )
    except ValueError:
        raise StorageError("a stored user account is not valid", retryable=False) from None


def _values(u: User) -> tuple[object, ...]:
    """Encode an account for ``_INSERT``.

    Args:
        u: The account.

    Returns:
        tuple[object, ...]: Bound values in ``_COLUMNS`` order.

    Raises:
        None
    """
    last = None if u.last_login_at is None else encode_time(u.last_login_at)
    return (
        u.id.bytes,
        u.username,
        u.display_name,
        u.role.value,
        int(u.enabled),
        u.password_hash,
        encode_time(u.created_at),
        encode_time(u.updated_at),
        encode_time(u.password_changed_at),
        last,
        u.version,
    )


class SqliteUserRepository:
    """Local accounts in one unit of work."""

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

    def add(self, user: User) -> User:
        """Insert a new account.

        Args:
            user: The account, ``version == 1``.

        Returns:
            User: The account.

        Raises:
            ValidationError, DuplicateError: As the port documents.
            StorageError: If the write fails.
        """
        if user.version != 1:
            raise ValidationError("a new user must have version 1", field="version")
        self._tx.begin_write()
        if self._tx.query("SELECT 1 FROM local_user WHERE id = ?", (user.id.bytes,)):
            raise DuplicateError("user", "id", user.id)
        if self._tx.query("SELECT 1 FROM local_user WHERE username = ?", (user.username,)):
            raise DuplicateError("user", "username", user.username)
        self._tx.execute(_INSERT, _values(user), ErrorContext("user", user.id))
        return user

    def get(self, user_id: UserId) -> User | None:
        """Return one account.

        Args:
            user_id: The UUID.

        Returns:
            User | None: The account, or ``None``.

        Raises:
            StorageError: If the row cannot be read or decoded.
        """
        rows = self._tx.query(f"{_SELECT} WHERE id = ?", (user_id.bytes,))
        return _decode(rows[0]) if rows else None

    def get_by_username(self, username: str) -> User | None:
        """Return the account with this canonical username.

        Args:
            username: Username as typed.

        Returns:
            User | None: The account, or ``None``.

        Raises:
            StorageError: If the row cannot be read or decoded.
        """
        try:
            name = canonical_username(username)
        except ValidationError:
            return None
        rows = self._tx.query(f"{_SELECT} WHERE username = ?", (name,))
        return _decode(rows[0]) if rows else None

    def list_page(self, page: PageRequest, where: UserFilter | None = None) -> Page[User]:
        """List accounts by ``(username, id)``.

        Args:
            page: Limit and cursor.
            where: Filter, or ``None``.

        Returns:
            Page[User]: The page, with ``total``.

        Raises:
            ValidationError: If the cursor is invalid.
        """
        w = where or UserFilter()
        parts: list[Clause] = []
        if w.role is not None:
            parts.append(Clause("role = ?", (w.role.value,)))
        if w.enabled is not None:
            parts.append(Clause("enabled = ?", (int(w.enabled),)))
        rows, cursor, total = fetch_page(self._tx, _LISTING, every(parts), page)
        return Page(tuple(_decode(r) for r in rows), cursor, total)

    def update(self, user: User) -> User:
        """Replace an account's administrator-managed fields.

        Args:
            user: New content based on ``user.version``.

        Returns:
            User: The stored account.

        Raises:
            NotFoundError, ConflictError, ValidationError: As the port documents.
            StorageError: If the write fails.
        """
        self._tx.begin_write()
        stored = self.get(user.id)
        if stored is None:
            raise NotFoundError("user", user.id)
        if stored.version != user.version:
            raise ConflictError(
                "user", user.id, expected_version=user.version, actual_version=stored.version
            )
        for name in _FIXED:
            if getattr(stored, name) != getattr(user, name):
                raise ValidationError(f"{name} cannot change", field=name)
        new = replace(user, last_login_at=stored.last_login_at, version=stored.version + 1)
        values = (
            new.display_name,
            new.role.value,
            int(new.enabled),
            new.password_hash,
            encode_time(new.updated_at),
            encode_time(new.password_changed_at),
            new.version,
            new.id.bytes,
        )
        self._tx.execute(_UPDATE, values, ErrorContext("user", user.id, user.version))
        return new

    def record_login(
        self, user_id: UserId, *, at: datetime, verified_hash: str, new_hash: str | None = None
    ) -> bool:
        """Record a login and maybe a rehash, without changing ``version``.

        Args:
            user_id: The account.
            at: Login time.
            verified_hash: Hash the password matched.
            new_hash: Replacement hash, or ``None``.

        Returns:
            bool: Whether the hash was replaced.

        Raises:
            NotFoundError: If the account does not exist.
            StorageError: If the write fails.
        """
        self._tx.begin_write()
        context = ErrorContext("user", user_id)
        changed = self._tx.execute(
            "UPDATE local_user SET last_login_at = ? WHERE id = ?",
            (encode_time(at), user_id.bytes),
            context,
        )
        if not changed:
            raise NotFoundError("user", user_id)
        if new_hash is None:
            return False
        # Compare-and-set: a password changed after verification must survive the rehash.
        return bool(
            self._tx.execute(
                "UPDATE local_user SET password_hash = ? WHERE id = ? AND password_hash = ?",
                (new_hash, user_id.bytes, verified_hash),
                context,
            )
        )
