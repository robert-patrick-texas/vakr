"""``SqliteMigrationManager``: the ``MigrationManager`` port for one SQLite file (5.6).

The manager answers what an operator or the startup preflight asks: the
schema state (``status``), whether the service may start
(``require_current``, 16.3), whether the file is sound
(``check_integrity``), and a verified online ``backup``. ``migrate`` checks
the state first, refusing a newer schema (MIG-004), a mismatched history,
or an interrupted non-transactional migration (MIG-003). Destructive
migrations on a database that already holds a schema need
``allow_destructive`` and a backup at the current version that passes
``verify_backup`` (MIG-005); an empty database has nothing to destroy. The
steps themselves run in ``MigrationRunner``.

Dependencies:
    vakr.adapters.sqlite.connection, errors: Connections and translation.
    vakr.adapters.sqlite.migrations.*: Files, journal, status, backups, runner.
    vakr.ports: The port's types and the clock.

Safety:
    ``status``, ``check_integrity``, and ``backup`` open the database
    read-only and never create it.

Example:
    >>> db = Path("/var/lib/vakr/metadata.db")
    >>> SqliteMigrationManager(db, family).status().state  # doctest: +SKIP
    <SchemaState.CURRENT: 'current'>
"""

import sqlite3
from contextlib import closing
from pathlib import Path

from vakr.adapters.sqlite.connection import (
    DEFAULT_SETTINGS,
    OpenMode,
    SqliteSettings,
    open_connection,
)
from vakr.adapters.sqlite.errors import translated
from vakr.adapters.sqlite.migrations import journal
from vakr.adapters.sqlite.migrations.backup import integrity_problems, online_backup, verify_backup
from vakr.adapters.sqlite.migrations.model import SchemaFamily
from vakr.adapters.sqlite.migrations.runner import MigrationRunner
from vakr.adapters.sqlite.migrations.status import DOC_HINT, evaluate
from vakr.domain.errors import StorageError
from vakr.ports.clock import Clock, SystemClock
from vakr.ports.migrations import (
    AppliedMigration,
    DestructiveMigrationError,
    IntegrityReport,
    MigrationError,
    SchemaRange,
    SchemaState,
    SchemaStatus,
    SchemaTooNewError,
)
from vakr.version import get_version


def refusal(status: SchemaStatus) -> MigrationError:
    """Return the error that refuses to run on ``status``.

    Args:
        status: A status other than ``CURRENT``.

    Returns:
        MigrationError: ``SchemaTooNewError`` for a newer schema (MIG-004),
        otherwise ``MigrationError``; the message is ``status.detail``.

    Raises:
        None
    """
    if status.state is SchemaState.TOO_NEW:
        return SchemaTooNewError(status.detail, status=status)
    return MigrationError(status.detail, status=status)


