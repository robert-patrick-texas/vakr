"""SQLite login-failure repository (SEC-012, Appendix E.12).

Failure runs live in ``login_failure`` (migration ``0003``), keyed by
``(account_key, source)``. ``record_failure`` calls ``begin_write`` before
it reads, so the read-modify-write of a run happens under SQLite's write
lock and two concurrent failures always count as two.

Dependencies:
    vakr.adapters.sqlite.encoding, errors, metadata.transaction.
    vakr.domain: The failure record and errors.

Safety:
    SQL is fixed text with bound values. Account keys are digests, so no
    attempted username is stored readable.

Example:
    >>> SqliteLoginFailureRepository(tx).count()  # doctest: +SKIP
    0
"""

import sqlite3
from dataclasses import replace
from datetime import datetime

from vakr.adapters.sqlite.encoding import decode_time, encode_time
from vakr.adapters.sqlite.errors import ErrorContext
from vakr.adapters.sqlite.metadata.transaction import SqliteTransaction
from vakr.domain.errors import StorageError
from vakr.domain.users import LoginFailure

_FAILURE_COLUMNS = "account_key, source, failures, first_failed_at, last_failed_at"


def _failure(r: sqlite3.Row) -> LoginFailure:
    """Decode a failure-run row.

    Args:
        r: The row.

    Returns:
        LoginFailure: The run.

    Raises:
        StorageError: If the row no longer forms a valid run.
    """
    try:
        return LoginFailure(
            r["account_key"],
            r["source"],
            r["failures"],
            decode_time(r["first_failed_at"]),
            decode_time(r["last_failed_at"]),
        )
    except ValueError:
        raise StorageError("a stored login-failure record is not valid", retryable=False) from None


class SqliteLoginFailureRepository:
    """Consecutive login failures per ``(account_key, source)``."""

    def __init__(self, tx: SqliteTransaction) -> None:
        """Bind to a unit of work's transaction.

        Args:
            tx: The transaction.

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
            StorageError: If the row cannot be read.
        """
        rows = self._tx.query(
            f"SELECT {_FAILURE_COLUMNS} FROM login_failure "  # noqa: S608  # constant columns
            "WHERE account_key = ? AND source = ?",
            (account_key, source),
        )
        return _failure(rows[0]) if rows else None

    def record_failure(
        self, account_key: str, source: str, *, at: datetime, reset_before: datetime
    ) -> LoginFailure:
        """Count one more failure under the write lock, restarting a quiet run.

        Args:
            account_key: Keyed digest of the username.
            source: Client address label.
            at: Failure time.
            reset_before: Runs last failed at or before this restart at 1.

        Returns:
            LoginFailure: The run after this failure.

        Raises:
            ValidationError: If a field is malformed.
            StorageError: If the write fails.
        """
        self._tx.begin_write()
        old = self.get(account_key, source)
        if old is None or old.last_failed_at <= reset_before:
            new = LoginFailure(account_key, source, 1, at, at)
        else:
            new = replace(
                old, failures=old.failures + 1, last_failed_at=max(at, old.last_failed_at)
            )
        self._tx.execute(
            f"INSERT OR REPLACE INTO login_failure ({_FAILURE_COLUMNS}) "  # noqa: S608  # constant
            "VALUES (?, ?, ?, ?, ?)",
            (
                new.account_key,
                new.source,
                new.failures,
                encode_time(new.first_failed_at),
                encode_time(new.last_failed_at),
            ),
            ErrorContext("login failure"),
        )
        return new

    def clear(self, account_key: str, source: str) -> None:
        """Forget one run.

        Args:
            account_key: Keyed digest of the username.
            source: Client address label.

        Returns:
            None

        Raises:
            StorageError: If the write fails.
        """
        self._tx.begin_write()
        self._tx.execute(
            "DELETE FROM login_failure WHERE account_key = ? AND source = ?", (account_key, source)
        )

    def purge(self, before: datetime, *, limit: int) -> int:
        """Delete up to ``limit`` runs that last failed before ``before``, oldest first.

        Args:
            before: Cut-off time.
            limit: Most rows to delete.

        Returns:
            int: Rows deleted.

        Raises:
            StorageError: If the write fails.
        """
        self._tx.begin_write()
        return self._tx.execute(
            "DELETE FROM login_failure WHERE (account_key, source) IN ("
            "SELECT account_key, source FROM login_failure WHERE last_failed_at < ? "
            "ORDER BY last_failed_at LIMIT ?)",
            (encode_time(before), max(limit, 0)),
        )

    def count(self) -> int:
        """Return how many runs are tracked.

        Args:
            None

        Returns:
            int: Row count.

        Raises:
            StorageError: If the table cannot be read.
        """
        return int(self._tx.query("SELECT count(*) FROM login_failure")[0][0])
