"""The ``schema_migrations`` journal: what a database records about its own schema (5.6).

Each migration is journalled twice. First an ``in_progress`` row is
committed on its own, *before* the migration starts; then the row becomes
``applied`` in the same transaction as the migration's statements. A crash
in between therefore leaves exactly one trace, the ``in_progress`` row,
and the next start sees it (MIG-003). The file header's
``PRAGMA application_id`` is stamped in the same transaction that creates
the journal, so a file with a journal but the wrong ID is not ours.

The journal's shape never changes. An older release must be able to read
a newer database's journal in order to refuse it (MIG-004). Read-compatible
versioning (E.14) lives beside it in ``schema_compat``: one row per applied
migration that declared ``compat``. A journalled version without a row is
breaking, so files written before the table existed keep their meaning.

Dependencies:
    sqlite3: Statements on the caller's connection and transaction.
    vakr.adapters.sqlite.migrations.model: ``Migration``.

Safety:
    Only fixed statements with bound parameters; the application ID is a
    validated integer.

Example:
    >>> conn = sqlite3.connect(":memory:", autocommit=True)
    >>> read_facts(conn, 1).has_journal
    False
"""

import sqlite3
from dataclasses import dataclass

from vakr.adapters.sqlite.migrations.model import Compat, Migration

JOURNAL_TABLE = "schema_migrations"
COMPAT_TABLE = "schema_compat"

_CREATE_COMPAT = """
CREATE TABLE IF NOT EXISTS schema_compat (
    version INTEGER NOT NULL PRIMARY KEY CHECK (version >= 1),
    compat  TEXT    NOT NULL CHECK (compat IN ('additive', 'breaking'))
) STRICT
"""

