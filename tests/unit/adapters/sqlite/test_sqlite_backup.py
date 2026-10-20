"""Online backups and migration-runner edge cases (MIG-005, 16.5, 5.6).

Dependencies:
    pytest, sqlite3; vakr.adapters.sqlite.migrations.

Example:
    $ uv run pytest tests/unit/adapters/sqlite/test_sqlite_backup.py
"""

import sqlite3
from pathlib import Path

import pytest

from tests.contract import contract_samples as s
from tests.unit.adapters.sqlite.sqlite_testkit import (
    CURRENT,
    NOW,
    family_with,
    manager,
    migrated_store,
    raw_rows,
    raw_write,
    synthetic,
)
from vakr.adapters.sqlite.metadata import metadata_family, sqlite_uow_factory
from vakr.adapters.sqlite.migrations import backup as backups
from vakr.adapters.sqlite.migrations import journal, online_backup, verify_backup
from vakr.domain.errors import StorageError
from vakr.ports.migrations import SchemaState


def test_backup_is_a_consistent_verified_single_file(tmp_path: Path) -> None:
    """16.5: a backup taken while a writer is open is complete, private, and self-contained.

    Args:
        tmp_path: Directory for the database and backups.

    Returns:
        None

    Raises:
        None
    """
    path = tmp_path / "metadata.db"
    store = migrated_store(path)
    with sqlite_uow_factory(store)() as uow:
        uow.tags.add(s.tag("committed"))
        uow.commit()
        uow.tags.add(s.tag("uncommitted"))  # held open while the backup runs
        copy = manager(path).backup(tmp_path / "backups")
    store.close()
    assert copy.stat().st_mode & 0o777 == 0o600
    assert (tmp_path / "backups").stat().st_mode & 0o777 == 0o700
    assert raw_rows(copy, "SELECT name FROM tag") == [("committed",)]
    assert raw_rows(copy, "PRAGMA journal_mode") == [("delete",)]
    assert not list((tmp_path / "backups").glob("*.part"))
    second = manager(path).backup(tmp_path / "backups")  # same second: a new name, not a clobber
    assert second.name == f"metadata-{NOW:%Y%m%dT%H%M%SZ}-1.db"


def test_backup_failures_are_storage_errors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Missing sources, unwritable targets, and failed verification never leave a copy behind.

    Args:
        tmp_path: Directory for the databases.
        monkeypatch: Forces a verification failure.

    Returns:
        None

    Raises:
        None
    """
    family = metadata_family()
    with pytest.raises(StorageError, match="no database"):
        online_backup(tmp_path / "missing.db", tmp_path, family, now=NOW)
    foreign = tmp_path / "foreign.db"
    raw_write(foreign, "CREATE TABLE other (x INTEGER)")
    with pytest.raises(StorageError, match="not a vakr metadata database"):
        online_backup(foreign, tmp_path / "backups", family, now=NOW)
    path = tmp_path / "metadata.db"
    manager(path).migrate()
    blocker = tmp_path / "not-a-dir"
    blocker.write_text("x", encoding="utf-8")
    with pytest.raises(StorageError, match="cannot write a backup"):
        online_backup(path, blocker, family, now=NOW)
    monkeypatch.setattr(backups, "verify_backup", lambda *_args: False)
    with pytest.raises(StorageError, match="failed verification"):
        online_backup(path, tmp_path / "backups", family, now=NOW)
    assert list((tmp_path / "backups").iterdir()) == []


def test_verify_backup_rejects_unsound_copies(tmp_path: Path) -> None:
    """MIG-005: only a sound copy of this family at the expected version counts.

    Args:
        tmp_path: Directory for the copies.

    Returns:
        None

    Raises:
        None
    """
    family = metadata_family()
    path = tmp_path / "metadata.db"
    manager(path).migrate()
    copy = manager(path).backup(tmp_path / "backups")
    assert verify_backup(copy, family, CURRENT)
    assert not verify_backup(copy, family, CURRENT + 1)
    assert not verify_backup(tmp_path / "absent.db", family, CURRENT)
    page_size = int(raw_rows(copy, "PRAGMA page_size")[0][0])
    (root,) = raw_rows(copy, "SELECT rootpage FROM sqlite_schema WHERE name = 'device_by_site'")[0]
    with copy.open("r+b") as handle:
        handle.seek((int(root) - 1) * page_size)
        handle.write(b"\xff" * page_size)
    assert not verify_backup(copy, family, CURRENT)


def test_non_transactional_migration_runs_outside_a_transaction(tmp_path: Path) -> None:
    """5.6: statements SQLite refuses inside a transaction (VACUUM) still migrate.

    Args:
        tmp_path: Directory for the database.

    Returns:
        None

    Raises:
        None
    """
    path = tmp_path / "metadata.db"
    manager(path).migrate()
    family = family_with(synthetic(CURRENT + 1, "compact", "VACUUM;", transactional=False))
    assert [a.name for a in manager(path, family).migrate()] == ["compact"]
    assert manager(path, family).status().state is SchemaState.CURRENT


def test_journal_failures_roll_back(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A failure while stamping the journal leaves no half-created journal behind.

    Args:
        tmp_path: Directory for the database.
        monkeypatch: Makes the stamp fail.

    Returns:
        None

    Raises:
        None
    """

    def fail(*_args: object) -> None:
        """Fail like a full disk would.

        Args:
            *_args: Ignored.

        Returns:
            None

        Raises:
            sqlite3.OperationalError: Always, with SQLITE_FULL.
        """
        exc = sqlite3.OperationalError("disk full")
        exc.sqlite_errorcode, exc.sqlite_errorname = sqlite3.SQLITE_FULL, "SQLITE_FULL"
        raise exc

    path = tmp_path / "metadata.db"
    monkeypatch.setattr(journal, "stamp", fail)
    with pytest.raises(StorageError, match="full"):
        manager(path).migrate()
    assert raw_rows(path, "SELECT count(*) FROM sqlite_schema") == [(0,)]
    with pytest.raises(StorageError, match="no database"):
        manager(tmp_path / "absent.db").check_integrity()
