"""Online backups of a vakr SQLite database, and their verification (MIG-005, 16.5).

``online_backup`` copies a live database with SQLite's online backup API,
which yields a consistent snapshot while services keep reading and
writing. The copy is written as ``<name>.part``, verified, set to mode
0600, and only then renamed into place, so a file named
``metadata-<UTC time>.db`` is always a complete, checked copy.
``verify_backup`` checks a copy before a destructive migration trusts it:
it must open, pass ``PRAGMA quick_check``, carry the family's application
ID, and be at the expected schema version.

Dependencies:
    sqlite3: The online backup API.
    vakr.adapters.sqlite.connection, errors, migrations.journal, model.

Safety:
    Backups go to a directory created at mode 0700. They hold inventory
    and audit history but never credentials (devices name credentials by
    reference only, SEC-001).

Example:
    >>> copy = online_backup(db, backups, family, now=now)  # doctest: +SKIP
    >>> copy.name  # doctest: +SKIP
    'metadata-20261020T031500Z.db'
"""

import sqlite3
from contextlib import closing
from datetime import datetime
from pathlib import Path

from vakr.adapters.sqlite.connection import DEFAULT_SETTINGS, OpenMode, SqliteSettings
from vakr.adapters.sqlite.connection import open_connection as connect
from vakr.adapters.sqlite.errors import translated
from vakr.adapters.sqlite.migrations.journal import read_facts
from vakr.adapters.sqlite.migrations.model import SchemaFamily
from vakr.domain.errors import StorageError

BACKUP_MODE = 0o600
BACKUP_DIR_MODE = 0o700
QUICK_CHECK_LIMIT = 20  # problems listed per check; the first few explain a failure


def online_backup(
    source: Path,
    directory: Path,
    family: SchemaFamily,
    *,
    now: datetime,
    settings: SqliteSettings = DEFAULT_SETTINGS,
) -> Path:
    """Copy a live database into ``directory`` and verify the copy.

    Args:
        source: The live database.
        directory: Destination; created at mode 0700 if missing.
        family: The database's family, for verification.
        now: UTC time for the file name.
        settings: Timeouts for reading the source.

    Returns:
        Path: ``<source stem>-<YYYYmmddTHHMMSSZ>.db``, verified.

    Raises:
        StorageError: If the source is missing or unreadable, or the copy
            cannot be written or fails verification.
    """
    if not source.is_file():
        raise StorageError(f"there is no database at {source} to back up", retryable=False)
    try:
        directory.mkdir(parents=True, exist_ok=True, mode=BACKUP_DIR_MODE)
        target = _unique(directory / f"{source.stem}-{now:%Y%m%dT%H%M%SZ}.db")
    except OSError as exc:
        reason = exc.strerror or type(exc).__name__
        raise StorageError(
            f"cannot write a backup in {directory}: {reason}", retryable=False
        ) from None
    part = target.with_name(f"{target.name}.part")
    try:
        with closing(connect(source, settings, mode=OpenMode.READ_ONLY)) as src:
            version = _schema_version(src, family)
            with translated(), closing(sqlite3.connect(part, autocommit=True)) as dst:
                src.backup(dst)
                # A backup is one self-contained file: no -wal or -shm beside it.
                dst.execute("PRAGMA journal_mode = DELETE")
        if not verify_backup(part, family, version):
            raise StorageError(f"the backup of {source.name} failed verification", retryable=False)
        part.chmod(BACKUP_MODE)
        part.replace(target)
    except OSError as exc:
        reason = exc.strerror or type(exc).__name__
        raise StorageError(
            f"cannot write a backup in {directory}: {reason}", retryable=False
        ) from None
    finally:
        part.unlink(missing_ok=True)
    return target


def verify_backup(path: Path, family: SchemaFamily, version: int) -> bool:
    """Tell whether ``path`` is a sound backup of a ``family`` database at ``version``.

    Args:
        path: Candidate backup file.
        family: Expected family.
        version: Expected schema version.

    Returns:
        bool: ``True`` when it opens, passes ``quick_check``, carries the
        family's ID, and is at ``version``.

    Raises:
        None
    """
    if not path.is_file():
        return False
    try:
        with closing(connect(path, mode=OpenMode.READ_ONLY)) as conn:
            if integrity_problems(conn):
                return False
            return _schema_version(conn, family) == version
    except StorageError:
        return False


def integrity_problems(conn: sqlite3.Connection) -> tuple[str, ...]:
    """Run ``PRAGMA quick_check`` and return its problems; empty means sound.

    Args:
        conn: Open connection.

    Returns:
        tuple[str, ...]: Up to ``QUICK_CHECK_LIMIT`` problem lines; ``()`` when sound.

    Raises:
        StorageError: If the check cannot run.
    """
    with translated():
        rows = [str(r[0]) for r in conn.execute(f"PRAGMA quick_check({QUICK_CHECK_LIMIT})")]
    return () if rows == ["ok"] else tuple(line[:200] for line in rows)


def _schema_version(conn: sqlite3.Connection, family: SchemaFamily) -> int:
    """Return the applied schema version of one of ``family``'s databases.

    Args:
        conn: Open connection.
        family: Expected family.

    Returns:
        int: Highest applied version.

    Raises:
        StorageError: If the file is not a ``family`` database.
    """
    with translated():
        facts = read_facts(conn, family.application_id)
    if facts.application_id != family.application_id or not facts.has_journal:
        raise StorageError(f"not a vakr {family.name} database", retryable=False)
    return max((e.version for e in facts.entries if e.applied), default=0)


def _unique(path: Path) -> Path:
    """Return ``path``, or the first ``-N`` variant that does not exist yet.

    Args:
        path: Preferred name.

    Returns:
        Path: A name no file uses.

    Raises:
        None
    """
    candidate, n = path, 1
    while candidate.exists() or candidate.with_name(f"{candidate.name}.part").exists():
        candidate = path.with_name(f"{path.stem}-{n}{path.suffix}")
        n += 1
    return candidate
