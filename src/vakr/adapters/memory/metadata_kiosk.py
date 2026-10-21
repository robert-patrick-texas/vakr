"""In-memory kiosk-token repository (Appendix E.12).

The reference implementation of ``KioskTokenRepository``: unversioned rows
staged in the unit of work's transaction, unique by ID, digest, and label,
and deleted with tombstones.

Dependencies:
    vakr.adapters.memory.metadata_store: The transaction.
    vakr.domain: The kiosk record and errors.

Safety:
    Digests are compared, never logged or echoed in errors.

Example:
    >>> from vakr.adapters.memory.metadata_store import MemoryMetadataStore, Transaction
    >>> MemoryKioskTokenRepository(Transaction(MemoryMetadataStore())).count()
    0
"""

from dataclasses import replace
from datetime import datetime

from vakr.adapters.memory.metadata_store import Transaction
from vakr.domain.errors import DuplicateError
from vakr.domain.ids import KioskTokenId
from vakr.domain.sessions import KioskToken
from vakr.domain.timestamps import to_epoch_us


class MemoryKioskTokenRepository:
    """Kiosk display tokens, by ``(label, id)``."""

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

    def add(self, token: KioskToken) -> None:
        """Stage a new kiosk token.

        Args:
            token: The record.

        Returns:
            None

        Raises:
            DuplicateError: If the ID, digest, or label is taken.
        """
        if self.get(token.id) is not None:
            raise DuplicateError("kiosk token", "id", token.id)
        if self.get_by_token_hash(token.token_hash) is not None:
            raise DuplicateError("kiosk token", "token", "-")
        if any(t.label == token.label for t in self._tx.records("kiosk_token")):
            raise DuplicateError("kiosk token", "label", token.label)
        self._tx.put("kiosk_token", token, None)

    def get(self, token_id: KioskTokenId) -> KioskToken | None:
        """Return one kiosk token.

        Args:
            token_id: The UUID.

        Returns:
            KioskToken | None: The record, or ``None``.

        Raises:
            None
        """
        return self._tx.get("kiosk_token", token_id)

    def get_by_token_hash(self, token_hash: bytes) -> KioskToken | None:
        """Return the kiosk token with this digest.

        Args:
            token_hash: SHA-256 of the token.

        Returns:
            KioskToken | None: The record, or ``None``.

        Raises:
            None
        """
        rows = self._tx.records("kiosk_token")
        return next((t for t in rows if t.token_hash == token_hash), None)

    def list_all(self, *, limit: int) -> tuple[KioskToken, ...]:
        """List kiosk tokens by ``(label, id)``.

        Args:
            limit: Most records to return.

        Returns:
            tuple[KioskToken, ...]: The records.

        Raises:
            None
        """
        rows = sorted(self._tx.records("kiosk_token"), key=lambda t: (t.label, str(t.id)))
        return tuple(rows[: max(limit, 0)])

    def touch(self, token_id: KioskTokenId, at: datetime) -> None:
        """Record use of a kiosk token; a missing one is ignored.

        Args:
            token_id: The token.
            at: Time of use.

        Returns:
            None

        Raises:
            None
        """
        if (stored := self.get(token_id)) is not None:
            self._tx.put("kiosk_token", replace(stored, last_used_at=at), None)

    def delete(self, token_id: KioskTokenId) -> bool:
        """Delete one kiosk token.

        Args:
            token_id: The token.

        Returns:
            bool: Whether it existed.

        Raises:
            None
        """
        if self.get(token_id) is None:
            return False
        self._tx.delete("kiosk_token", token_id)
        return True

    def purge_expired(self, *, now: datetime, limit: int) -> int:
        """Delete up to ``limit`` expired kiosk tokens, earliest first.

        Args:
            now: Current time.
            limit: Most rows to delete.

        Returns:
            int: Tokens deleted.

        Raises:
            None
        """
        dead = sorted(
            (t for t in self._tx.records("kiosk_token") if t.expires_at <= now),
            key=lambda t: (to_epoch_us(t.expires_at), t.id.int),
        )[: max(limit, 0)]
        for token in dead:
            self._tx.delete("kiosk_token", token.id)
        return len(dead)

    def count(self) -> int:
        """Return how many kiosk tokens exist.

        Args:
            None

        Returns:
            int: Count.

        Raises:
            None
        """
        return len(self._tx.records("kiosk_token"))
