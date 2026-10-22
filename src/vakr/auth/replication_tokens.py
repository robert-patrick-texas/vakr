"""Replication tokens: issue, validate, list, and revoke the partner node's pull token (MPOS-006).

Each node of a two-position pair issues one token for the other
(``sudo vakr replication token create LABEL``), and the other installs it in
``/etc/vakr/secrets/replication.token``. A token is checked on every pull
and grants only the replication scopes (``vakr.domain.replication.SCOPES``)
on ``/api/v1/replication/*``; it never becomes a session, a kiosk display,
or an operator. Use is recorded at most once per ``TOUCH_INTERVAL``.

Dependencies:
    vakr.auth.audit, kiosk_rules, tokens: Records, label rules, digests.
    vakr.ports: The metadata unit of work and the clock.

Safety:
    The token is returned once as a ``Secret`` and stored only as a digest.
    At most ``MAX_TOKENS`` exist, so the table stays bounded (E.13).

Example:
    >>> MIN_LIFETIME.days, DEFAULT_LIFETIME.days, MAX_LIFETIME.days, MAX_TOKENS
    (1, 365, 730, 10)
"""

import logging
from dataclasses import dataclass
from datetime import timedelta

from vakr.auth.audit import record
from vakr.auth.kiosk_rules import check_label
from vakr.auth.tokens import REPLICATION_PREFIX, new_token, token_digest, token_kind
from vakr.credentials.secret import Secret
from vakr.domain.errors import StorageError, ValidationError
from vakr.domain.ids import KioskTokenId, ReplicationTokenId, new_id
from vakr.domain.replication import ReplicationToken
from vakr.ports.clock import Clock
from vakr.ports.metadata.unit_of_work import MetadataUnitOfWorkFactory

_LOG = logging.getLogger(__name__)

MIN_LIFETIME = timedelta(days=1)
DEFAULT_LIFETIME = timedelta(days=365)
MAX_LIFETIME = timedelta(days=730)
"""A pull token lives on a server, not a wall display, so it may outlive a kiosk token."""
MAX_TOKENS = 10
"""Two nodes need one each; the rest is room for rotation (E.13)."""
TOUCH_INTERVAL = timedelta(seconds=60)


@dataclass(frozen=True, slots=True)
class Replicator:
    """An admitted partner node: which token it presented.

    Attributes:
        token_id: The token record.
        label: The token's label, for logs (``replication:<label>``).
    """

    token_id: ReplicationTokenId
    label: str


@dataclass(frozen=True, slots=True)
class IssuedReplicationToken:
    """A new token record and the token, shown exactly once.

    Attributes:
        record: The stored record (``repr`` hides the digest).
        token: The token; ``repr`` shows ``Secret('***')``.
    """

    record: ReplicationToken
    token: Secret


def check_replication_lifetime(lifetime: timedelta) -> timedelta:
    """Enforce ``MIN_LIFETIME`` to ``MAX_LIFETIME`` (E.13).

    Args:
        lifetime: The requested lifetime.

    Returns:
        timedelta: The lifetime.

    Raises:
        ValidationError: Outside the range.
    """
    if not MIN_LIFETIME <= lifetime <= MAX_LIFETIME:
        raise ValidationError(
            f"lifetime must be {MIN_LIFETIME.days} to {MAX_LIFETIME.days} days", field="lifetime"
        )
    return lifetime


class ReplicationTokenService:
    """Manages the tokens partner nodes present to pull from this node."""

    def __init__(self, uow_factory: MetadataUnitOfWorkFactory, clock: Clock) -> None:
        """Create the service.

        Args:
            uow_factory: Opens metadata units of work.
            clock: Time source.

        Returns:
            None

        Raises:
            None
        """
        self._uow = uow_factory
        self._clock = clock

    def create(
        self, label: str, *, actor: str, lifetime: timedelta | None = None
    ) -> IssuedReplicationToken:
        """Create a token, with audit (SEC-018).

        Args:
            label: Unique name, such as ``west`` for the node that presents it.
            actor: The administrator (the OS user for the CLI).
            lifetime: How long it lives; ``DEFAULT_LIFETIME`` when ``None``.

        Returns:
            IssuedReplicationToken: The record and the token to install once.

        Raises:
            ValidationError: If the label or lifetime is invalid or ``MAX_TOKENS`` exist.
            DuplicateError: If the label is taken.
            StorageError: If it cannot be written.
        """
        life = DEFAULT_LIFETIME if lifetime is None else check_replication_lifetime(lifetime)
        now = self._clock.now_utc()
        token = new_token(REPLICATION_PREFIX)
        rid = ReplicationTokenId(new_id(KioskTokenId))
        made = ReplicationToken(rid, check_label(label), token_digest(token) or b"", actor, now,
                                now + life)  # fmt: skip
        with self._uow() as uow:
            if uow.replication_tokens.count() >= MAX_TOKENS:
                raise ValidationError(f"at most {MAX_TOKENS} replication tokens may exist; "
                                      "revoke one first", field="label")  # fmt: skip
            uow.replication_tokens.add(made)
            summary = f"created replication token {made.label}, expires {made.expires_at:%Y-%m-%d}"
            record(uow, "replication.token_create", made.id, summary, at=now, actor=actor)
            uow.commit()
        return IssuedReplicationToken(made, token)

    def validate(self, token: Secret) -> Replicator | None:
        """Resolve a presented token to the partner it was issued for.

        Args:
            token: The presented token.

        Returns:
            Replicator | None: The partner, or ``None`` for an unknown,
            expired, revoked, or non-replication token.

        Raises:
            StorageError: If the record cannot be read.
        """
        digest = token_digest(token) if token_kind(token) == "replication" else None
        if digest is None:
            return None
        now = self._clock.now_utc()
        with self._uow() as uow:
            found = uow.replication_tokens.get_by_token_hash(digest)
            if found is None or not found.is_live(now):
                return None
            last = found.last_used_at
            if last is None or now - last >= TOUCH_INTERVAL:
                try:
                    uow.replication_tokens.touch(found.id, now)
                    uow.commit()
                except StorageError as exc:
                    _LOG.warning("could not record replication token use; retried later: %s", exc)
        return Replicator(ReplicationTokenId(found.id), found.label)

    def tokens(self) -> tuple[ReplicationToken, ...]:
        """List the tokens by label.

        Args:
            None

        Returns:
            tuple[ReplicationToken, ...]: Every token (at most ``MAX_TOKENS``).

        Raises:
            StorageError: If they cannot be read.
        """
        with self._uow() as uow:
            found = uow.replication_tokens.list_all(limit=MAX_TOKENS)
        return tuple(t for t in found if isinstance(t, ReplicationToken))

    def revoke(self, token_id: KioskTokenId, *, actor: str) -> bool:
        """Revoke a token at once, with audit; the partner's next pull is refused.

        Args:
            token_id: The record's UUID.
            actor: The administrator.

        Returns:
            bool: Whether a token was revoked.

        Raises:
            StorageError: If the change cannot be written.
        """
        with self._uow() as uow:
            found = uow.replication_tokens.get(token_id)
            if found is None or not uow.replication_tokens.delete(token_id):
                return False
            record(uow, "replication.token_revoke", token_id,
                   f"revoked replication token {found.label}", at=self._clock.now_utc(),
                   actor=actor)  # fmt: skip
            uow.commit()
        return True