_CREATE = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    version     INTEGER NOT NULL PRIMARY KEY CHECK (version >= 1),
    name        TEXT    NOT NULL,
    checksum    TEXT    NOT NULL,
    state       TEXT    NOT NULL CHECK (state IN ('in_progress', 'applied')),
    started_at  INTEGER NOT NULL,
    finished_at INTEGER,
    applied_by  TEXT    NOT NULL
) STRICT
"""
_SELECT = (
    "SELECT version, name, checksum, state, started_at, finished_at, applied_by "
    "FROM schema_migrations ORDER BY version"
)


@dataclass(frozen=True, slots=True)
class JournalEntry:
    """One journal row.

    Attributes:
        version: Schema version the migration produces.
        name: Migration name when it was journalled.
        checksum: ``sha256:<hex>`` of the SQL that ran.
        applied: ``True`` once it committed; ``False`` while ``in_progress``.
        started_at: Start time, integer microseconds (UTC).
        finished_at: Finish time, or ``None`` while in progress.
        applied_by: Release that ran it.
    """

    version: int
    name: str
    checksum: str
    applied: bool
    started_at: int
    finished_at: int | None
    applied_by: str


@dataclass(frozen=True, slots=True)
class DatabaseFacts:
    """What a database file says about its schema.

    Attributes:
        application_id: ``PRAGMA application_id``; 0 when never stamped.
        object_count: Tables, indexes, views, and triggers besides the journal.
        has_journal: Whether ``schema_migrations`` exists.
        entries: Journal rows by version; empty unless the ID matched.
        additive: Versions ``schema_compat`` records as additive (E.14).
    """

    application_id: int
    object_count: int
    has_journal: bool
    entries: tuple[JournalEntry, ...]
    additive: frozenset[int] = frozenset()

    @property
    def floor(self) -> int:
        """The lowest schema version a release must support to use this file (E.14).

        Args:
            None

        Returns:
            int: The newest applied version that is not additive; 0 for none.

        Raises:
            None
        """
        return max((e.version for e in self.entries if e.applied
                    and e.version not in self.additive), default=0)  # fmt: skip


def read_facts(conn: sqlite3.Connection, application_id: int) -> DatabaseFacts:
    """Read the header ID, the schema objects, and, for our files, the journal.

    Args:
        conn: Open connection; read-only is enough.
        application_id: The family's ID; another file's journal is not read,
            because its shape is unknown.

    Returns:
        DatabaseFacts: The facts.

    Raises:
        sqlite3.Error: If the file cannot be read; the caller translates it.
    """
    found = int(conn.execute("PRAGMA application_id").fetchone()[0])
    names = [r[0] for r in conn.execute("SELECT name FROM sqlite_schema")]
    names = [n for n in names if not n.startswith("sqlite_")]
    has_journal = JOURNAL_TABLE in names
    entries: tuple[JournalEntry, ...] = ()
    additive: frozenset[int] = frozenset()
    if has_journal and found == application_id:
        entries = tuple(
            JournalEntry(row[0], row[1], row[2], row[3] == "applied", row[4], row[5], row[6])
            for row in conn.execute(_SELECT)
        )
        if COMPAT_TABLE in names:
            query = "SELECT version FROM schema_compat WHERE compat = 'additive'"
            additive = frozenset(int(r[0]) for r in conn.execute(query))
    objects = len(names) - has_journal - (COMPAT_TABLE in names)
    return DatabaseFacts(found, objects, has_journal, entries, additive)


def stamp(conn: sqlite3.Connection, application_id: int) -> None:
    """Create the journal and stamp the header ID, inside the caller's transaction.

    Args:
        conn: Connection holding the write lock.
        application_id: The family's ID.

    Returns:
        None

    Raises:
        sqlite3.Error: If either statement fails.
    """
    conn.execute(_CREATE)
    conn.execute(f"PRAGMA application_id = {int(application_id)}")


def mark_started(
    conn: sqlite3.Connection, migration: Migration, *, at_us: int, applied_by: str
) -> None:
    """Record that ``migration`` is starting; the caller commits this alone.

    Args:
        conn: Connection holding the write lock.
        migration: The migration about to run.
        at_us: Start time, integer microseconds.
        applied_by: Release running it.

    Returns:
        None

    Raises:
        sqlite3.Error: If the row cannot be written.
    """
    conn.execute(
        "INSERT OR REPLACE INTO schema_migrations "
        "(version, name, checksum, state, started_at, finished_at, applied_by) "
        "VALUES (?, ?, ?, 'in_progress', ?, NULL, ?)",
        (migration.version, migration.name, migration.checksum, at_us, applied_by),
    )


def mark_applied(conn: sqlite3.Connection, version: int, *, at_us: int) -> None:
    """Record that a migration finished, inside its own transaction.

    Args:
        conn: Connection holding the write lock.
        version: The migration's version.
        at_us: Finish time, integer microseconds.

    Returns:
        None

    Raises:
        sqlite3.Error: If the row cannot be updated.
    """
    conn.execute(
        "UPDATE schema_migrations SET state = 'applied', finished_at = ? "
        "WHERE version = ? AND state = 'in_progress'",
        (at_us, version),
    )


def record_compat(conn: sqlite3.Connection, version: int, compat: Compat) -> None:
    """Record a migration's ``compat``, inside the transaction that marks it applied.

    Args:
        conn: Connection holding the write lock.
        version: The migration's version.
        compat: What its header declared.

    Returns:
        None

    Raises:
        sqlite3.Error: If the table or row cannot be written.
    """
    conn.execute(_CREATE_COMPAT)
    conn.execute(
        "INSERT OR REPLACE INTO schema_compat (version, compat) VALUES (?, ?)",
        (version, compat.value),
    )


def clear_started(conn: sqlite3.Connection, version: int) -> None:
    """Drop the marker of a migration that failed cleanly and was rolled back.

    Args:
        conn: Connection holding the write lock.
        version: The migration's version.

    Returns:
        None

    Raises:
        sqlite3.Error: If the row cannot be removed.
    """
    conn.execute(
        "DELETE FROM schema_migrations WHERE version = ? AND state = 'in_progress'", (version,)
    )