class SqliteMigrationManager:
    """Inspects, verifies, backs up, and migrates one SQLite database file.

    Attributes:
        path: The database file.
        family: Its schema family.
    """

    def __init__(
        self,
        path: Path,
        family: SchemaFamily,
        *,
        settings: SqliteSettings = DEFAULT_SETTINGS,
        clock: Clock | None = None,
        applied_by: str | None = None,
    ) -> None:
        """Bind the manager to a file.

        Args:
            path: The database file; it need not exist yet.
            family: The migrations this release ships for it.
            settings: Connection timeouts.
            clock: Time source for the journal and backup names.
            applied_by: Release label for the journal; defaults to ``vakr <version>``.

        Returns:
            None

        Raises:
            None
        """
        self.path = path
        self.family = family
        self._settings = settings
        self._clock = clock if clock is not None else SystemClock()
        self._applied_by = applied_by or f"vakr {get_version()}"

    @property
    def supported(self) -> SchemaRange:
        """The range this release supports.

        Args:
            None

        Returns:
            SchemaRange: Upgrades from ``oldest``, runs on ``newest``.

        Raises:
            None
        """
        return self.family.supported

    def status(self) -> SchemaStatus:
        """Read the schema state without creating or changing the file.

        Args:
            None

        Returns:
            SchemaStatus: The state and an actionable detail.

        Raises:
            StorageError: If the file exists but cannot be read.
        """
        if not self.path.exists():
            return evaluate(str(self.path), self.family, None)
        with closing(self._open(OpenMode.READ_ONLY)) as conn, translated():
            facts = journal.read_facts(conn, self.family.application_id)
        return evaluate(str(self.path), self.family, facts)

    def require_current(self) -> SchemaStatus:
        """Startup preflight (16.3): refuse anything but the current schema.

        Args:
            None

        Returns:
            SchemaStatus: The ``CURRENT`` status.

        Raises:
            SchemaTooNewError: If a newer release wrote the schema (MIG-004).
            MigrationError: If it is missing, older, interrupted, or mismatched.
            StorageError: If the file cannot be read.
        """
        status = self.status()
        if status.state is not SchemaState.CURRENT:
            raise refusal(status)
        return status

    def check_integrity(self) -> IntegrityReport:
        """Run ``PRAGMA quick_check`` on the file.

        Args:
            None

        Returns:
            IntegrityReport: Whether it is sound, with the first problems.

        Raises:
            StorageError: If the file is missing or cannot be read.
        """
        if not self.path.exists():
            raise StorageError(f"there is no database at {self.path}", retryable=False)
        with closing(self._open(OpenMode.READ_ONLY)) as conn:
            problems = integrity_problems(conn)
        return IntegrityReport(not problems, problems)

    def backup(self, directory: Path) -> Path:
        """Write a verified online backup into ``directory``.

        Args:
            directory: Destination directory.

        Returns:
            Path: The verified copy.

        Raises:
            StorageError: If the file is missing or the copy fails.
        """
        now = self._clock.now_utc()
        return online_backup(self.path, directory, self.family, now=now, settings=self._settings)

    def migrate(
        self, *, allow_destructive: bool = False, verified_backup: Path | None = None
    ) -> tuple[AppliedMigration, ...]:
        """Apply every pending migration in order, creating the file if missing.

        Args:
            allow_destructive: The operator explicitly allows destructive steps.
            verified_backup: Backup of this database at its current schema.

        Returns:
            tuple[AppliedMigration, ...]: What was applied; empty when current.

        Raises:
            SchemaTooNewError: If a newer release wrote the schema (MIG-004).
            DestructiveMigrationError: If a destructive step lacks confirmation.
            MigrationError: If the schema is mismatched, a non-transactional
                migration was interrupted, or a migration fails.
            StorageError: If the file cannot be read or written.
        """
        status = self.status()
        if status.state is SchemaState.CURRENT:
            return ()
        if not status.can_migrate:
            raise refusal(status)
        self._check_destructive(status, allow_destructive, verified_backup)
        runner = MigrationRunner(self.path, self.family, self._clock, self._applied_by)
        with closing(self._open(OpenMode.CREATE)) as conn:
            return runner.run(conn, (self.family.migrations[m.version - 1] for m in status.pending))

    def _open(self, mode: OpenMode) -> sqlite3.Connection:
        """Open the file.

        Args:
            mode: Read-only, or create for migrations.

        Returns:
            sqlite3.Connection: The connection.

        Raises:
            StorageError: If it cannot be opened.
        """
        return open_connection(self.path, self._settings, mode=mode)

    def _check_destructive(self, status: SchemaStatus, allow: bool, backup: Path | None) -> None:
        """Refuse destructive pending migrations unless confirmed (MIG-005).

        Args:
            status: Current status.
            allow: The operator allowed destructive steps.
            backup: Backup offered as confirmation.

        Returns:
            None

        Raises:
            DestructiveMigrationError: If a destructive step is pending on a
                database with a schema and the confirmation is missing.
        """
        destructive = tuple(m for m in status.pending if m.destructive)
        if not destructive or status.version == 0:
            return
        if allow and backup is not None and verify_backup(backup, self.family, status.version):
            return
        names = ", ".join(f"{m.version:04d} {m.name}" for m in destructive)
        message = (
            f"migration {names} removes or rewrites data; it needs --allow-destructive and a "
            f"verified backup of schema {status.version} (--backup-confirmed); {DOC_HINT}"
        )
        raise DestructiveMigrationError(message, status=status)
