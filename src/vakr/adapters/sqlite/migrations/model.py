"""Migrations and schema families: what one release ships for one database (5.6).

A ``Migration`` is one parsed SQL file (see ``vakr.adapters.sqlite.migrations.parse``
for the file format); a ``SchemaFamily`` is one kind of vakr database, such
as metadata, with its ``PRAGMA application_id`` and its migrations 1..N in
order. The family publishes the supported schema range (5.6): it upgrades
from an empty database (0) and runs on its newest version.

Dependencies:
    vakr.domain: Checks and errors.
    vakr.ports.migrations: ``MigrationInfo`` and ``SchemaRange``.

Safety:
    Plain data; nothing here touches a database.

Example:
    >>> from vakr.adapters.sqlite.metadata import metadata_family
    >>> metadata_family().supported.oldest
    0
"""

from collections.abc import Sequence
from dataclasses import dataclass

from vakr.domain.checks import require_int
from vakr.domain.errors import ValidationError
from vakr.ports.migrations import MigrationInfo, SchemaRange

_MAX_APPLICATION_ID = 2**31 - 1  # PRAGMA application_id is a signed 32-bit header field


@dataclass(frozen=True, slots=True)
class Migration:
    """One parsed migration file.

    Attributes:
        version: Schema version it produces.
        name: Identifier from the file name.
        description: What it changes.
        destructive: It removes or rewrites data (MIG-005).
        transactional: It runs inside one transaction.
        recovery: Recovery notes for a partial failure.
        data_volume: How its duration grows with the data.
        test_impact: Tests that cover it.
        sql: The statements, without the header.
        checksum: ``sha256:<hex>`` of ``sql``.
    """

    version: int
    name: str
    description: str
    destructive: bool
    transactional: bool
    recovery: str
    data_volume: str
    test_impact: str
    sql: str
    checksum: str

    def info(self) -> MigrationInfo:
        """Return the operator-facing summary.

        Args:
            None

        Returns:
            MigrationInfo: Version, name, notes, and flags.

        Raises:
            None
        """
        return MigrationInfo(
            self.version, self.name, self.description, self.destructive,
            self.transactional, self.recovery, self.data_volume,
        )  # fmt: skip


@dataclass(frozen=True, slots=True)
class SchemaFamily:
    """One kind of vakr SQLite database and its ordered migrations.

    Attributes:
        name: Kind of database, such as ``metadata``, for messages.
        application_id: Value stamped into the file header with
            ``PRAGMA application_id``, which tells vakr's database kinds
            apart from each other and from foreign files.
        migrations: Versions 1..N in order.
    """

    name: str
    application_id: int
    migrations: tuple[Migration, ...]

    def __post_init__(self) -> None:
        """Validate the family.

        Args:
            None

        Returns:
            None

        Raises:
            ValidationError: If the application ID is out of range or the
                versions do not run 1..N.
        """
        require_int(self.application_id, "application_id", minimum=1, maximum=_MAX_APPLICATION_ID)
        require_sequence(self.migrations)

    @property
    def supported(self) -> SchemaRange:
        """The range this release supports: upgrades from empty, runs on the newest.

        Args:
            None

        Returns:
            SchemaRange: ``(0, N)``; every migration since the first is kept.

        Raises:
            None
        """
        return SchemaRange(0, self.migrations[-1].version)

    def migration(self, version: int) -> Migration | None:
        """Return the migration that produces ``version``.

        Args:
            version: Schema version.

        Returns:
            Migration | None: The migration, or ``None`` if this release has none.

        Raises:
            None
        """
        return self.migrations[version - 1] if 1 <= version <= len(self.migrations) else None


def require_sequence(migrations: Sequence[Migration]) -> None:
    """Require versions 1..N in order, with at least one migration.

    Args:
        migrations: Candidate sequence.

    Returns:
        None

    Raises:
        ValidationError: If the sequence is empty, has a gap, or is out of order.
    """
    if not migrations:
        raise ValidationError("a schema family needs at least one migration")
    for expected, migration in enumerate(migrations, start=1):
        if migration.version != expected:
            raise ValidationError(
                f"migration {migration.version:04d} {migration.name} is where version "
                f"{expected:04d} belongs; versions run 1, 2, 3, ... without gaps"
            )
