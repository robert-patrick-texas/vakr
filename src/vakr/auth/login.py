"""Password login: verification, throttling, rehash, session start, and audit (SEC-009..012).

``LoginService.login`` is the whole login decision, without HTTP:

1. An attempt for an ``(account, address)`` pair that is still inside its
   throttle wait is refused as ``THROTTLED`` at once, without checking the
   password, so a flood costs neither CPU nor audit rows.
2. The password is checked against the account's Argon2id hash, or against
   a dummy hash when the account does not exist, so both paths take the
   same time (AUTH-004). A disabled account fails like a wrong password.
3. Success clears the failure run, rehashes the password if the hash
   parameters changed (SEC-009), ends the session being replaced, starts a
   new one (rotation after login, SEC-010), and audits the login.
4. Failure counts the run and audits it; the result says how long the HTTP
   layer should wait before answering (``delay_s``), from the 5th
   consecutive failure on, at most 30 s (E.12). The service never sleeps.

The client sees the same ``FAILED`` for every reason; only the audit log,
which administrators read, says which it was.

Dependencies:
    vakr.auth: Passwords, sessions, attempts (failure accounting), throttle, audit.
    vakr.ports: The metadata unit of work and the clock.

Safety:
    Argon2 runs outside any database transaction, so a slow hash never holds
    SQLite's write lock. Passwords stay inside ``Secret``; failure rows key
    the attempted username by HMAC, and audit names an account only if it
    exists. Call ``login`` from a worker thread, never on the event loop.

Example:
    >>> service = LoginService(factory, clock, passwords, sessions, keys)  # doctest: +SKIP
    >>> service.login("alice", Secret("..."), source="192.0.2.10").outcome  # doctest: +SKIP
    <LoginOutcome.SUCCESS: 'success'>
"""

from vakr.auth.attempts import (
    Attempt,
    LoginOutcome,
    LoginResult,
    account_key,
    clean_source,
    fail,
)
from vakr.auth.audit import record
from vakr.auth.keys import AuthKeys
from vakr.auth.passwords import Passwords
from vakr.auth.sessions import IssuedSession, SessionService
from vakr.auth.settings import AuthSettings
from vakr.auth.throttle import wait_remaining
from vakr.auth.tokens import token_digest
from vakr.credentials.secret import Secret
from vakr.domain.users import User
from vakr.ports.clock import Clock
from vakr.ports.metadata.unit_of_work import MetadataUnitOfWork, MetadataUnitOfWorkFactory

__all__ = ["LoginOutcome", "LoginResult", "LoginService", "clean_source"]


class LoginService:
    """Decides password logins."""

    def __init__(
        self,
        uow_factory: MetadataUnitOfWorkFactory,
        clock: Clock,
        passwords: Passwords,
        sessions: SessionService,
        keys: AuthKeys,
        settings: AuthSettings | None = None,
    ) -> None:
        """Create the service.

        Args:
            uow_factory: Opens metadata units of work.
            clock: Time source.
            passwords: Hashing and verification.
            sessions: Starts the session after a success.
            keys: The throttle key for account digests.
            settings: Throttle schedule and bounds; ``None`` for the E.12 defaults.

        Returns:
            None

        Raises:
            None
        """
        self._uow = uow_factory
        self._clock = clock
        self._passwords = passwords
        self._sessions = sessions
        self._keys = keys
        self._settings = settings or AuthSettings()

    def login(
        self,
        username: str,
        password: Secret,
        *,
        source: str | None,
        request_id: str | None = None,
        replaces: Secret | None = None,
    ) -> LoginResult:
        """Decide one login attempt.

        Args:
            username: Username as typed.
            password: Password as typed.
            source: Client address (after trusted-proxy handling, SEC-013).
            request_id: API request ID for the audit record.
            replaces: The session cookie the client sent, if any; that
                session ends on success (rotation after login).

        Returns:
            LoginResult: The outcome, the new session, and any wait.

        Raises:
            AuthBusyError: If no password-hashing slot frees up in time.
            StorageError: If the database is unavailable.
        """
        key = account_key(self._keys.throttle, username)
        attempt = Attempt(key, clean_source(source), self._clock.now_utc(), request_id)
        with self._uow() as uow:
            run = uow.login_failures.get(attempt.key, attempt.source)
            user = uow.users.get_by_username(username)
        wait = wait_remaining(run, attempt.at, self._settings.throttle)
        if wait > 0:
            return LoginResult(LoginOutcome.THROTTLED, retry_after_s=wait)
        if user is None:
            self._passwords.verify_dummy(password)
            return fail(self._uow, attempt, None, "unknown account", self._settings)
        matched = self._passwords.verify(user.password_hash, password)
        if not matched or not user.enabled:
            reason = "wrong password" if not matched else "account disabled"
            return fail(self._uow, attempt, user, reason, self._settings)
        new_hash = None
        if self._passwords.needs_rehash(user.password_hash):
            new_hash = self._passwords.hash(password)
        issued = self._succeed(attempt, user, new_hash, replaces)
        if issued is None:
            return fail(self._uow, attempt, user, "account changed during login", self._settings)
        return LoginResult(LoginOutcome.SUCCESS, issued=issued)

    def _succeed(
        self, attempt: Attempt, user: User, new_hash: str | None, replaces: Secret | None
    ) -> IssuedSession | None:
        """Record the login, start the session, and audit, in one transaction.

        Args:
            attempt: The attempt.
            user: The account as read before verification.
            new_hash: A rehash with current parameters, or ``None``.
            replaces: Cookie of the session to end, if any.

        Returns:
            IssuedSession | None: The session, or ``None`` when the account was
            disabled or its password changed while the hash was checked.

        Raises:
            StorageError: If the database is unavailable.
        """
        now = attempt.at
        with self._uow() as uow:
            rehashed = uow.users.record_login(
                user.id, at=now, verified_hash=user.password_hash, new_hash=new_hash
            )
            current = uow.users.get(user.id)  # read under the write lock: the newest state
            expected = new_hash if rehashed else user.password_hash
            if current is None or not current.enabled or current.password_hash != expected:
                return None  # leaving the block uncommitted rolls the login back
            uow.login_failures.clear(attempt.key, attempt.source)
            self._end_replaced(uow, replaces)
            issued = self._sessions.start(uow, current, source=attempt.source, now=now)
            note = "; password hash upgraded to current parameters" if rehashed else ""
            summary = f"signed in from {attempt.source}{note}"
            record(uow, "auth.login", user.id, summary, at=now, actor=user.username,
                   request_id=attempt.request_id)  # fmt: skip
            uow.commit()
        return issued

    def _end_replaced(self, uow: MetadataUnitOfWork, replaces: Secret | None) -> None:
        """End the session whose cookie the client presented while logging in.

        Args:
            uow: The login's unit of work.
            replaces: The presented cookie, or ``None``.

        Returns:
            None

        Raises:
            StorageError: If the delete cannot be written.
        """
        digest = token_digest(replaces) if replaces is not None else None
        if digest is not None and (old := uow.sessions.get_by_token_hash(digest)) is not None:
            uow.sessions.delete(old.id)
