"""Local user accounts and login-failure state (SEC-005, SEC-006, SEC-009, SEC-012, 7.1).

vakr ships its own accounts so a monitoring host works without a directory
service: an *admin* manages users, inventory, and policy; an *operator*
reads monitoring state only (3.1). ``User`` holds the Argon2id hash of the
password, never the password. ``LoginFailure`` counts consecutive failed
logins for one account and one client address, so the login service can
delay the next attempt (Appendix E.12).

Usernames are canonical: trimmed and lower case, 1 to 64 characters from
``a-z 0-9 . _ @ -``, starting with a letter or digit. ``Alice`` and
``alice `` are therefore one account in every backend, and the plain
ASCII alphabet means lower-casing means the same thing in SQLite,
PostgreSQL, and Python.

Dependencies:
    dataclasses, enum, re: Frozen records, the role enum, and the name rule.
    vakr.domain.checks, ids, timestamps: Shared invariant checks.

Safety:
    ``repr`` never shows ``password_hash``: a stolen hash still lets an
    attacker guess weak passwords offline. ``canonical_username`` never
    echoes rejected text, which may be a password typed into the wrong field.

Example:
    >>> canonical_username("  Alice ")
    'alice'
    >>> Role("operator") is Role.OPERATOR
    True
"""

import re
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum

from vakr.domain.checks import require_bool, require_enum, require_int, require_name, require_uuid
from vakr.domain.errors import ValidationError
from vakr.domain.ids import UserId
from vakr.domain.timestamps import require_utc

MAX_USERNAME_LENGTH = 64
MAX_DISPLAY_NAME_LENGTH = 128
MAX_SOURCE_LENGTH = 64  # an IPv6 address with a zone index fits; longer text is not an address
MAX_HASH_LENGTH = 512  # an Argon2 PHC string is about 100 characters; this bounds bad input

_USERNAME = re.compile(r"[a-z0-9][a-z0-9._@-]{0,63}")
_ACCOUNT_KEY = re.compile(r"[0-9a-f]{64}")


class Role(StrEnum):
    """What a signed-in principal may do (3.1, SEC-005, SEC-006)."""

    ADMIN = "admin"
    OPERATOR = "operator"


def canonical_username(text: object) -> str:
    """Return the canonical form of a username: trimmed and lower case.

    Args:
        text: Username as typed.

    Returns:
        str: The canonical username.

    Raises:
        ValidationError: If the result is empty, too long, or uses other
            characters; the message never repeats the text.
    """
    if not isinstance(text, str):
        raise ValidationError("username must be a string", field="username")
    name = text.strip().lower()
    if not _USERNAME.fullmatch(name):
        raise ValidationError(
            f"username must be 1-{MAX_USERNAME_LENGTH} characters: letters, digits, '.', '_', "
            "'@', or '-', starting with a letter or digit",
            field="username",
        )
    return name


def require_source(value: object, field_name: str = "source") -> None:
    """Require a client address label: a short single token such as ``192.0.2.10``.

    Args:
        value: Candidate label.
        field_name: Field name for the error message.

    Returns:
        None

    Raises:
        ValidationError: If it is not a printable string of 1-64 characters
            without whitespace.
    """
    require_name(value, field_name)
    if not isinstance(value, str) or len(value) > MAX_SOURCE_LENGTH or not value.isprintable():
        raise ValidationError(
            f"{field_name} must be at most {MAX_SOURCE_LENGTH} characters", field=field_name
        )
    if any(c.isspace() for c in value):
        raise ValidationError(f"{field_name} must not contain whitespace", field=field_name)


UNKNOWN_SOURCE = "unknown"


def clean_source(source: object) -> str:
    """Return a client address label fit for storage, or ``unknown``.

    Args:
        source: The client address the HTTP layer determined, or anything else.

    Returns:
        str: The label, or ``"unknown"`` for missing or malformed input.

    Raises:
        None
    """
    try:
        require_source(source)
    except ValidationError:
        return UNKNOWN_SOURCE
    return str(source)


