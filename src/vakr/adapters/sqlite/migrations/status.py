r"""Decide a database's schema state from its journal (MIG-001..004).

``evaluate`` is pure: it compares the facts a file holds with the
migrations this release ships, and returns a ``SchemaStatus`` with one
actionable sentence. Checks run from the most to the least serious:

1. no file: ``MISSING`` (a fresh install);
2. an empty file: ``PENDING`` at version 0;
3. another kind of file: ``MISMATCH``;
4. any journal row above this release's newest version: ``TOO_NEW``, even
   an unfinished one, because a newer release has touched the schema;
   except that a family with read-compatible versioning (E.14) runs as
   ``CURRENT`` on a newer schema whose reader floor (the newest breaking
   migration applied) it supports, when every newer migration finished and
   its own history matches;
5. a history this release did not write (gaps, other checksums): ``MISMATCH``;
6. an ``in_progress`` row: ``INTERRUPTED``;
7. otherwise ``PENDING`` or ``CURRENT``.

Dependencies:
    vakr.adapters.sqlite.migrations.journal, model: Facts and migrations.
    vakr.ports.migrations: The status read model.

Safety:
    Messages name versions, migration names, and the database path only.

Example:
    >>> from vakr.adapters.sqlite.migrations.parse import parse_migration
    >>> header = "-- description: d\n-- destructive: false\n-- recovery: r\n"
    >>> header += "-- data-volume: v\n-- test-impact: t\n\nCREATE TABLE t (x INT);\n"
    >>> family = SchemaFamily("demo", 1, (parse_migration("0001_initial.sql", header),))
    >>> evaluate("demo.db", family, None).state
    <SchemaState.MISSING: 'missing'>
"""

from datetime import timedelta

from vakr.adapters.sqlite.encoding import decode_time
from vakr.adapters.sqlite.migrations.journal import DatabaseFacts, JournalEntry
from vakr.adapters.sqlite.migrations.model import Migration, SchemaFamily
from vakr.ports.migrations import AppliedMigration, SchemaState, SchemaStatus

MIGRATE_HINT = "run `vakr db migrate`"
DOC_HINT = "see docs/operations/database.md"


def evaluate(location: str, family: SchemaFamily, facts: DatabaseFacts | None) -> SchemaStatus:
    """Return the schema state of one database.

    Args:
        location: Database path, for messages.
        family: The migrations this release ships.
        facts: What the file holds, or ``None`` when it does not exist.

    Returns:
        SchemaStatus: State, versions, pending work, and an actionable detail.

    Raises:
        StorageError: If a journal timestamp is not valid.
    """
    if facts is None:
        detail = f"no {family.name} database at {location} yet (fresh install); {MIGRATE_HINT}"
        return _status(location, family, SchemaState.MISSING, 0, detail)
    if not facts.has_journal and facts.object_count == 0 and facts.application_id == 0:
        newest = family.supported.newest
        detail = f"empty {family.name} database; {MIGRATE_HINT} to create schema {newest}"
        return _status(location, family, SchemaState.PENDING, 0, detail)
    if not facts.has_journal or facts.application_id != family.application_id:
        detail = f"{location} is not a vakr {family.name} database; check the path, {DOC_HINT}"
        return _status(location, family, SchemaState.MISMATCH, 0, detail)
    return _from_journal(location, family, facts)


def _from_journal(location: str, family: SchemaFamily, facts: DatabaseFacts) -> SchemaStatus:
    """Decide the state of one of our databases from its journal.

    Args:
        location: Database path, for messages.
        family: The migrations this release ships.
        facts: The file's facts, with journal entries.

    Returns:
        SchemaStatus: ``TOO_NEW``, ``MISMATCH``, ``INTERRUPTED``, ``PENDING``, or ``CURRENT``.

    Raises:
        StorageError: If a journal timestamp is not valid.
    """
    newest = family.supported.newest
    version = max((e.version for e in facts.entries if e.applied), default=0)
    seen = max((e.version for e in facts.entries), default=0)
    if seen > newest:
        return _newer(location, family, facts, version, seen)
    problem = _history_problem(family, facts.entries)
    if problem is not None:
        detail = f"{problem}; this release's migrations did not build this database ({DOC_HINT})"
        return _status(location, family, SchemaState.MISMATCH, version, detail, facts)
    # The history check proved every journalled version is a known migration.
    started = next((family.migration(e.version) for e in facts.entries if not e.applied), None)
    if started is not None:
        return _interrupted(location, family, version, facts, started)
    if version < newest:
        detail = f"schema {version} is older than {newest}; {MIGRATE_HINT}"
        return _status(location, family, SchemaState.PENDING, version, detail, facts)
    detail = f"schema {version} is current"
    return _status(location, family, SchemaState.CURRENT, version, detail, facts)


