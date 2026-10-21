"""Login attempts: results, account digests, and failure accounting (SEC-012, AUTH-004).

``LoginService`` (``vakr.auth.login``) decides each attempt; this module
holds what it returns and what it does when an attempt fails: count the
run for the ``(account, address)`` pair, keep the failure table bounded,
audit the failure, and say how long the HTTP layer should wait before
answering. The client gets the same ``FAILED`` for an unknown account, a
wrong password, and a disabled account; only the audit log says which.

Dependencies:
    hashlib, hmac: The keyed account digest.
    vakr.auth.audit, settings, throttle: Audit, bounds, and the schedule.
    vakr.ports.metadata: The unit of work.

Safety:
    Failure rows store ``HMAC(throttle key, attempted username)``, never the
    text, and a failure for an account that does not exist is audited as
    ``anonymous``, so a password typed into the username field is stored
    nowhere.

Example:
    >>> clean_source("192.0.2.10"), clean_source("not an address"), clean_source(None)
    ('192.0.2.10', 'unknown', 'unknown')
    >>> len(account_key(bytes(32), "Alice")) == 64 == len(account_key(bytes(32), "x" * 999))
    True
"""

import hashlib
import hmac
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from vakr.auth.audit import AUDIT_ANONYMOUS, record
from vakr.auth.sessions import IssuedSession
from vakr.auth.settings import AuthSettings
from vakr.auth.throttle import delay_after
from vakr.domain.enums import AuditOutcome
from vakr.domain.users import MAX_USERNAME_LENGTH, UNKNOWN_SOURCE, User, clean_source
from vakr.ports.metadata.unit_of_work import MetadataUnitOfWork, MetadataUnitOfWorkFactory

__all__ = [
    "UNKNOWN_SOURCE",
    "Attempt",
    "LoginOutcome",
    "LoginResult",
    "account_key",
    "clean_source",
    "fail",
]


class LoginOutcome(StrEnum):
    """What the HTTP layer should answer."""

    SUCCESS = "success"
    FAILED = "failed"
    THROTTLED = "throttled"


@dataclass(frozen=True, slots=True)
class LoginResult:
    """The outcome of one login attempt.

    Attributes:
        outcome: Success, failure, or refused while throttled.
        issued: The new session and token, on success.
        delay_s: For ``FAILED``: seconds to wait before answering (0 for the
            first four failures).
        retry_after_s: For ``THROTTLED``: seconds until an attempt is accepted.
    """

    outcome: LoginOutcome
    issued: IssuedSession | None = None
    delay_s: float = 0.0
    retry_after_s: float = 0.0


@dataclass(frozen=True, slots=True)
class Attempt:
    """One login attempt's identity for throttling and audit.

    Attributes:
        key: Keyed digest of the attempted username (``account_key``).
        source: Client address label (``vakr.domain.users.clean_source``).
        at: Arrival time (UTC).
        request_id: API request ID, if any.
    """

    key: str
    source: str
    at: datetime
    request_id: str | None = None


def account_key(throttle_key: bytes, username: str) -> str:
    """Return the keyed digest that names an attempted account in failure rows.

    Args:
        throttle_key: ``AuthKeys.throttle``.
        username: Username as typed; valid or not.

    Returns:
        str: 64 hex digits.

    Raises:
        None
    """
    # Bounded before hashing; canonical names are short, so nothing real is cut.
    name = username.strip().lower()[: MAX_USERNAME_LENGTH * 4]
    return hmac.new(throttle_key, b"vakr-login-v1\x00" + name.encode(), hashlib.sha256).hexdigest()


def fail(
    uow_factory: MetadataUnitOfWorkFactory,
    attempt: Attempt,
    user: User | None,
    reason: str,
    settings: AuthSettings,
) -> LoginResult:
    """Count a failed attempt, bound the failure table, and audit it.

    Args:
        uow_factory: Opens metadata units of work.
        attempt: The attempt.
        user: The account, if it exists.
        reason: Why it failed, for the audit log only.
        settings: Throttle schedule and bounds.

    Returns:
        LoginResult: ``FAILED`` with the delay to apply before answering.

    Raises:
        StorageError: If the database is unavailable.
    """
    policy = settings.throttle
    with uow_factory() as uow:
        run = uow.login_failures.record_failure(
            attempt.key, attempt.source, at=attempt.at, reset_before=attempt.at - policy.reset_after
        )
        if run.failures == 1:
            _bound_failures(uow, attempt.at, settings)
        denied = user is not None and not user.enabled
        summary = f"login failed from {attempt.source}: {reason}; {run.failures} in a row"
        record(uow, "auth.login", user.id if user else None, summary, at=attempt.at,
               actor=user.username if user else AUDIT_ANONYMOUS, request_id=attempt.request_id,
               outcome=AuditOutcome.DENIED if denied else AuditOutcome.FAILURE)  # fmt: skip
        uow.commit()
    return LoginResult(LoginOutcome.FAILED, delay_s=delay_after(run.failures, policy))


def _bound_failures(uow: MetadataUnitOfWork, now: datetime, settings: AuthSettings) -> None:
    """Keep the failure table bounded when a new run starts (E.12).

    Args:
        uow: The failure's unit of work.
        now: Current time.
        settings: The bounds.

    Returns:
        None

    Raises:
        StorageError: If the deletes cannot be written.
    """
    uow.login_failures.purge(now - settings.throttle.reset_after, limit=settings.purge_batch)
    excess = uow.login_failures.count() - settings.max_tracked_failures
    if excess > 0:
        # Evict the quietest runs; one being hammered right now is the newest, so it stays.
        uow.login_failures.purge(now, limit=excess)
