"""Session lifecycle: start, validate and touch, rotate, revoke (SEC-010, AUTH-002).

A login starts a session and hands the client its opaque token once. Each
request presents the token; ``validate`` finds the session by digest,
checks idle and absolute expiry and that the account is still enabled,
and returns the ``Principal``. To keep reads cheap, the last-seen time is
written at most once per touch interval (60 s), so idle expiry can arrive
up to that much *earlier* than 30 minutes after the very last request,
never later. ``rotate`` swaps the token while keeping the absolute
expiry; password, role, and enabled-state changes revoke every session of
the account instead (``vakr.auth.accounts``). Expired rows are deleted by
``vakr.auth.purge``.

Dependencies:
    vakr.auth.audit, principal, settings, tokens: Records and token helpers.
    vakr.ports: The metadata unit of work and the clock.

Safety:
    Tokens stay inside ``Secret``; only digests reach storage. A failed
    last-seen write is logged and the request still succeeds: storage
    trouble must not sign users out.

Example:
    >>> service = SessionService(memory_uow_factory(), SystemClock())  # doctest: +SKIP
    >>> service.validate(Secret("vks_not-a-real-token")) is None  # doctest: +SKIP
    True
"""

import logging
from dataclasses import dataclass
from datetime import datetime

from vakr.auth.audit import AUDIT_ANONYMOUS, record
from vakr.auth.principal import Principal, PrincipalKind
from vakr.auth.settings import AuthSettings
from vakr.auth.tokens import SESSION_PREFIX, new_token, token_digest, token_kind
from vakr.credentials.secret import Secret
from vakr.domain.errors import StorageError
from vakr.domain.ids import SessionId, UserId, new_id
from vakr.domain.sessions import Session
from vakr.domain.users import User, clean_source
from vakr.ports.clock import Clock
from vakr.ports.metadata.unit_of_work import MetadataUnitOfWork, MetadataUnitOfWorkFactory

_LOG = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class IssuedSession:
    """A new session and its token, which the client receives exactly once.

    Attributes:
        session: The stored session (its ``repr`` hides the digest).
        token: The opaque token for the cookie; ``repr`` shows ``Secret('***')``.
    """

    session: Session
    token: Secret


def _digest(token: Secret) -> bytes | None:
    """Return a session token's digest, or ``None`` if it is not a session token.

    Args:
        token: The presented token.

    Returns:
        bytes | None: The digest.

    Raises:
        None
    """
    return token_digest(token) if token_kind(token) == "session" else None


class SessionService:
    """Creates, checks, rotates, and revokes sessions."""

    def __init__(
        self, uow_factory: MetadataUnitOfWorkFactory, clock: Clock, settings: AuthSettings
    ) -> None:
        """Create the service.

        Args:
            uow_factory: Opens metadata units of work.
            clock: Time source.
            settings: Lifetimes and bounds.

        Returns:
            None

        Raises:
            None
        """
        self._uow = uow_factory
        self._clock = clock
        self._settings = settings

    def start(
        self, uow: MetadataUnitOfWork, user: User, *, source: str, now: datetime
    ) -> IssuedSession:
        """Create a session inside the caller's unit of work, dropping the oldest over the cap.

        Args:
            uow: The login's unit of work; the caller commits.
            user: The authenticated account.
            source: Client address label.
            now: Login time (UTC).

        Returns:
            IssuedSession: The session and its token.

        Raises:
            StorageError: If the session cannot be written.
        """
        cap = self._settings.max_sessions_per_user
        existing = uow.sessions.list_for_user(user.id, limit=cap)
        for old in existing[: max(len(existing) - cap + 1, 0)]:
            uow.sessions.delete(old.id)
        token = new_token(SESSION_PREFIX)
        digest = token_digest(token) or b""
        expires = now + self._settings.session_lifetime
        session = Session(new_id(SessionId), user.id, digest, now, now, expires, source)
        uow.sessions.add(session)
        return IssuedSession(session, token)

    def validate(self, token: Secret) -> Principal | None:
        """Resolve a presented session token to its principal, touching it when due.

        Args:
            token: The cookie value.

        Returns:
            Principal | None: The signed-in user, or ``None`` for an unknown,
            expired, or revoked token or a disabled account.

        Raises:
            StorageError: If the session cannot be read.
        """
        digest = _digest(token)
        if digest is None:
            return None
        now = self._clock.now_utc()
        with self._uow() as uow:
            session = uow.sessions.get_by_token_hash(digest)
            if session is None or not session.is_live(now, idle=self._settings.session_idle):
                return None
            user = uow.users.get(session.user_id)
            if user is None or not user.enabled:
                return None
            if now - session.last_seen_at >= self._settings.touch_interval:
                self._touch(uow, session.id, now)
        return Principal(PrincipalKind.USER, user.username, user.role, user.id, session.id)

    def _touch(self, uow: MetadataUnitOfWork, session_id: SessionId, now: datetime) -> None:
        """Write the last-seen time, tolerating a busy or failing database.

        Args:
            uow: The validation's unit of work.
            session_id: The session.
            now: Time of use.

        Returns:
            None

        Raises:
            None
        """
        try:
            uow.sessions.touch(session_id, now)
            uow.commit()
        except StorageError as exc:
            _LOG.warning("could not record session use; the next request retries: %s", exc)

    def rotate(self, token: Secret, *, source: str | None = None) -> IssuedSession | None:
        """Replace a live session's token and ID, keeping its absolute expiry.

        Args:
            token: The current cookie value.
            source: New client address, or ``None`` to keep the old one.

        Returns:
            IssuedSession | None: The new session, or ``None`` if the old one
            is not live.

        Raises:
            StorageError: If the change cannot be written.
        """
        digest = _digest(token)
        if digest is None:
            return None
        now = self._clock.now_utc()
        with self._uow() as uow:
            old = uow.sessions.get_by_token_hash(digest)
            if old is None or not old.is_live(now, idle=self._settings.session_idle):
                return None
            uow.sessions.delete(old.id)
            fresh = new_token(SESSION_PREFIX)
            seen = max(now, old.created_at)
            session = Session(
                new_id(SessionId),
                old.user_id,
                token_digest(fresh) or b"",
                old.created_at,
                seen,
                old.expires_at,
                old.source if source is None else clean_source(source),
            )
            uow.sessions.add(session)
            uow.commit()
        return IssuedSession(session, fresh)

    def revoke(self, token: Secret, *, request_id: str | None = None) -> bool:
        """End the session a token belongs to (logout), with audit (SEC-018).

        Args:
            token: The cookie value.
            request_id: API request ID, if any.

        Returns:
            bool: Whether a session ended.

        Raises:
            StorageError: If the change cannot be written.
        """
        digest = _digest(token)
        if digest is None:
            return False
        with self._uow() as uow:
            session = uow.sessions.get_by_token_hash(digest)
            if session is None:
                return False
            user = uow.users.get(session.user_id)
            uow.sessions.delete(session.id)
            actor = user.username if user else AUDIT_ANONYMOUS
            record(uow, "auth.logout", session.id, "signed out", at=self._clock.now_utc(),
                   actor=actor, request_id=request_id)  # fmt: skip
            uow.commit()
        return True

    def sessions_of(self, user_id: UserId) -> tuple[Session, ...]:
        """List one user's sessions, oldest first (bounded by the per-user cap).

        Args:
            user_id: The account.

        Returns:
            tuple[Session, ...]: The sessions, live or not yet purged.

        Raises:
            StorageError: If they cannot be read.
        """
        with self._uow() as uow:
            return uow.sessions.list_for_user(user_id, limit=self._settings.max_sessions_per_user)
