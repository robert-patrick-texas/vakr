"""The ``MigrationManager`` port: versioned schemas and startup compatibility (5.2, 5.6, 16.3).

Every database vakr owns has a schema version, and each release publishes
the range it supports (``SchemaRange``): it upgrades from ``oldest`` and runs
only on ``newest``. Startup preflight (``require_current``) refuses anything
else, above all a schema from a newer release, whose changes an older binary
cannot know (MIG-004). ``migrate`` applies pending migrations in order,
detects one that never finished (MIG-003), and runs destructive steps only
with operator confirmation and a verified backup (MIG-005).

Dependencies:
    dataclasses, datetime, enum, pathlib, typing: Read models and the protocol.
    vakr.domain.errors: The base error.

Safety:
    Read models hold versions, names, operator notes, and a database
    location; never row data or SQL text.

Example:
    >>> status = SchemaStatus("metadata.db", SchemaState.PENDING, 1, SchemaRange(0, 2))
    >>> status.supported.newest, status.can_migrate
    (2, True)
"""

from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
from pathlib import Path
from typing import Protocol

from vakr.domain.errors import DomainError


class SchemaState(StrEnum):
    """How a database's schema relates to what this release supports."""

    CURRENT = "current"  # at ``SchemaRange.newest``; the service may start
    PENDING = "pending"  # older; ``migrate`` upgrades it
    MISSING = "missing"  # no database yet (fresh install); ``migrate`` creates it
    TOO_NEW = "too_new"  # written by a newer release (MIG-004)
    INTERRUPTED = "interrupted"  # a migration started and never finished (MIG-003)
    MISMATCH = "mismatch"  # another kind of file, or a history this release did not write


@dataclass(frozen=True, slots=True)
class SchemaRange:
    """The schema versions a release supports (5.6).

    Attributes:
        oldest: Lowest version it upgrades from; 0 is an empty database.
        newest: The version it runs on.
    """

    oldest: int
    newest: int


@dataclass(frozen=True, slots=True)
class MigrationInfo:
    """What an operator needs to know about one migration (5.6).

    Attributes:
        version: Schema version the migration produces.
        name: Short identifier, such as ``initial``.
        description: What it changes.
        destructive: It removes or rewrites data; needs MIG-005 confirmation.
        transactional: It runs in one transaction, so a crash leaves nothing behind.
        recovery: How to recover if it fails part-way.
        data_volume: How its duration grows with the data.
    """

    version: int
    name: str
    description: str
    destructive: bool
    transactional: bool
    recovery: str
    data_volume: str


@dataclass(frozen=True, slots=True)
class AppliedMigration:
    """One completed migration, as the database's journal records it (MIG-002).

    Attributes:
        version: Schema version it produced.
        name: Short identifier.
        applied_at: When it finished (UTC).
        duration: How long it took.
        applied_by: Release that applied it, such as ``vakr 0.2.0``.
    """

    version: int
    name: str
    applied_at: datetime
    duration: timedelta
    applied_by: str


@dataclass(frozen=True, slots=True)
class SchemaStatus:
    """A database's schema state, read without changing anything.

    Attributes:
        location: Where the database lives, for messages (a file path for SQLite).
        state: How the schema relates to ``supported``.
        version: Highest applied migration; 0 when empty or missing.
        supported: This release's range.
        pending: Migrations ``migrate`` would apply, in order.
        applied: The journal's completed migrations, oldest first.
        interrupted: The migration that started and never finished, if any.
        detail: One actionable operator sentence about ``state``.
    """

    location: str
    state: SchemaState
    version: int
    supported: SchemaRange
    pending: tuple[MigrationInfo, ...] = ()
    applied: tuple[AppliedMigration, ...] = ()
    interrupted: MigrationInfo | None = None
    detail: str = ""

    @property
    def can_migrate(self) -> bool:
        """Tell whether ``migrate`` may proceed (it still gates destructive steps).

        Args:
            None

        Returns:
            bool: ``True`` for a missing or older schema, or a transactional
            migration that was interrupted (SQLite rolled it back; it reruns).

        Raises:
            None
        """
        if self.state is SchemaState.INTERRUPTED:
            return self.interrupted is not None and self.interrupted.transactional
        return self.state in {SchemaState.MISSING, SchemaState.PENDING}


