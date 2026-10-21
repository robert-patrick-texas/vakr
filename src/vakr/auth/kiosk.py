"""Kiosk tokens for unattended displays: create, validate, list, revoke (Appendix E.12).

A NOC wall display cannot type a password every 30 minutes. An
administrator creates a kiosk token instead, labelled for the display,
and pastes it into the display's browser once. The token grants the
operator role only (view, never change), has no idle expiry, and expires
absolutely after 30 days by default. Use is recorded at most once per
touch interval.

Dependencies:
    vakr.auth.audit, principal, settings, tokens: Records and token helpers.
    vakr.ports: The metadata unit of work and the clock.

Safety:
    The token is returned once as a ``Secret`` and stored only as a digest.
    At most ``max_kiosk_tokens`` exist, so the table stays bounded.

Example:
    >>> service = KioskService(memory_uow_factory(), SystemClock())  # doctest: +SKIP
    >>> issued = service.create("noc-wall", actor="alice")  # doctest: +SKIP
"""

import logging
from dataclasses import dataclass

from vakr.auth.audit import record
from vakr.auth.principal import Principal, PrincipalKind
from vakr.auth.settings import AuthSettings
from vakr.auth.tokens import KIOSK_PREFIX, new_token, token_digest, token_kind
from vakr.credentials.secret import Secret
from vakr.domain.errors import StorageError, ValidationError
from vakr.domain.ids import KioskTokenId, new_id
from vakr.domain.sessions import KioskToken
from vakr.ports.clock import Clock
from vakr.ports.metadata.unit_of_work import MetadataUnitOfWorkFactory

_LOG = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class IssuedKiosk:
    """A new kiosk token record and the token, shown exactly once.

    Attributes:
        record: The stored record (``repr`` hides the digest).
        token: The token; ``repr`` shows ``Secret('***')``.
    """

    record: KioskToken
    token: Secret


class KioskService:
    """Manages kiosk display tokens."""

    def __init__(
        self, uow_factory: MetadataUnitOfWorkFactory, clock: Clock, settings: AuthSettings
    ) -> None:
        """Create the service.

        Args:
            uow_factory: Opens metadata units of work.
            clock: Time source.
            settings: Lifetime and bounds.

        Returns:
            None

        Raises:
            None
        """
        self._uow = uow_factory
        self._clock = clock
        self._settings = settings

    def create(self, label: str, *, actor: str, request_id: str | None = None) -> IssuedKiosk:
        """Create a kiosk token, with audit (SEC-018).

        Args:
            label: Unique name for the display, such as ``noc-wall``.
            actor: The administrator.
            request_id: API request ID, if any.

        Returns:
            IssuedKiosk: The record and the token to hand over once.

        Raises:
            ValidationError: If the label is invalid or the token limit is reached.
            DuplicateError: If the label is taken.
            StorageError: If it cannot be written.
        """
        now = self._clock.now_utc()
        token = new_token(KIOSK_PREFIX)
        kiosk = KioskToken(
            new_id(KioskTokenId),
            label.strip(),
            token_digest(token) or b"",
            actor,
            now,
            now + self._settings.kiosk_lifetime,
        )
        with self._uow() as uow:
            if uow.kiosk_tokens.count() >= self._settings.max_kiosk_tokens:
                raise ValidationError(
                    f"at most {self._settings.max_kiosk_tokens} kiosk tokens "
                    "may exist; revoke one first",
                    field="label",
                )
            uow.kiosk_tokens.add(kiosk)
            summary = f"created kiosk token {kiosk.label}, expires {kiosk.expires_at:%Y-%m-%d}"
            record(uow, "kiosk.create", kiosk.id, summary, at=now, actor=actor,
                   request_id=request_id)  # fmt: skip
            uow.commit()
        return IssuedKiosk(kiosk, token)

    def validate(self, token: Secret) -> Principal | None:
        """Resolve a presented kiosk token to an operator principal.

        Args:
            token: The presented token.

        Returns:
            Principal | None: The kiosk principal, or ``None`` for an unknown,
            expired, or revoked token.

        Raises:
            StorageError: If the record cannot be read.
        """
        digest = token_digest(token) if token_kind(token) == "kiosk" else None
        if digest is None:
            return None
        now = self._clock.now_utc()
        with self._uow() as uow:
            kiosk = uow.kiosk_tokens.get_by_token_hash(digest)
            if kiosk is None or not kiosk.is_live(now):
                return None
            last = kiosk.last_used_at
            if last is None or now - last >= self._settings.touch_interval:
                try:
                    uow.kiosk_tokens.touch(kiosk.id, now)
                    uow.commit()
                except StorageError as exc:
                    _LOG.warning("could not record kiosk use; the next request retries: %s", exc)
        return Principal(PrincipalKind.KIOSK, f"kiosk:{kiosk.label}", kiosk.role, kiosk_id=kiosk.id)

    def tokens(self) -> tuple[KioskToken, ...]:
        """List kiosk tokens by label.

        Args:
            None

        Returns:
            tuple[KioskToken, ...]: Every token (at most ``max_kiosk_tokens``).

        Raises:
            StorageError: If they cannot be read.
        """
        with self._uow() as uow:
            return uow.kiosk_tokens.list_all(limit=self._settings.max_kiosk_tokens)

    def revoke(self, token_id: KioskTokenId, *, actor: str, request_id: str | None = None) -> bool:
        """Revoke a kiosk token, with audit.

        Args:
            token_id: The token record's UUID.
            actor: The administrator.
            request_id: API request ID, if any.

        Returns:
            bool: Whether a token was revoked.

        Raises:
            StorageError: If the change cannot be written.
        """
        with self._uow() as uow:
            kiosk = uow.kiosk_tokens.get(token_id)
            if kiosk is None or not uow.kiosk_tokens.delete(token_id):
                return False
            record(uow, "kiosk.revoke", token_id, f"revoked kiosk token {kiosk.label}",
                   at=self._clock.now_utc(), actor=actor, request_id=request_id)  # fmt: skip
            uow.commit()
        return True
