"""``vakr db`` on the telemetry database, and on both databases together (MIG-001..005).

The package's postinst runs ``vakr db migrate`` with no database named, so
these tests pin that the bare command migrates both files, that each
subcommand can name one database, and that a telemetry backup is
recognised by ``--database`` without naming its kind.

Dependencies:
    pytest; vakr.cli; telemetry_testkit.

Example:
    $ uv run pytest tests/unit/adapters/sqlite/telemetry/test_cmd_db_telemetry.py
"""

import re
from pathlib import Path

import pytest

from tests.unit.adapters.sqlite.telemetry.telemetry_testkit import raw
from vakr.cli.main import main

POSTINST = Path(__file__).resolve().parents[5] / "packaging/scripts/deb/postinst"


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


def test_postinst_migrate_creates_both_databases(
    state: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """MIG-001, PKG-003: postinst's bare ``vakr db migrate`` creates and migrates both files.

    Args:
        state: State directory.
        capsys: Output capture.

    Returns:
        None

    Raises:
        None
    """
    calls = re.findall(r"bin/vakr db migrate[^\n]*", POSTINST.read_text(encoding="utf-8"))
    assert calls == ["bin/vakr db migrate || status=$?"]  # no database named: both
    assert main(["db", "migrate"]) == 0
    out = capsys.readouterr().out
    assert out.count("applied 0001 initial") == 2
    assert {p.name for p in state.glob("*.db")} == {"metadata.db", "telemetry.db"}
    assert raw(state / "telemetry.db", "PRAGMA application_id") == [(0x766B746C,)]
    assert main(["db", "status"]) == 0
    out = capsys.readouterr().out
    assert f"database:  {state / 'metadata.db'}" in out
    assert f"database:  {state / 'telemetry.db'}" in out
    assert "staging:   oldest staged row 0 s old" in out
    assert main(["db", "migrate"]) == 0
    assert capsys.readouterr().out.count("nothing to migrate") == 2


def test_one_database_by_name_and_backups_recognised(
    state: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """MIG-001, 16.5: name one database; a telemetry backup is checked as telemetry.

    Args:
        state: State directory.
        capsys: Output capture.

    Returns:
        None

    Raises:
        None
    """
    assert main(["db", "migrate", "telemetry"]) == 0
    assert not (state / "metadata.db").exists()
    assert main(["db", "status"]) == 1  # metadata is missing
    capsys.readouterr()
    assert main(["db", "status", "telemetry"]) == 0
    out = capsys.readouterr().out
    assert "metadata.db" not in out
    assert "fixed tables; --full checks all" in out
    assert main(["db", "status", "telemetry", "--full"]) == 0
    assert "integrity: ok (PRAGMA quick_check)" in capsys.readouterr().out
    assert main(["db", "backup", "telemetry"]) == 0
    copy = Path(capsys.readouterr().out.strip())
    assert copy.parent == state / "backups"
    assert copy.name.startswith("telemetry-")
    assert main(["db", "status", "--database", str(copy)]) == 0
    assert "fixed tables" in capsys.readouterr().out  # recognised as a telemetry database
    assert main(["db", "backup"]) == 1  # metadata has nothing to back up yet
