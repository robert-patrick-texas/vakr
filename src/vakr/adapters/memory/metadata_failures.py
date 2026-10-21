"""In-memory login-failure repository (SEC-012, Appendix E.12).

The reference implementation of ``LoginFailureRepository``. Failure runs
are unversioned rows keyed by a UUID derived from ``(account_key,
source)``, so the shared ``Transaction`` can stage, replace, and delete
them like any other row.

Dependencies:
    hashlib, uuid: The derived row key.
    vakr.adapters.memory.metadata_store: The transaction.
    vakr.domain: The failure record.

Safety:
    Account keys are digests; nothing here logs.

Example:
    >>> from vakr.adapters.memory.metadata_store import MemoryMetadataStore, Transaction
    >>> MemoryLoginFailureRepository(Transaction(MemoryMetadataStore())).count()
    0
"""

import hashlib
from dataclasses import dataclass, replace
from datetime import datetime
from uuid import UUID

from vakr.adapters.memory.metadata_store import Transaction
from vakr.domain.timestamps import to_epoch_us
from vakr.domain.users import LoginFailure


@dataclass(frozen=True, slots=True)
class _FailureRow:
    """A failure run wrapped with the derived key the shared transaction stages by.

    Attributes:
        id: UUID derived from ``(account_key, source)``.
        failure: The run.
    """

    id: UUID
    failure: LoginFailure


def _row_key(account_key: str, source: str) -> UUID:
    """Derive the staging key of one ``(account_key, source)`` pair.

    Args:
        account_key: Keyed digest of the username.
        source: Client address label.

    Returns:
        UUID: A stable key; the NUL separator keeps the pair unambiguous.

    Raises:
        None
    """
    return UUID(bytes=hashlib.sha256(f"{account_key}\0{source}".encode()).digest()[:16])


class MemoryLoginFailureRepository:
    """Consecutive login failures per ``(account_key, source)``."""

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

    def get(self, account_key: str, source: str) -> LoginFailure | None:
        """Return one run.

        Args:
            account_key: Keyed digest of the username.
            source: Client address label.

        Returns:
            LoginFailure | None: The run, or ``None``.

        Raises:
            None
        """
        row: _FailureRow | None = self._tx.get("login_failure", _row_key(account_key, source))
        return None if row is None else row.failure

    def record_failure(
        self, account_key: str, source: str, *, at: datetime, reset_before: datetime
    ) -> LoginFailure:
        """Count one more failure, restarting a quiet run.

        Args:
            account_key: Keyed digest of the username.
            source: Client address label.
            at: Failure time.
            reset_before: Runs last failed at or before this restart at 1.

        Returns:
            LoginFailure: The run after this failure.

        Raises:
            ValidationError: If a field is malformed.
        """
        old = self.get(account_key, source)
        if old is None or old.last_failed_at <= reset_before:
            new = LoginFailure(account_key, source, 1, at, at)
        else:
            new = replace(
                old, failures=old.failures + 1, last_failed_at=max(at, old.last_failed_at)
            )
        key = _row_key(account_key, source)
        self._tx.put("login_failure", _FailureRow(key, new), None)
        return new

    def clear(self, account_key: str, source: str) -> None:
        """Forget one run.

        Args:
            account_key: Keyed digest of the username.
            source: Client address label.

        Returns:
            None

        Raises:
            None
        """
        self._tx.delete("login_failure", _row_key(account_key, source))

    def purge(self, before: datetime, *, limit: int) -> int:
        """Delete up to ``limit`` runs that last failed before ``before``, oldest first.

        Args:
            before: Cut-off time.
            limit: Most rows to delete.

        Returns:
            int: Rows deleted.

        Raises:
            None
        """
        rows: list[_FailureRow] = self._tx.records("login_failure")
        stale = sorted(
            (r for r in rows if r.failure.last_failed_at < before),
            key=lambda r: (to_epoch_us(r.failure.last_failed_at), r.id.int),
        )[: max(limit, 0)]
        for row in stale:
            self._tx.delete("login_failure", row.id)
        return len(stale)

    def count(self) -> int:
        """Return how many runs are tracked.

        Args:
            None

        Returns:
            int: Row count.

        Raises:
            None
        """
        return len(self._tx.records("login_failure"))
