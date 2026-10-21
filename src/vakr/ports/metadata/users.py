"""Repository protocols for local users and login-failure state (SEC-005, SEC-009, SEC-012).

Every backend must reproduce, and ``tests/contract/contract_auth_users.py``
checks:

- usernames are unique after canonicalisation (``canonical_username``);
  ``get_by_username`` canonicalises its argument, and text that cannot be a
  username finds nothing instead of raising;
- ``add`` requires ``version == 1`` and raises ``DuplicateError`` for a
  reused ID or username;
- ``update`` raises, in this order, ``NotFoundError``, ``ConflictError``
  (stale ``version``), and ``ValidationError`` (``id``, ``username``, or
  ``created_at`` changed); it stores ``version + 1`` and keeps the stored
  ``last_login_at``, which only ``record_login`` changes;
- ``record_login`` never changes ``version``, and replaces the password
  hash only while the stored hash is still the one that was verified, so a
  rehash can never undo a password change made meanwhile (SEC-009);
- users list by ``(username, id)`` with ``total``; nothing is deleted.

Login-failure rows are throttle state, not history: they are replaced,
cleared, and purged freely.

Dependencies:
    vakr.domain: User and failure records, IDs, paging types.

Safety:
    Implementations never log or echo password hashes or usernames from
    failed lookups.

Example:
    >>> UserFilter().role is None
    True
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from vakr.domain.ids import UserId
from vakr.domain.paging import Page, PageRequest
from vakr.domain.users import LoginFailure, Role, User


@dataclass(frozen=True, slots=True)
class UserFilter:
    """Which users to list.

    Attributes:
        role: Only users with this role; ``None`` for every role.
        enabled: Only enabled (``True``) or disabled (``False``) users; ``None`` for both.
    """

    role: Role | None = None
    enabled: bool | None = None


class UserRepository(Protocol):
    """Local accounts, listed by ``(username, id)``."""

    def add(self, user: User) -> User:
        """Store a new account.

        Args:
            user: The account, with ``version == 1``.

        Returns:
            User: The stored account.

        Raises:
            DuplicateError: If the ID or username is already used.
            ValidationError: If ``version`` is not 1.
        """
        ...

    def get(self, user_id: UserId) -> User | None:
        """Return one account, enabled or not.

        Args:
            user_id: The account's UUID.

        Returns:
            User | None: The account, or ``None``.

        Raises:
            None
        """
        ...

    def get_by_username(self, username: str) -> User | None:
        """Return the account with this username, compared canonically.

        Args:
            username: Username as typed; trimmed and lower-cased first.

        Returns:
            User | None: The account, or ``None`` (also for text that is not a username).

        Raises:
            None
        """
        ...

    def list_page(self, page: PageRequest, where: UserFilter | None = None) -> Page[User]:
        """List accounts by ``(username, id)``.

        Args:
            page: Limit and cursor.
            where: Filter, or ``None`` for every account.

        Returns:
            Page[User]: The accounts and the total match count.

        Raises:
            ValidationError: If ``page.cursor`` is invalid.
        """
        ...

    def update(self, user: User) -> User:
        """Replace an account's administrator-managed fields, checking ``version``.

        Args:
            user: New content; ``version`` is the version it was based on.

        Returns:
            User: The stored account, ``version`` incremented, ``last_login_at`` kept.

        Raises:
            NotFoundError: If the account does not exist.
            ConflictError: If it changed since ``user.version``.
            ValidationError: If ``id``, ``username``, or ``created_at`` changed.
        """
        ...

    def record_login(
        self, user_id: UserId, *, at: datetime, verified_hash: str, new_hash: str | None = None
    ) -> bool:
        """Record a successful login and, optionally, a transparent rehash (SEC-009).

        Args:
            user_id: The account.
            at: Login time (UTC); stored as ``last_login_at``.
            verified_hash: The hash the password was verified against.
            new_hash: Replacement hash with current parameters, or ``None``.

        Returns:
            bool: Whether ``new_hash`` was stored; ``False`` when none was
            given or the stored hash changed after verification.

        Raises:
            NotFoundError: If the account does not exist.
        """
        ...


class LoginFailureRepository(Protocol):
    """Consecutive login failures per ``(account_key, source)`` (E.12)."""

    def get(self, account_key: str, source: str) -> LoginFailure | None:
        """Return the failure run for one account and address.

        Args:
            account_key: Keyed digest of the attempted username.
            source: Client address label.

        Returns:
            LoginFailure | None: The run, or ``None`` when there is none.

        Raises:
            None
        """
        ...

    def record_failure(
        self, account_key: str, source: str, *, at: datetime, reset_before: datetime
    ) -> LoginFailure:
        """Count one more failure atomically, restarting a run that went quiet.

        Args:
            account_key: Keyed digest of the attempted username.
            source: Client address label.
            at: Failure time (UTC).
            reset_before: A run whose last failure is at or before this restarts at 1.

        Returns:
            LoginFailure: The run after this failure.

        Raises:
            ValidationError: If ``account_key`` or ``source`` is malformed.
        """
        ...

    def clear(self, account_key: str, source: str) -> None:
        """Forget the run after a successful login.

        Args:
            account_key: Keyed digest of the username.
            source: Client address label.

        Returns:
            None

        Raises:
            None
        """
        ...

    def purge(self, before: datetime, *, limit: int) -> int:
        """Delete runs whose last failure is earlier than ``before``, oldest first.

        Args:
            before: Cut-off time (UTC).
            limit: Most rows to delete in this call.

        Returns:
            int: Rows deleted.

        Raises:
            None
        """
        ...

    def count(self) -> int:
        """Return how many runs are tracked.

        Args:
            None

        Returns:
            int: Row count.

        Raises:
            None
        """
        ...
