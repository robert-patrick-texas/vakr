"""CLI: ``vakr inventory import`` and ``export`` exit codes, output, and round trip (CFG-010).

Runs the real commands against a relocated install (``VAKR_ROOT``) with a
SQLite metadata database: the dry run changes nothing, ``--apply`` writes
once, an export re-imports and re-plans as no changes, and a peer refuses
``--apply`` (MPOS-003). The credentials file holds a community generated
at test time, and no output may contain it (CRED-002).

Dependencies:
    pytest; vakr.cli.main; the SQLite metadata adapter.

Example:
    $ uv run pytest tests/unit/inventory/test_inventory_bulk_cli.py
"""

import json
import secrets
from pathlib import Path

import pytest

from tests.unit.inventory.inventory_fixtures import write_inventory
from vakr.adapters.sqlite.metadata import METADATA_FILE, metadata_migrations
from vakr.cli.main import main

COMMUNITY = f"fake-{secrets.token_hex(8)}"
"""Generated when the tests run, never stored; no output may contain it (CRED-002)."""

DEVICES = b"name,address,credential,tags\ncore2,192.0.2.30,network-v2c,core\n"


@pytest.fixture
def root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Relocate the install with a credentials file and a minimal configuration.

    Args:
        tmp_path: Install root.
        monkeypatch: Sets ``VAKR_ROOT``.

    Returns:
        Path: The install root.

    Raises:
        None
    """
    monkeypatch.setenv("VAKR_ROOT", str(tmp_path))
    secrets_dir = tmp_path / "etc/vakr/secrets"
    secrets_dir.mkdir(parents=True, mode=0o750)
    creds = secrets_dir / "credentials.toml"
    creds.write_text(f'[credentials.network-v2c]\nversion = "2c"\ncommunity = "{COMMUNITY}"\n')
    creds.chmod(0o640)
    (tmp_path / "etc/vakr/config.toml").write_text("config_version = 1\n")
    (tmp_path / "var/lib/vakr").mkdir(parents=True)
    return tmp_path


def _run(capsys: pytest.CaptureFixture[str], *argv: str) -> tuple[int, str, str]:
    """Run ``vakr`` and return its exit code, stdout, and stderr.

    Args:
        capsys: Captures output.
        *argv: Arguments.

    Returns:
        tuple[int, str, str]: Exit code, stdout, stderr.

    Raises:
        None
    """
    code = main(list(argv))
    out = capsys.readouterr()
    assert COMMUNITY not in out.out + out.err  # CRED-002: never print a secret
    return code, out.out, out.err


def test_cfg010_import_dry_run_then_apply(root: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """CFG-010, CFG-011, SEC-018: dry run by default; --apply once; JSON for scripts.

    Args:
        root: Install root.
        capsys: Captures output.

    Returns:
        None

    Raises:
        None
    """
    csv = root / "devices.csv"
    csv.write_bytes(DEVICES)
    code, out, _ = _run(capsys, "inventory", "import", str(csv), "--dev")
    assert (code, out.splitlines()[-2:]) == (0, [
        "note: no metadata database yet; planned against nothing",
        "devices.csv: dry run; nothing changed (apply with --apply)"])  # fmt: skip
    assert _run(capsys, "inventory", "import", str(csv), "--dev", "--apply")[0] == 4
    metadata_migrations(root / "var/lib/vakr" / METADATA_FILE).migrate()
    code, out, _ = _run(capsys, "inventory", "import", str(csv), "--dev")
    assert code == 0
    assert out.splitlines() == [
        "note: an import writes API-managed records; new ones are api:import (E.38)",
        "devices.csv: 1 create", "  create device core2: 192.0.2.30",
        "devices.csv: dry run; nothing changed (apply with --apply)"]  # fmt: skip
    code, out, _ = _run(capsys, "inventory", "import", str(csv), "--dev", "--apply", "--json")
    data = json.loads(out)
    assert (code, data["applied"], data["summary"]) == (0, True, "1 create")
    code, out, _ = _run(capsys, "inventory", "import", str(csv), "--dev", "--apply")
    assert (code, out.splitlines()[-1]) == (0, "devices.csv: nothing to apply")
    code, out, _ = _run(capsys, "inventory", "list")
    assert "api:import" in out


def test_import_refusals_exit_1_with_located_messages(
    root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """11.4, CONF-008, MPOS-003: invalid and missing files, and a peer's --apply, exit 1.

    Args:
        root: Install root.
        capsys: Captures output.

    Returns:
        None

    Raises:
        None
    """
    bad = root / "bad.csv"
    bad.write_bytes(b"name,address\nr1,169.254.169.254\n")
    code, _, err = _run(capsys, "inventory", "import", str(bad), "--dev")
    assert code == 1
    assert err.splitlines() == [
        "bad.csv: row 2, column address: 169.254.169.254 is denied by "
        "security.denied_target_addresses (169.254.169.254/32) (15.5)",
        "bad.csv: invalid",
    ]
    code, _, err = _run(capsys, "inventory", "import", str(root / "missing.csv"))
    assert (code, err.splitlines()[-1]) == (1, "missing.csv: invalid")
    metadata_migrations(root / "var/lib/vakr" / METADATA_FILE).migrate()
    with (root / "etc/vakr/config.toml").open("a") as handle:
        handle.write('[node]\nrole = "peer"\nposition = "west"\n'
                     'primary_url = "https://vakr-east.example.net"\n')  # fmt: skip
    csv = root / "devices.csv"
    csv.write_bytes(DEVICES)
    assert _run(capsys, "inventory", "import", str(csv), "--dev")[0] == 0  # a dry run is fine
    code, _, err = _run(capsys, "inventory", "import", str(csv), "--dev", "--apply")
    assert code == 1
    assert "vakr-east.example.net" in err


def test_meta010_export_reimports_and_replans_as_no_changes(
    root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """META-010, CFG-011: export TOML and CSV; both plan as no changes; bounds on usage.

    Args:
        root: Install root.
        capsys: Captures output.

    Returns:
        None

    Raises:
        None
    """
    assert _run(capsys, "inventory", "export")[0] == 4  # no metadata database yet
    write_inventory(root / "etc/vakr")
    metadata_migrations(root / "var/lib/vakr" / METADATA_FILE).migrate()
    assert _run(capsys, "inventory", "apply", "--dev")[0] == 0
    csv = root / "devices.csv"
    csv.write_bytes(DEVICES)
    assert _run(capsys, "inventory", "import", str(csv), "--dev", "--apply")[0] == 0
    code, everything, _ = _run(capsys, "inventory", "export")
    assert code == 0
    assert everything == _run(capsys, "inventory", "export")[1]  # byte-identical
    (root / "all.toml").write_text(everything)
    code, out, _ = _run(capsys, "inventory", "import", str(root / "all.toml"), "--dev")
    assert (code, out.splitlines()[0]) == (0, "all.toml: no changes")
    file_only = _run(capsys, "inventory", "export", "--owner", "file")[1]
    (root / "file.toml").write_text(file_only)
    code, out, _ = _run(capsys, "inventory", "plan", "--file", str(root / "file.toml"), "--dev")
    assert (code, out.splitlines()) == (0, ["file.toml: no changes"])
    assert _run(capsys, "inventory", "export", "--format", "csv")[0] == 2
    code, rows, _ = _run(capsys, "inventory", "export", "--format", "csv", "--kind", "devices")
    assert (code, len(rows.splitlines())) == (0, 4)
    (root / "devices-export.csv").write_text(rows)
    code, out, _ = _run(capsys, "inventory", "import", str(root / "devices-export.csv"), "--dev")
    assert (code, out.splitlines()[0]) == (0, "devices-export.csv: no changes")
