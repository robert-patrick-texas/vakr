"""Server-side login sessions and kiosk tokens (SEC-010, 7.1 Session, Appendix E.12).

A browser that signs in receives an opaque random token in a cookie; the
server keeps only the token's SHA-256 digest, so a copy of the database
cannot be replayed as a login. A ``Session`` ends at whichever comes first:
``idle`` time after ``last_seen_at`` or the absolute ``expires_at``. A
``KioskToken`` is the same idea for an unattended wall display: operator
role only, no idle expiry, and a long absolute lifetime (30 days by
default), created and revoked by an administrator.

Expiry is half-open: a session last seen at 10:00 with a 30-minute idle
limit is valid at 10:29:59.999999 and expired at 10:30:00.

Dependencies:
    dataclasses, datetime: Frozen records and expiry arithmetic.
    vakr.domain.checks, ids, timestamps, users: Shared invariant checks.

Safety:
    ``repr`` never shows ``token_hash``. The token itself is never stored
    in these records at all.

Example:
    >>> from datetime import UTC, datetime, timedelta
    >>> from vakr.domain.ids import SessionId, UserId, new_id
    >>> t0 = datetime(2026, 10, 20, 10, 0, tzinfo=UTC)
    >>> s = Session(new_id(SessionId), new_id(UserId), bytes(32), t0, t0,
    ...             t0 + timedelta(hours=12), "192.0.2.10")
    >>> s.is_live(t0 + timedelta(minutes=29), idle=timedelta(minutes=30))
    True
"""

from dataclasses import dataclass, field
from datetime import datetime, timedelta

from vakr.domain.checks import require_name, require_uuid
from vakr.domain.errors import ValidationError
from vakr.domain.ids import KioskTokenId, SessionId, UserId
from vakr.domain.timestamps import require_utc
from vakr.domain.users import Role, require_source

TOKEN_HASH_BYTES = 32
"""Length of a stored SHA-256 token digest."""

MAX_LABEL_LENGTH = 64


def _require_digest(value: object) -> None:
    """Require a 32-byte token digest without quoting it.

    Args:
        value: Candidate digest.

    Returns:
        None

    Raises:
        ValidationError: If it is not 32 bytes.
    """
    if not isinstance(value, bytes) or len(value) != TOKEN_HASH_BYTES:
        raise ValidationError("token_hash must be a 32-byte SHA-256 digest", field="token_hash")


@dataclass(frozen=True, slots=True)
class Session:
    """One signed-in browser or API client (SEC-010).

    Attributes:
        id: UUID; also what CSRF tokens are bound to (SEC-011).
        user_id: The signed-in account.
        token_hash: SHA-256 of the opaque token; not in ``repr``.
        created_at: Login time (UTC).
        last_seen_at: Last recorded use (UTC), written at most once per touch interval.
        expires_at: Absolute expiry (UTC), fixed at login.
        source: Client address at login.
    """

    id: SessionId
    user_id: UserId
    token_hash: bytes = field(repr=False)
    created_at: datetime
    last_seen_at: datetime
    expires_at: datetime
    source: str

    def __post_init__(self) -> None:
        """Validate the session.

        Args:
            None

        Returns:
            None

        Raises:
            ValidationError: If a field breaks its rule or times are out of order.
        """
        require_uuid(self.id, "id")
        require_uuid(self.user_id, "user_id")
        _require_digest(self.token_hash)
        for name in ("created_at", "last_seen_at", "expires_at"):
            require_utc(getattr(self, name), name)
        if not self.created_at <= self.last_seen_at or not self.created_at < self.expires_at:
            raise ValidationError(
                "session times must satisfy created <= last seen, created < expires",
                field="expires_at",
            )
        require_source(self.source)

    def is_live(self, now: datetime, *, idle: timedelta) -> bool:
        """Tell whether the session is still valid at ``now``.

        Args:
            now: Current time (UTC).
            idle: Idle limit.

        Returns:
            bool: ``False`` from ``last_seen_at + idle`` or ``expires_at``,
            whichever is earlier, onwards.

        Raises:
            None
        """
        return now < self.expires_at and now < self.last_seen_at + idle


@dataclass(frozen=True, slots=True)
class KioskToken:
    """A read-only token for an unattended display (E.12): operator role, no idle expiry.

    Attributes:
        id: UUID.
        label: Unique name an administrator gave it, such as ``noc-wall``.
        token_hash: SHA-256 of the opaque token; not in ``repr``.
        created_by: Administrator who created it.
        created_at: Creation time (UTC).
        expires_at: Absolute expiry (UTC).
        last_used_at: Last recorded use (UTC), if any.
    """

    id: KioskTokenId
    label: str
    token_hash: bytes = field(repr=False)
    created_by: str
    created_at: datetime
    expires_at: datetime
    last_used_at: datetime | None = None

    def __post_init__(self) -> None:
        """Validate the token record.

        Args:
            None

        Returns:
            None

        Raises:
            ValidationError: If a field breaks its rule or times are out of order.
        """
        require_uuid(self.id, "id")
        require_name(self.label, "label")
        if len(self.label) > MAX_LABEL_LENGTH:
            raise ValidationError(
                f"label must be at most {MAX_LABEL_LENGTH} characters", field="label"
            )
        _require_digest(self.token_hash)
        require_name(self.created_by, "created_by")
        require_utc(self.created_at, "created_at")
        require_utc(self.expires_at, "expires_at")
        if not self.created_at < self.expires_at:
            raise ValidationError("expires_at must follow created_at", field="expires_at")
        if self.last_used_at is not None:
            require_utc(self.last_used_at, "last_used_at")

    @property
    def role(self) -> Role:
        """The role a kiosk token grants: always operator (E.12).

        Args:
            None

        Returns:
            Role: ``Role.OPERATOR``.

        Raises:
            None
        """
        return Role.OPERATOR

    def is_live(self, now: datetime) -> bool:
        """Tell whether the token is valid at ``now``.

        Args:
            now: Current time (UTC).

        Returns:
            bool: ``True`` before ``expires_at``.

        Raises:
            None
        """
        return now < self.expires_at
