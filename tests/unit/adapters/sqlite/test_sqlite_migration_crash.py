"""Interrupted and failed migrations: MIG-003, and the 5.6 restart-safety rules.

The crash tests run a real child process that dies with ``os._exit`` inside
the migration, after its statements ran and before its commit, exactly
where a power cut or ``kill -9`` would hurt most. The parent then reopens
the file the way the next service start would.

Dependencies:
    pytest, subprocess; vakr.adapters.sqlite.

Example:
    $ uv run pytest tests/unit/adapters/sqlite/test_sqlite_migration_crash.py
"""

import subprocess
import sys
from pathlib import Path

import pytest

from tests.unit.adapters.sqlite.sqlite_testkit import (
    family_with,
    manager,
    raw_rows,
    synthetic,
)
from vakr.adapters.sqlite.metadata import SqliteMetadataStore
from vakr.ports.migrations import MigrationError, SchemaState

CRASH_EXIT = 17
_CRASH_SCRIPT = """
import os, sys
from pathlib import Path
from vakr.adapters.sqlite.metadata import metadata_family
from vakr.adapters.sqlite.migrations import SchemaFamily, SqliteMigrationManager, parse_migration
from vakr.adapters.sqlite.migrations import journal

def die(*args, **kwargs):
    os._exit(17)  # the process dies inside the migration, before it commits

path, name, text = Path(sys.argv[1]), sys.argv[2], sys.argv[3]
base = metadata_family()
extra = parse_migration(name, text)
family = SchemaFamily(base.name, base.application_id, (*base.migrations, extra))
journal.mark_applied = die
SqliteMigrationManager(path, family).migrate()
"""


def _crash_during(path: Path, transactional: bool) -> None:
    """Migrate to schema 2 in a child process that dies before committing.

    Args:
        path: Database at schema 1.
        transactional: Whether migration 0002 runs in one transaction.

    Returns:
        None

    Raises:
        AssertionError: If the child did not die where intended.
    """
    flag = str(transactional).lower()
    text = (
        f"-- description: Crash test.\n-- destructive: false\n-- transactional: {flag}\n"
        "-- recovery: Restore the backup.\n-- data-volume: Tiny.\n-- test-impact: MIG-003.\n\n"
        "CREATE TABLE crash_probe (x INTEGER) STRICT;\n"
    )
    argv = [sys.executable, "-c", _CRASH_SCRIPT, str(path), "0002_crash_probe.sql", text]
    child = subprocess.run(argv, capture_output=True, check=False, timeout=120)  # noqa: S603
    assert child.returncode == CRASH_EXIT, child.stderr.decode()


def test_mig003_transactional_crash_is_detected_and_resumed(tmp_path: Path) -> None:
    """MIG-003: a crash mid-transaction leaves only the marker; migrate reruns it safely.

    Args:
        tmp_path: Directory for the database.

    Returns:
        None

    Raises:
        None
    """
    path = tmp_path / "metadata.db"
    manager(path).migrate()
    _crash_during(path, transactional=True)
    family = family_with(
        synthetic(2, "crash_probe", "CREATE TABLE crash_probe (x INTEGER) STRICT;")
    )
    status = manager(path, family).status()
    assert (status.state, status.version) == (SchemaState.INTERRUPTED, 1)
    assert status.interrupted is not None
    assert (status.interrupted.version, status.can_migrate) == (2, True)
    assert "safe to retry" in status.detail
    assert raw_rows(path, "SELECT 1 FROM sqlite_schema WHERE name = 'crash_probe'") == []
    with pytest.raises(MigrationError):
        SqliteMetadataStore(path, family=family)  # startup refuses until migrated
    assert [a.version for a in manager(path, family).migrate()] == [2]
    assert manager(path, family).require_current().version == 2
    assert raw_rows(path, "SELECT version, state FROM schema_migrations") == [
        (1, "applied"),
        (2, "applied"),
    ]


def test_mig003_non_transactional_crash_blocks_until_restored(tmp_path: Path) -> None:
    """MIG-003: a crashed non-transactional migration is blocked, never silently rerun.

    Args:
        tmp_path: Directory for the database.

    Returns:
        None

    Raises:
        None
    """
    path = tmp_path / "metadata.db"
    manager(path).migrate()
    _crash_during(path, transactional=False)
    family = family_with(
        synthetic(2, "crash_probe", "CREATE TABLE crash_probe (x INTEGER) STRICT;",
                  transactional=False)
    )  # fmt: skip
    status = manager(path, family).status()
    assert (status.state, status.can_migrate) == (SchemaState.INTERRUPTED, False)
    assert "may be partly applied" in status.detail
    assert "Restore the backup" in status.detail
    with pytest.raises(MigrationError) as caught:
        manager(path, family).migrate()
    assert caught.value.status.state is SchemaState.INTERRUPTED


def test_mig003_non_transactional_failure_part_way_blocks(tmp_path: Path) -> None:
    """MIG-003: a non-transactional migration that fails half-way is reported and blocked.

    Args:
        tmp_path: Directory for the database.

    Returns:
        None

    Raises:
        None
    """
    path = tmp_path / "metadata.db"
    manager(path).migrate()
    half = "CREATE TABLE half_done (x INTEGER);\nCREATE TABLE device (x INTEGER);"
    family = family_with(synthetic(2, "half_done", half, transactional=False))
    with pytest.raises(MigrationError) as caught:
        manager(path, family).migrate()
    assert "part-way" in str(caught.value)
    assert "SQLITE_ERROR" in str(caught.value)
    assert "CREATE TABLE" not in str(caught.value)  # no SQL in operator messages
    assert caught.value.status.state is SchemaState.INTERRUPTED
    assert raw_rows(path, "SELECT 1 FROM sqlite_schema WHERE name = 'half_done'") == [(1,)]
    with pytest.raises(MigrationError):
        manager(path, family).migrate()


def test_failed_transactional_migration_leaves_database_unchanged(tmp_path: Path) -> None:
    """5.6: a transactional migration that fails rolls back fully and clears its marker.

    Args:
        tmp_path: Directory for the database.

    Returns:
        None

    Raises:
        None
    """
    path = tmp_path / "metadata.db"
    manager(path).migrate()
    broken = "CREATE TABLE ok_part (x INTEGER);\nCREATE TABLE device (x INTEGER);"
    family = family_with(synthetic(2, "broken", broken))
    with pytest.raises(MigrationError) as caught:
        manager(path, family).migrate()
    assert "rolled back" in str(caught.value)
    assert "unchanged at schema 1" in str(caught.value)
    status = manager(path, family).status()
    assert (status.state, status.version) == (SchemaState.PENDING, 1)
    assert raw_rows(path, "SELECT 1 FROM sqlite_schema WHERE name = 'ok_part'") == []


def test_schema_moved_by_another_process_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """5.6: a migrate run whose status went stale stops instead of reapplying a step.

    Args:
        tmp_path: Directory for the database.
        monkeypatch: Freezes the first run's status.

    Returns:
        None

    Raises:
        None
    """
    path = tmp_path / "metadata.db"
    manager(path).migrate()
    family = family_with(synthetic(2, "add_probe", "CREATE TABLE probe (x INTEGER);"))
    slow = manager(path, family)
    stale = slow.status()
    manager(path, family).migrate()  # another process gets there first
    monkeypatch.setattr(slow, "status", lambda: stale)
    with pytest.raises(MigrationError) as caught:
        slow.migrate()
    assert "moved to 2" in str(caught.value)
