"""In-memory user repository (SEC-005, SEC-009).

The reference implementation of ``UserRepository``. Users are staged in
the unit of work's transaction like every other versioned record, unique
by ``username``, and never deleted.

Dependencies:
    vakr.adapters.memory.metadata_store, paging: Transaction and pages.
    vakr.domain: Records and errors.

Safety:
    Nothing here logs; errors name the record kind and ID only.

Example:
    >>> from vakr.adapters.memory.metadata_store import MemoryMetadataStore, Transaction
    >>> MemoryUserRepository(Transaction(MemoryMetadataStore())).get_by_username("x") is None
    True
"""

from dataclasses import replace
from datetime import datetime

from vakr.adapters.memory.metadata_store import Transaction
from vakr.adapters.memory.paging import paginate
from vakr.domain.errors import ConflictError, DuplicateError, NotFoundError, ValidationError
from vakr.domain.ids import UserId
from vakr.domain.paging import Page, PageRequest
from vakr.domain.users import User, canonical_username
from vakr.ports.metadata.users import UserFilter

_FIXED = ("id", "username", "created_at")


class MemoryUserRepository:
    """Local accounts, by ``(username, id)``."""

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

    def add(self, user: User) -> User:
        """Stage a new account.

        Args:
            user: The account.

        Returns:
            User: The account.

        Raises:
            ValidationError, DuplicateError: As the port documents.
        """
        if user.version != 1:
            raise ValidationError("a new user must have version 1", field="version")
        if self._tx.get("user", user.id) is not None:
            raise DuplicateError("user", "id", user.id)
        if self.get_by_username(user.username) is not None:
            raise DuplicateError("user", "username", user.username)
        self._tx.put("user", user, None)
        return user

    def get(self, user_id: UserId) -> User | None:
        """Return one account.

        Args:
            user_id: The UUID.

        Returns:
            User | None: The account, or ``None``.

        Raises:
            None
        """
        return self._tx.get("user", user_id)

    def get_by_username(self, username: str) -> User | None:
        """Return the account with this canonical username.

        Args:
            username: Username as typed.

        Returns:
            User | None: The account, or ``None``.

        Raises:
            None
        """
        try:
            name = canonical_username(username)
        except ValidationError:
            return None
        return next((u for u in self._tx.records("user") if u.username == name), None)

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
        users = [
            u
            for u in self._tx.records("user")
            if (w.role is None or u.role is w.role)
            and (w.enabled is None or u.enabled is w.enabled)
        ]
        return paginate(users, lambda u: (u.username, str(u.id)), page)

    def update(self, user: User) -> User:
        """Stage a changed account, checking the version.

        Args:
            user: The new content.

        Returns:
            User: The stored account.

        Raises:
            NotFoundError, ConflictError, ValidationError: As the port documents.
        """
        stored: User | None = self._tx.get("user", user.id)
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
        self._tx.put("user", new, stored.version)
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
        """
        stored: User | None = self._tx.get("user", user_id)
        if stored is None:
            raise NotFoundError("user", user_id)
        rehash = new_hash is not None and stored.password_hash == verified_hash
        changed = replace(stored, last_login_at=at)
        if rehash and new_hash is not None:
            changed = replace(changed, password_hash=new_hash)
        self._tx.put("user", changed, stored.version)
        return rehash