def _newer(
    location: str, family: SchemaFamily, facts: DatabaseFacts, version: int, seen: int
) -> SchemaStatus:
    """Decide a schema a newer release wrote: read-compatible (E.14) or refused (MIG-004).

    Args:
        location: Database path, for messages.
        family: The migrations this release ships.
        facts: The file's facts, with journal entries above ``newest``.
        version: Highest applied version.
        seen: Highest journalled version, finished or not.

    Returns:
        SchemaStatus: ``CURRENT`` when this release may run on it, ``MISMATCH``
        when its own part of the history differs, else ``TOO_NEW``.

    Raises:
        StorageError: If a journal timestamp is not valid.
    """
    newest, floor = family.supported.newest, facts.floor
    finished = all(e.applied for e in facts.entries)
    versioned = family.compat_from is not None
    if versioned and finished and floor <= newest:
        problem = _history_problem(family, facts.entries)
        if problem is not None:
            detail = (
                f"{problem}; this release's migrations did not build this database ({DOC_HINT})"
            )
            return _status(location, family, SchemaState.MISMATCH, version, detail, facts)
        detail = (
            f"schema {version} (readable by schema {floor} and later) was written by a newer "
            f"vakr release; its later changes are additive, so this release, which runs "
            f"schema {newest}, uses it as it is (E.14)"
        )
        return _status(location, family, SchemaState.CURRENT, version, detail, facts)
    why = f": its migration {floor:04d} is breaking" if versioned and finished else ""
    detail = (
        f"schema {seen} was written by a newer vakr release{why}; this release supports up "
        f"to {newest}. Do not run this release on it: reinstall the newer release, or "
        f"restore the backup taken before the upgrade ({DOC_HINT})"
    )
    return _status(location, family, SchemaState.TOO_NEW, version, detail, facts)


def _history_problem(family: SchemaFamily, entries: tuple[JournalEntry, ...]) -> str | None:
    """Explain how the journal differs from this release's migrations, if it does.

    Args:
        family: The migrations this release ships.
        entries: Journal rows by version; rows above the newest (a newer,
            read-compatible release's) are checked for gaps only.

    Returns:
        str | None: The first difference, or ``None`` when the history matches.

    Raises:
        None
    """
    for position, entry in enumerate(entries, start=1):
        known = family.migration(entry.version)
        if entry.version != position:
            return f"the journal has no migration {position:04d}"
        if known is not None and entry.checksum != known.checksum:
            return f"migration {entry.version:04d} in the database differs from this release's"
        if not entry.applied and position != len(entries):
            return f"migration {entry.version:04d} never finished, yet later ones did"
    return None


def _interrupted(
    location: str, family: SchemaFamily, version: int, facts: DatabaseFacts, migration: Migration
) -> SchemaStatus:
    """Describe a migration that started and never finished (MIG-003).

    Args:
        location: Database path.
        family: The migrations this release ships.
        version: Highest applied version.
        facts: The file's facts.
        migration: The migration whose ``in_progress`` row has no ``applied`` successor.

    Returns:
        SchemaStatus: ``INTERRUPTED``, saying whether a rerun is safe.

    Raises:
        StorageError: If a journal timestamp is not valid.
    """
    label = f"migration {migration.version:04d} {migration.name} started and never finished"
    if migration.transactional:
        detail = f"{label}; SQLite rolled it back, so it is safe to retry: {MIGRATE_HINT}"
    else:
        detail = (
            f"{label} and is not transactional, so it may be partly applied. Restore the "
            f"backup taken before migrating ({DOC_HINT}). Recovery notes: {migration.recovery}"
        )
    status = _status(location, family, SchemaState.INTERRUPTED, version, detail, facts)
    return SchemaStatus(
        status.location, status.state, status.version, status.supported, status.pending,
        status.applied, migration.info(), status.detail,
    )  # fmt: skip


def _status(
    location: str,
    family: SchemaFamily,
    state: SchemaState,
    version: int,
    detail: str,
    facts: DatabaseFacts | None = None,
) -> SchemaStatus:
    """Assemble a status with pending migrations and the applied history.

    Args:
        location: Database path.
        family: The migrations this release ships.
        state: The decided state.
        version: Highest applied version.
        detail: The operator sentence.
        facts: The file's facts, for the applied history.

    Returns:
        SchemaStatus: The status.

    Raises:
        StorageError: If a journal timestamp is not valid.
    """
    pending = () if state is SchemaState.TOO_NEW else family.migrations[version:]
    entries = facts.entries if facts is not None else ()
    applied = tuple(_applied(e) for e in entries if e.applied)
    return SchemaStatus(
        location, state, version, family.supported, tuple(m.info() for m in pending), applied,
        None, detail,
    )  # fmt: skip


def _applied(entry: JournalEntry) -> AppliedMigration:
    """Convert a finished journal row into the port's read model.

    Args:
        entry: A row with ``applied`` set.

    Returns:
        AppliedMigration: Version, name, finish time, duration, and release.

    Raises:
        StorageError: If a timestamp is not valid.
    """
    finished = entry.finished_at if entry.finished_at is not None else entry.started_at
    duration = timedelta(microseconds=max(0, finished - entry.started_at))
    return AppliedMigration(
        entry.version, entry.name, decode_time(finished), duration, entry.applied_by
    )
