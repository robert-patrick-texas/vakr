"""Unit tests for ``vakr field collect`` (FIELD-001).

Once vakr is installed, ``vakr field collect`` must run the packaged
``vakr-fieldkit.pyz`` with vakr's own interpreter and pass every argument
through untouched, so both ways of running the kit accept the same options.
A missing zipapp must produce a clear error rather than a traceback.

Dependencies:
    pytest: ``monkeypatch`` relocates the install root, ``capsys`` captures output.

Example:
    $ uv run pytest tests/unit/test_cmd_field.py
"""

import json
import sys
from pathlib import Path

import pytest

from vakr.cli import cmd_field
from vakr.cli.main import main

STAND_IN = """\
import json, sys
print(json.dumps({"exe": sys.executable, "argv": sys.argv[1:]}))
sys.exit(7)
"""


def _install_stand_in(path: Path) -> Path:
    """Write a script that reports how it was invoked and exits 7.

    Args:
        path: Where to write it (named like the zipapp).

    Returns:
        Path: The script path.

    Raises:
        OSError: If it cannot be written.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(STAND_IN, encoding="utf-8")
    return path


def test_collect_runs_packaged_zipapp_with_arguments_passed_through(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capfd: pytest.CaptureFixture[str]
) -> None:
    """The packaged kit runs under vakr's interpreter with every option unchanged.

    Args:
        tmp_path: Install root for ``VAKR_ROOT``.
        monkeypatch: Sets ``VAKR_ROOT`` and clears ``VAKR_FIELDKIT``.
        capfd: Captures the child's stdout.

    Returns:
        None

    Raises:
        None
    """
    monkeypatch.setenv("VAKR_ROOT", str(tmp_path))
    monkeypatch.delenv(cmd_field.FIELDKIT_ENV, raising=False)
    _install_stand_in(tmp_path / "opt/vakr/share/fieldkit/vakr-fieldkit.pyz")
    options = ["--profile", "host,snmp", "--devices", "d.txt", "--help", "--", "-x"]
    assert main(["field", "collect", *options]) == 7
    seen = json.loads(capfd.readouterr().out)
    assert seen["exe"] == sys.executable
    assert seen["argv"] == ["collect", *options]


def test_environment_override_wins(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capfd: pytest.CaptureFixture[str]
) -> None:
    """``VAKR_FIELDKIT`` points at a different zipapp, for example a newer release asset.

    Args:
        tmp_path: Directory holding the override.
        monkeypatch: Sets ``VAKR_FIELDKIT``.
        capfd: Captures the child's stdout.

    Returns:
        None

    Raises:
        None
    """
    override = _install_stand_in(tmp_path / "newer" / "vakr-fieldkit.pyz")
    monkeypatch.setenv(cmd_field.FIELDKIT_ENV, str(override))
    assert cmd_field.fieldkit_path() == override
    assert main(["field", "collect"]) == 7
    assert json.loads(capfd.readouterr().out)["argv"] == ["collect"]


def test_missing_zipapp_is_a_clear_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Without the zipapp the command explains where it looked and how to fix it.

    Args:
        tmp_path: Empty install root.
        monkeypatch: Sets ``VAKR_ROOT`` and clears ``VAKR_FIELDKIT``.
        capsys: Captures stderr.

    Returns:
        None

    Raises:
        None
    """
    monkeypatch.setenv("VAKR_ROOT", str(tmp_path))
    monkeypatch.delenv(cmd_field.FIELDKIT_ENV, raising=False)
    assert main(["field", "collect", "--profile", "host"]) == cmd_field.MISSING_EXIT
    err = capsys.readouterr().err
    assert str(tmp_path / "opt/vakr/share/fieldkit/vakr-fieldkit.pyz") in err
    assert cmd_field.FIELDKIT_ENV in err


def test_default_path_follows_install_layout() -> None:
    """The default location is ``<opt_dir>/share/fieldkit/vakr-fieldkit.pyz``.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    path = cmd_field.fieldkit_path({"VAKR_ROOT": "/srv/t"})
    assert path == Path("/srv/t/opt/vakr/share/fieldkit/vakr-fieldkit.pyz")
    assert cmd_field.fieldkit_path({}) == Path("/opt/vakr/share/fieldkit/vakr-fieldkit.pyz")


def test_field_without_subcommand_prints_usage(capsys: pytest.CaptureFixture[str]) -> None:
    """``vakr field`` alone is a usage error that lists ``collect``.

    Args:
        capsys: Captures stderr.

    Returns:
        None

    Raises:
        None
    """
    assert main(["field"]) == 2
    assert "collect" in capsys.readouterr().err
