"""Apply migrations to one SQLite file, crash-safely (5.6, MIG-002, MIG-003).

Each migration is applied in three steps:

1. Under the write lock, confirm the schema is still at the version before
   this one, then commit an ``in_progress`` journal row by itself.
2. A transactional migration then runs its SQL and marks itself
   ``applied`` in one ``BEGIN IMMEDIATE`` transaction. If it fails, the
   transaction rolls back and the marker is removed: the database is
   exactly as before. If the process dies, SQLite discards the unfinished
   transaction, the marker survives, and status reports ``INTERRUPTED``;
   since nothing was applied, the next ``migrate`` simply runs it again.
3. A non-transactional migration runs its statements one by one and is
   marked ``applied`` afterwards. A failure or crash leaves the marker and
   possibly some changes, so ``migrate`` refuses to continue until the
   operator restores the backup.

Dependencies:
    sqlite3: Running migration scripts.
    vakr.adapters.sqlite.connection, encoding, errors: Transactions and translation.
    vakr.adapters.sqlite.migrations.journal, model, status.

Safety:
    Failure messages carry the SQLite error name, never its text or the SQL.

Example:
    >>> runner = MigrationRunner(path, family, SystemClock(), "vakr 7.2.0")  # doctest: +SKIP
    >>> runner.run(conn, family.migrations)  # doctest: +SKIP
"""

import sqlite3
from collections.abc import Iterable
from datetime import timedelta
from pathlib import Path

from vakr.adapters.sqlite.connection import begin, commit, rollback
from vakr.adapters.sqlite.encoding import decode_time, encode_time
from vakr.adapters.sqlite.errors import translated
from vakr.adapters.sqlite.migrations import journal
from vakr.adapters.sqlite.migrations.model import Migration, SchemaFamily
from vakr.adapters.sqlite.migrations.status import DOC_HINT, evaluate
from vakr.ports.clock import Clock
from vakr.ports.migrations import AppliedMigration, MigrationError


class MigrationRunner:
    """Applies migrations to one open, read-write connection.

    Attributes:
        None beyond the private path, family, clock, and release label.
    """

    def __init__(self, path: Path, family: SchemaFamily, clock: Clock, applied_by: str) -> None:
        """Prepare a runner.

        Args:
            path: The database file, for messages.
            family: Its schema family.
            clock: Time source for the journal.
            applied_by: Release label for the journal.

        Returns:
            None

        Raises:
            None
        """
        self._path = path
        self._family = family
        self._clock = clock
        self._applied_by = applied_by

    def run(
        self, conn: sqlite3.Connection, migrations: Iterable[Migration]
    ) -> tuple[AppliedMigration, ...]:
        """Stamp the file if needed, then apply ``migrations`` in order.

        Args:
            conn: Read-write connection with no open transaction.
            migrations: The pending migrations, oldest first.

        Returns:
            tuple[AppliedMigration, ...]: What was applied.

        Raises:
            MigrationError: If the schema moved underneath or a migration fails.
            StorageError: If the journal cannot be written.
        """
        self._stamp(conn)
        return tuple(self._apply(conn, migration) for migration in migrations)

    def _stamp(self, conn: sqlite3.Connection) -> None:
        """Create the journal and stamp the file's family ID if needed.

        Args:
            conn: Read-write connection.

        Returns:
            None

        Raises:
            StorageError: If the file cannot be written.
        """
        begin(conn, immediate=True)
        try:
            with translated():
                journal.stamp(conn, self._family.application_id)
        except BaseException:
            rollback(conn)
            raise
        commit(conn)

    def _apply(self, conn: sqlite3.Connection, migration: Migration) -> AppliedMigration:
        """Apply one migration as the module docstring describes.

        Args:
            conn: Read-write connection with the journal in place.
            migration: The next migration.

        Returns:
            AppliedMigration: What was applied and how long it took.

        Raises:
            MigrationError: If the schema moved underneath or the migration fails.
            StorageError: If the journal cannot be written.
        """
        started = encode_time(self._clock.now_utc())
        self._mark_started(conn, migration, started)
        try:
            if migration.transactional:
                begin(conn, immediate=True)
            conn.executescript(migration.sql)
            if not migration.transactional:
                begin(conn, immediate=True)
            finished = encode_time(self._clock.now_utc())
            journal.mark_applied(conn, migration.version, at_us=finished)
            conn.execute("COMMIT")
        except sqlite3.Error as exc:
            raise self._failed(conn, migration, exc) from None
        duration = timedelta(microseconds=max(0, finished - started))
        return AppliedMigration(
            migration.version, migration.name, decode_time(finished), duration, self._applied_by
        )

    def _mark_started(self, conn: sqlite3.Connection, migration: Migration, at_us: int) -> None:
        """Commit the ``in_progress`` marker, if the schema is where it should be.

        Args:
            conn: Read-write connection.
            migration: The migration about to run.
            at_us: Start time, integer microseconds.

        Returns:
            None

        Raises:
            MigrationError: If another process moved the schema meanwhile.
            StorageError: If the journal cannot be written.
        """
        begin(conn, immediate=True)
        try:
            with translated():
                facts = journal.read_facts(conn, self._family.application_id)
            self._require_next(facts, migration)
            with translated():
                journal.mark_started(conn, migration, at_us=at_us, applied_by=self._applied_by)
        except BaseException:
            rollback(conn)
            raise
        commit(conn)

    def _require_next(self, facts: journal.DatabaseFacts, migration: Migration) -> None:
        """Check, under the write lock, that ``migration`` is the next one to apply.

        Args:
            facts: The journal as read under the write lock.
            migration: The migration about to run.

        Returns:
            None

        Raises:
            MigrationError: If another process moved the schema since ``status``.
        """
        current = max((e.version for e in facts.entries if e.applied), default=0)
        if current != migration.version - 1:
            raise MigrationError(
                f"the schema moved to {current} while migrating (another `vakr db migrate`?); "
                "run `vakr db status`",
                status=evaluate(str(self._path), self._family, facts),
            )

    def _failed(
        self, conn: sqlite3.Connection, migration: Migration, exc: sqlite3.Error
    ) -> MigrationError:
        """Clean up after a failed migration and describe the outcome.

        Args:
            conn: The connection the migration ran on.
            migration: The failed migration.
            exc: The driver error; only its name is reported.

        Returns:
            MigrationError: What happened and what to do.

        Raises:
            StorageError: If the rollback or marker cleanup fails.
        """
        code = getattr(exc, "sqlite_errorname", type(exc).__name__)
        label = f"migration {migration.version:04d} {migration.name} failed ({code})"
        rollback(conn)
        if migration.transactional:
            begin(conn, immediate=True)
            with translated():
                journal.clear_started(conn, migration.version)
            commit(conn)
            message = (
                f"{label} and was rolled back; the database is unchanged at schema "
                f"{migration.version - 1}. Keep the database and report this ({DOC_HINT})"
            )
        else:
            message = (
                f"{label} part-way; it is not transactional, so restore the backup taken "
                f"before migrating ({DOC_HINT}). Recovery notes: {migration.recovery}"
            )
        with translated():
            facts = journal.read_facts(conn, self._family.application_id)
        return MigrationError(message, status=evaluate(str(self._path), self._family, facts))