@dataclass(frozen=True, slots=True)
class IntegrityReport:
    """The result of a backend integrity check, such as SQLite ``PRAGMA quick_check``.

    Attributes:
        ok: No problem was found.
        problems: The first few problems reported, if any.
    """

    ok: bool
    problems: tuple[str, ...] = ()


class MigrationError(DomainError):
    """The schema is not in a state this operation accepts; the message says what to do.

    Attributes:
        status: The schema status that caused the refusal.
    """

    def __init__(self, message: str, *, status: SchemaStatus) -> None:
        """Create the error.

        Args:
            message: Actionable operator explanation.
            status: The schema status behind it.

        Returns:
            None

        Raises:
            None
        """
        super().__init__(message)
        self.status = status


class SchemaTooNewError(MigrationError):
    """The database was written by a newer release than this one (MIG-004)."""


class DestructiveMigrationError(MigrationError):
    """Pending migrations would destroy data and were not confirmed (MIG-005)."""

    @property
    def destructive(self) -> tuple[MigrationInfo, ...]:
        """The destructive migrations among the pending ones.

        Args:
            None

        Returns:
            tuple[MigrationInfo, ...]: Them, in order.

        Raises:
            None
        """
        return tuple(m for m in self.status.pending if m.destructive)


class MigrationManager(Protocol):
    """Inspects, verifies, backs up, and upgrades one database's schema."""

    @property
    def supported(self) -> SchemaRange:
        """The schema range this release supports.

        Args:
            None

        Returns:
            SchemaRange: ``oldest`` it upgrades from and ``newest`` it runs on.

        Raises:
            None
        """
        ...

    def status(self) -> SchemaStatus:
        """Read the schema state without creating or changing anything.

        Args:
            None

        Returns:
            SchemaStatus: The state, with pending migrations and an actionable detail.

        Raises:
            StorageError: If the database exists but cannot be read.
        """
        ...

    def require_current(self) -> SchemaStatus:
        """Startup preflight (16.3): succeed only on the schema this release runs on.

        Args:
            None

        Returns:
            SchemaStatus: The ``CURRENT`` status.

        Raises:
            SchemaTooNewError: If a newer release wrote the schema (MIG-004).
            MigrationError: If the schema is missing, older, interrupted, or mismatched.
            StorageError: If the database cannot be read.
        """
        ...

    def check_integrity(self) -> IntegrityReport:
        """Run the backend's quick integrity check.

        Args:
            None

        Returns:
            IntegrityReport: Whether the database is sound, with the first problems.

        Raises:
            StorageError: If the database is missing or cannot be read.
        """
        ...

    def backup(self, directory: Path) -> Path:
        """Write a consistent, verified copy while writers keep running.

        Args:
            directory: Where to put the copy; created if missing.

        Returns:
            Path: The verified copy.

        Raises:
            StorageError: If the database is missing or unreadable, or the copy is unsound.
        """
        ...

    def migrate(
        self, *, allow_destructive: bool = False, verified_backup: Path | None = None
    ) -> tuple[AppliedMigration, ...]:
        """Apply every pending migration in order, creating the database if missing.

        Args:
            allow_destructive: The operator explicitly allows destructive steps.
            verified_backup: A backup of this database at its current schema,
                which the manager re-verifies before any destructive step.

        Returns:
            tuple[AppliedMigration, ...]: What was applied; empty when current.

        Raises:
            SchemaTooNewError: If a newer release wrote the schema (MIG-004).
            DestructiveMigrationError: If a destructive step lacks confirmation (MIG-005).
            MigrationError: If the schema is mismatched, a non-transactional
                migration was interrupted (MIG-003), or a migration fails.
            StorageError: If the database cannot be read or written.
        """
        ...
