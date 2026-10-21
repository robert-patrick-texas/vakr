"""Reading local accounts and the guards user changes share (SEC-005, SEC-006).

``AccountQueries`` lists and finds accounts and applies the password
policy; ``UserAdmin`` (``vakr.auth.accounts``) extends it with the audited
changes. ``require_user`` and ``keep_an_admin`` are the two checks every
change makes: the account exists, and at least one enabled admin remains.

Dependencies:
    vakr.auth.passwords, settings: Policy and hashing.
    vakr.ports: The metadata unit of work and the clock.

Safety:
    Returned ``User`` records keep the hash out of ``repr``; callers that
    render accounts must not print ``password_hash``.

Example:
    >>> MAX_LISTED_USERS
    10000
"""

from vakr.auth.passwords import PasswordPolicy, Passwords
from vakr.auth.settings import AuthSettings
from vakr.credentials.secret import Secret
from vakr.domain.errors import NotFoundError, ValidationError
from vakr.domain.paging import MAX_PAGE_LIMIT, PageRequest
from vakr.domain.users import MAX_USERNAME_LENGTH, Role, User
from vakr.ports.clock import Clock
from vakr.ports.metadata.unit_of_work import MetadataUnitOfWork, MetadataUnitOfWorkFactory
from vakr.ports.metadata.users import UserFilter

MAX_LISTED_USERS = 10_000  # far beyond a monitoring team; bounds one list call
_ENABLED_ADMINS = UserFilter(role=Role.ADMIN, enabled=True)


def require_user(uow: MetadataUnitOfWork, username: str) -> User:
    """Return an existing account.

    Args:
        uow: The unit of work.
        username: Username as typed.

    Returns:
        User: The account.

    Raises:
        NotFoundError: If there is no such account.
    """
    user = uow.users.get_by_username(username)
    if user is None:
        raise NotFoundError("user", username.strip().lower()[:MAX_USERNAME_LENGTH])
    return user


def keep_an_admin(uow: MetadataUnitOfWork) -> None:
    """Refuse a change that leaves no enabled admin; call it after the change.

    Args:
        uow: The unit of work holding the change; it has written, so the
            count reads the newest data under the write lock.

    Returns:
        None

    Raises:
        ValidationError: If no enabled admin would remain.
    """
    if not uow.users.list_page(PageRequest(limit=1), _ENABLED_ADMINS).total:
        raise ValidationError("at least one enabled admin must remain", field="role")


class AccountQueries:
    """Read-only account operations and the password policy."""

    def __init__(
        self,
        uow_factory: MetadataUnitOfWorkFactory,
        clock: Clock,
        passwords: Passwords,
        settings: AuthSettings | None = None,
    ) -> None:
        """Create the service.

        Args:
            uow_factory: Opens metadata units of work.
            clock: Time source.
            passwords: Hashing.
            settings: Password policy and bounds; ``None`` for the E.12 defaults.

        Returns:
            None

        Raises:
            None
        """
        self._uow = uow_factory
        self._clock = clock
        self._passwords = passwords
        self._policy = PasswordPolicy(min_length=(settings or AuthSettings()).password_min_length)

    def check_password(self, username: str, password: Secret) -> None:
        """Apply the password policy (E.12).

        Args:
            username: Canonical username.
            password: The candidate.

        Returns:
            None

        Raises:
            ValidationError: Listing every broken rule, never the password.
        """
        if problems := self._policy.problems(password, username):
            raise ValidationError("password " + "; ".join(problems), field="password")

    def get(self, username: str) -> User | None:
        """Return one account by username.

        Args:
            username: Username as typed.

        Returns:
            User | None: The account, or ``None``.

        Raises:
            None
        """
        with self._uow() as uow:
            return uow.users.get_by_username(username)

    def users(self, *, include_disabled: bool = True) -> tuple[User, ...]:
        """List accounts by username.

        Args:
            include_disabled: Whether disabled accounts are listed.

        Returns:
            tuple[User, ...]: The accounts, at most ``MAX_LISTED_USERS``.

        Raises:
            None
        """
        where = None if include_disabled else UserFilter(enabled=True)
        found: list[User] = []
        cursor: str | None = None
        with self._uow() as uow:
            while len(found) < MAX_LISTED_USERS:
                page = uow.users.list_page(PageRequest(MAX_PAGE_LIMIT, cursor), where)
                found.extend(page.items)
                if (cursor := page.next_cursor) is None:
                    break
        return tuple(found[:MAX_LISTED_USERS])

    def admin_exists(self) -> bool:
        """Tell whether any enabled admin account exists.

        Args:
            None

        Returns:
            bool: Whether one does.

        Raises:
            None
        """
        with self._uow() as uow:
            return bool(uow.users.list_page(PageRequest(limit=1), _ENABLED_ADMINS).total)