@dataclass(frozen=True, slots=True)
class User:
    """A local account (7.1 LocalUser). Users are disabled, never deleted (META-009).

    Attributes:
        id: Immutable UUID.
        username: Canonical username; the natural key, fixed after creation.
        display_name: Name shown in the UI; may be empty.
        role: ``ADMIN`` or ``OPERATOR``.
        password_hash: Argon2id PHC string with its parameters (SEC-009); not in ``repr``.
        created_at: When the account was created (UTC).
        updated_at: When an administrator last changed it (UTC).
        password_changed_at: When the password was last set (UTC).
        enabled: Whether the account may sign in.
        last_login_at: Last successful login, changed only by ``record_login``.
        version: Optimistic-concurrency version (DB-003).
    """

    id: UserId
    username: str
    display_name: str
    role: Role
    password_hash: str = field(repr=False)
    created_at: datetime
    updated_at: datetime
    password_changed_at: datetime
    enabled: bool = True
    last_login_at: datetime | None = None
    version: int = 1

    def __post_init__(self) -> None:
        """Validate the account.

        Args:
            None

        Returns:
            None

        Raises:
            ValidationError: If a field breaks its rule; messages never quote
                the hash.
        """
        require_uuid(self.id, "id")
        if canonical_username(self.username) != self.username:
            raise ValidationError(
                "username must be canonical (trimmed, lower case)", field="username"
            )
        if not isinstance(self.display_name, str) or len(self.display_name) > (
            MAX_DISPLAY_NAME_LENGTH
        ):
            raise ValidationError(
                f"display_name must be at most {MAX_DISPLAY_NAME_LENGTH} characters",
                field="display_name",
            )
        if self.display_name:
            require_name(self.display_name, "display_name")
        require_enum(self.role, Role, "role")
        _require_hash(self.password_hash)
        for name in ("created_at", "updated_at", "password_changed_at"):
            require_utc(getattr(self, name), name)
        if self.last_login_at is not None:
            require_utc(self.last_login_at, "last_login_at")
        require_bool(self.enabled, "enabled")
        require_int(self.version, "version", minimum=1)

    @property
    def label(self) -> str:
        """Name to show: the display name, else the username.

        Args:
            None

        Returns:
            str: The label.

        Raises:
            None
        """
        return self.display_name or self.username


def _require_hash(value: object) -> None:
    """Require a stored password hash in PHC string form, without quoting it.

    Args:
        value: Candidate hash.

    Returns:
        None

    Raises:
        ValidationError: If it is not a ``$``-prefixed printable string of
            bounded length.
    """
    if (
        not isinstance(value, str)
        or not value.startswith("$")
        or len(value) > MAX_HASH_LENGTH
        or not value.isprintable()
        or any(c.isspace() for c in value)
    ):
        raise ValidationError("password_hash must be a PHC hash string", field="password_hash")


@dataclass(frozen=True, slots=True)
class LoginFailure:
    """Consecutive failed logins for one account and one client address (SEC-012, E.12).

    Attributes:
        account_key: Keyed SHA-256 of the canonical attempted username, as
            64 hex digits; unknown usernames are tracked the same way, so the
            delay never reveals whether an account exists (AUTH-004).
        source: Client address label.
        failures: Consecutive failures, at least 1.
        first_failed_at: First failure of this run (UTC).
        last_failed_at: Latest failure (UTC).
    """

    account_key: str
    source: str
    failures: int
    first_failed_at: datetime
    last_failed_at: datetime

    def __post_init__(self) -> None:
        """Validate the record.

        Args:
            None

        Returns:
            None

        Raises:
            ValidationError: If a field breaks its rule or times are out of order.
        """
        if not isinstance(self.account_key, str) or not _ACCOUNT_KEY.fullmatch(self.account_key):
            raise ValidationError(
                "account_key must be 64 lower-case hex digits", field="account_key"
            )
        require_source(self.source)
        require_int(self.failures, "failures", minimum=1)
        require_utc(self.first_failed_at, "first_failed_at")
        require_utc(self.last_failed_at, "last_failed_at")
        if self.last_failed_at < self.first_failed_at:
            raise ValidationError(
                "last_failed_at must not precede first_failed_at", field="last_failed_at"
            )
