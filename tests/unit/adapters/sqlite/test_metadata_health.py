"""``metadata_db`` self-health check: OK, WARNING, and CRITICAL states (OPS-001, 16.3).

Dependencies:
    pytest; vakr.adapters.sqlite.metadata.health; vakr.health.registry.

Example:
    $ uv run pytest tests/unit/adapters/sqlite/test_metadata_health.py
"""

from pathlib import Path

import pytest

from tests.unit.adapters.sqlite.sqlite_testkit import (
    family_with,
    manager,
    raw_rows,
    raw_write,
    synthetic,
)
from vakr.adapters.sqlite.metadata import health
from vakr.adapters.sqlite.metadata.health import check_metadata_db
from vakr.adapters.sqlite.migrations import Migration, SqliteMigrationManager
from vakr.health.model import Status
from vakr.health.registry import CHECKS

ADD_PROBE = synthetic(2, "add_probe", "CREATE TABLE probe (x INTEGER);")


@pytest.fixture
def state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Relocate the installation and create its state directory.

    Args:
        tmp_path: Install root.
        monkeypatch: Sets ``VAKR_ROOT``.

    Returns:
        Path: The state directory.

    Raises:
        None
    """
    monkeypatch.setenv("VAKR_ROOT", str(tmp_path))
    state_dir = tmp_path / "var/lib/vakr"
    state_dir.mkdir(parents=True)
    return state_dir


def _release_with(monkeypatch: pytest.MonkeyPatch, *extra: Migration) -> None:
    """Make the check run as a later release with ``extra`` migrations.

    Args:
        monkeypatch: Replaces the check's manager factory.
        *extra: Synthetic migrations.

    Returns:
        None

    Raises:
        None
    """
    family = family_with(*extra)

    def later(path: Path) -> SqliteMigrationManager:
        """Return a manager for the later release.

        Args:
            path: Database file.

        Returns:
            SqliteMigrationManager: The manager.

        Raises:
            None
        """
        return manager(path, family)

    monkeypatch.setattr(health, "metadata_migrations", later)


def test_check_is_registered() -> None:
    """OPS-001: ``vakr check health`` and ``vakr doctor`` run the metadata check.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    assert check_metadata_db in CHECKS


def test_host_without_state_dir_is_not_a_node(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A host without ``/var/lib/vakr`` is not an installed node: OK, nothing to check.

    Args:
        tmp_path: Install root without a state directory.
        monkeypatch: Sets ``VAKR_ROOT``.

    Returns:
        None

    Raises:
        None
    """
    monkeypatch.setenv("VAKR_ROOT", str(tmp_path))
    result = check_metadata_db()
    assert (result.name, result.status) == ("metadata_db", Status.OK)
    assert "not an installed vakr node" in result.summary


def test_fresh_install_warns_then_current_is_ok(state: Path) -> None:
    """WARNING before the first migration; OK with perfdata once current.

    Args:
        state: State directory.

    Returns:
        None

    Raises:
        None
    """
    fresh = check_metadata_db()
    assert fresh.status is Status.WARNING
    assert "fresh install" in fresh.summary
    manager(state / "metadata.db").migrate()
    current = check_metadata_db()
    assert current.status is Status.OK
    assert [(p.label, p.value) for p in current.perfdata] == [("metadata_schema", 1.0)]


def test_pending_warns_and_newer_is_critical(state: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Pending migrations warn; a schema from a newer release is CRITICAL (MIG-004).

    Args:
        state: State directory.
        monkeypatch: Simulates releases.

    Returns:
        None

    Raises:
        None
    """
    db = state / "metadata.db"
    manager(db).migrate()
    this_release = health.metadata_migrations
    _release_with(monkeypatch, ADD_PROBE)
    assert check_metadata_db().status is Status.WARNING
    manager(db, family_with(ADD_PROBE)).migrate()
    monkeypatch.setattr(health, "metadata_migrations", this_release)
    newer = check_metadata_db()
    assert newer.status is Status.CRITICAL
    assert "newer vakr release" in newer.summary


@pytest.mark.parametrize(
    ("transactional", "expected"), [(True, Status.WARNING), (False, Status.CRITICAL)]
)
def test_interrupted_migrations(
    state: Path, monkeypatch: pytest.MonkeyPatch, transactional: bool, expected: Status
) -> None:
    """MIG-003: a rerunnable interruption warns; one that needs a restore is CRITICAL.

    Args:
        state: State directory.
        monkeypatch: Simulates the later release.
        transactional: Whether the interrupted migration was transactional.
        expected: Expected severity.

    Returns:
        None

    Raises:
        None
    """
    db = state / "metadata.db"
    manager(db).migrate()
    probe = synthetic(
        2, "add_probe", "CREATE TABLE probe (x INTEGER);", transactional=transactional
    )
    marker = (2, probe.name, probe.checksum, "in_progress", 0, None, "vakr test")
    raw_write(db, "INSERT INTO schema_migrations VALUES (?, ?, ?, ?, ?, ?, ?)", marker)
    _release_with(monkeypatch, probe)
    assert check_metadata_db().status is expected


def test_unreadable_foreign_and_damaged_files_are_critical(state: Path) -> None:
    """A junk file, a foreign database, and a failed quick_check are all CRITICAL.

    Args:
        state: State directory.

    Returns:
        None

    Raises:
        None
    """
    db = state / "metadata.db"
    db.write_bytes(b"not a database" * 100)
    junk = check_metadata_db()
    assert (junk.status, "cannot read" in junk.summary) == (Status.CRITICAL, True)
    db.unlink()
    raw_write(db, "CREATE TABLE other (x INTEGER)")
    assert check_metadata_db().status is Status.CRITICAL
    db.unlink()
    manager(db).migrate()
    page_size = int(raw_rows(db, "PRAGMA page_size")[0][0])
    (root,) = raw_rows(db, "SELECT rootpage FROM sqlite_schema WHERE name = 'device_by_site'")[0]
    with db.open("r+b") as handle:  # overwrite one index page, as a disk fault would
        handle.seek((int(root) - 1) * page_size)
        handle.write(b"\xff" * page_size)
    damaged = check_metadata_db()
    assert damaged.status is Status.CRITICAL
    assert "quick_check" in damaged.summary
