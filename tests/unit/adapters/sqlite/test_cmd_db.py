"""``vakr db status|migrate|backup``: output and exit codes (MIG-001..005, 16.3).

Every test relocates the installation with ``VAKR_ROOT``. Later releases
are simulated by pointing the command at a manager with synthetic migrations.

Dependencies:
    pytest; vakr.cli; tests.unit.adapters.sqlite.sqlite_testkit.

Example:
    $ uv run pytest tests/unit/adapters/sqlite/test_cmd_db.py
"""

from pathlib import Path

import pytest

from tests.unit.adapters.sqlite.sqlite_testkit import (
    NEXT,
    family_with,
    manager,
    raw_write,
    synthetic,
)
from vakr.adapters.sqlite.migrations import Migration, SqliteMigrationManager
from vakr.cli import cmd_db
from vakr.cli.main import main
from vakr.ports.migrations import IntegrityReport, SchemaTooNewError

DROP_INDEX = synthetic(NEXT, "drop_site_index", "DROP INDEX device_by_site;", destructive=True)


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


def _use(monkeypatch: pytest.MonkeyPatch, *extra: Migration) -> None:
    """Make ``vakr db`` run as a later release with ``extra`` migrations.

    Args:
        monkeypatch: Replaces the command's manager factory.
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

    monkeypatch.setattr(cmd_db, "metadata_migrations", later)


def test_fresh_install_status_migrate_and_backup(
    state: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """MIG-001: status says missing (1), migrate creates it (0), then status is current (0).

    Args:
        state: State directory.
        capsys: Output capture.

    Returns:
        None

    Raises:
        None
    """
    assert main(["db", "status"]) == 1
    out = capsys.readouterr().out
    assert f"database:  {state / 'metadata.db'}" in out
    assert "state:     missing" in out
    assert "0001 initial" in out
    assert "integrity: not checked" in out
    assert main(["db", "migrate"]) == 0
    out = capsys.readouterr().out
    assert "applied 0001 initial" in out
    assert "backup:" not in out  # nothing to back up on a fresh install
    assert main(["db", "status"]) == 0
    out = capsys.readouterr().out
    assert "state:     current" in out
    assert "pending:   none" in out
    assert "integrity: ok" in out
    assert "0001 initial applied" in out
    assert main(["db", "migrate"]) == 0
    assert "nothing to migrate" in capsys.readouterr().out
    assert main(["db", "backup", "metadata"]) == 0
    copy = Path(capsys.readouterr().out.strip())
    assert copy.parent == state / "backups"
    assert main(["db", "status", "--database", str(copy)]) == 0  # backups can be checked


def test_migrate_backs_up_first_and_gates_destructive_steps(
    state: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """MIG-005: migrate backs up first; a destructive step needs both flags.

    Args:
        state: State directory.
        monkeypatch: Simulates the later release.
        capsys: Output capture.

    Returns:
        None

    Raises:
        None
    """
    assert main(["db", "migrate"]) == 0
    _use(monkeypatch, DROP_INDEX)
    assert main(["db", "status"]) == 1
    assert "[destructive]" in capsys.readouterr().out
    assert main(["db", "migrate"]) == 1
    captured = capsys.readouterr()
    assert "backup: " in captured.out
    assert "--allow-destructive" in captured.err
    assert main(["db", "migrate", "--allow-destructive"]) == 1
    assert main(["db", "migrate", "--allow-destructive", "--backup-confirmed"]) == 0
    assert f"applied {NEXT:04d} drop_site_index" in capsys.readouterr().out
    assert len(list((state / "backups").glob("metadata-*.db"))) == 3


def test_newer_schema_exits_2(state: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """MIG-004: status and migrate exit 2 on a schema from a newer release.

    Args:
        state: State directory.
        capsys: Output capture.

    Returns:
        None

    Raises:
        None
    """
    manager(state / "metadata.db", family_with(DROP_INDEX)).migrate()
    assert main(["db", "status", "metadata"]) == 2
    assert "newer vakr release" in capsys.readouterr().out
    assert main(["db", "migrate"]) == 2  # telemetry migrates; the newer metadata wins the status
    assert "newer vakr release" in capsys.readouterr().err
    assert main(["db", "status"]) == 2


def test_refusal_inside_migrate_exits_2(state: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """MIG-004: a newer schema found by ``migrate`` itself (a race with status) also exits 2.

    Args:
        state: State directory.
        monkeypatch: Makes ``migrate`` find a newer schema.

    Returns:
        None

    Raises:
        None
    """

    def refuse(self: SqliteMigrationManager, **_flags: object) -> tuple[()]:
        """Refuse as ``migrate`` does on a newer schema.

        Args:
            self: The manager.
            **_flags: Ignored ``migrate`` options.

        Returns:
            tuple[()]: Never returns.

        Raises:
            SchemaTooNewError: Always.
        """
        raise SchemaTooNewError(
            f"schema {NEXT} was written by a newer vakr release", status=self.status()
        )

    monkeypatch.setattr(SqliteMigrationManager, "migrate", refuse)
    assert main(["db", "migrate"]) == 2
    assert not (state / "metadata.db").exists()


def test_problems_exit_1(state: Path, monkeypatch: pytest.MonkeyPatch,
                         capsys: pytest.CaptureFixture[str]) -> None:  # fmt: skip
    """Unreadable, foreign, unsound, and failing databases are problems (exit 1).

    Args:
        state: State directory.
        monkeypatch: Injects an integrity failure.
        capsys: Output capture.

    Returns:
        None

    Raises:
        None
    """
    db = state / "metadata.db"
    db.write_bytes(b"not a database" * 100)
    assert main(["db", "status"]) == 1
    assert "cannot read" in capsys.readouterr().err
    assert main(["db", "migrate"]) == 1
    assert main(["db", "backup"]) == 1
    db.unlink()
    raw_write(db, "CREATE TABLE other (x INTEGER)")
    assert main(["db", "migrate"]) == 1
    assert "not a vakr metadata database" in capsys.readouterr().err
    db.unlink()
    assert main(["db", "migrate"]) == 0
    monkeypatch.setattr(
        SqliteMigrationManager, "check_integrity", lambda _self: IntegrityReport(False, ("bad",))
    )
    assert main(["db", "status"]) == 1
    assert "integrity: FAILED (PRAGMA quick_check): bad" in capsys.readouterr().out
    _use(monkeypatch, synthetic(NEXT, "broken", "CREATE TABLE device (x INTEGER);"))
    assert main(["db", "migrate"]) == 1
    assert "rolled back" in capsys.readouterr().err


def test_db_without_subcommand_is_a_usage_error(capsys: pytest.CaptureFixture[str]) -> None:
    """``vakr db`` alone prints help and exits 2.

    Args:
        capsys: Output capture.

    Returns:
        None

    Raises:
        None
    """
    assert main(["db"]) == 2
    assert "status" in capsys.readouterr().err


def test_migrate_names_a_missing_state_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A missing ``/var/lib/vakr`` is named, not reported as ``SQLITE_CANTOPEN``.

    Args:
        tmp_path: An empty relocated root.
        monkeypatch: Sets ``VAKR_ROOT``.
        capsys: Output capture.

    Returns:
        None

    Raises:
        None
    """
    monkeypatch.setenv("VAKR_ROOT", str(tmp_path))
    assert main(["db", "migrate"]) == 1
    err = capsys.readouterr().err
    assert "var/lib/vakr does not exist" in err
    assert "SQLITE_CANTOPEN" not in err
