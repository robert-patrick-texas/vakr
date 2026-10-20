"""Tests for line scanning, atomic writes, deployment state, values, and the import boundary.

Covers CFGM-006 (file:line locations), CFGM-002/CFGM-003 (atomic writes and
backups), CFGM-004 (previous-release tracking), and blueprint section 29
(portable ``vakr.lifecycle`` code imports nothing else from vakr).

Dependencies:
    pytest: Temporary directories.
    ast: Import-boundary scan.

Example:
    $ uv run pytest tests/unit/config/test_support.py
"""

import ast
import json
import secrets
import stat
from datetime import UTC, datetime
from pathlib import Path

import pytest

from vakr.lifecycle.configmerge import (
    DeploymentState,
    fileio,
    parse_duration,
    read_state,
    record_version,
    redact,
)
from vakr.lifecycle.configmerge.fileio import atomic_write, commit, write_backup
from vakr.lifecycle.configmerge.lines import key_lines, line_of, table_insert_index
from vakr.lifecycle.configmerge.values import ValueSpec

LIB = Path(__file__).resolve().parents[3] / "src/vakr/lifecycle"


def test_key_lines_handles_headers_quotes_arrays_and_strings() -> None:
    """Keys after multi-line arrays and strings, quoted keys, and AoT elements get correct lines.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    text = (
        'top = 1\n[a]\nlist = [\n  "x = 1",\n  [2],\n]\nafter = 2\n'
        'doc = """\nfake = 3\n"""\n"quoted.key" = 4\nb.c = 5  # [comment\n'
        "[[arr]]\nf = 1\n[[arr]]\nf = 2\n[ 'q t' . s ]\nz = 1\n"
    )
    lines = key_lines(text)
    assert (lines["top"], lines["a.list"], lines["a.after"]) == (1, 3, 7)
    assert "a.fake" not in lines
    assert (lines["a.quoted.key"], lines["a.b.c"]) == (11, 12)
    assert (lines["arr[0].f"], lines["arr[1].f"], lines["arr"]) == (14, 16, 13)
    assert lines["q t.s.z"] == 18
    assert line_of(lines, "arr[1].f.missing") == 16
    assert line_of(lines, "nowhere.at.all") is None


def test_table_insert_index_skips_trailing_blanks_and_next_header_comments() -> None:
    """Annotations go after a table's last setting, not inside the next table's comment block.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    text = "x = 0\n\n[a]\ny = 1\n\n# about b\n[b]\nz = 2\n# tail note\n\n"
    assert table_insert_index(text, "a") == 4
    assert table_insert_index(text, "b") == 9
    assert table_insert_index(text, "") == 1
    assert table_insert_index(text, "c") is None


def test_atomic_write_keeps_mode_and_new_files_are_world_readable(tmp_path: Path) -> None:
    """Replaced files keep their permission bits; new files get 0644.

    Args:
        tmp_path: Temporary directory.

    Returns:
        None

    Raises:
        None
    """
    existing = tmp_path / "config.toml"
    existing.write_text("a = 1\n")
    existing.chmod(0o640)
    atomic_write(existing, b"a = 2\n")
    assert existing.read_bytes() == b"a = 2\n"
    assert stat.S_IMODE(existing.stat().st_mode) == 0o640
    fresh = tmp_path / "new.toml"
    atomic_write(fresh, b"b = 1\n")
    assert stat.S_IMODE(fresh.stat().st_mode) == 0o644
    assert sorted(p.name for p in tmp_path.iterdir()) == ["config.toml", "new.toml"]


def test_backups_never_overwrite_and_commit_rolls_back_new_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Same-second backups get a suffix; a failed commit removes files it created.

    Args:
        tmp_path: Temporary directory.
        monkeypatch: Makes the second write fail.

    Returns:
        None

    Raises:
        None
    """
    base = tmp_path / "etc"
    base.mkdir()
    (base / "config.toml").write_text("a = 1\n")
    now = datetime(2026, 10, 20, tzinfo=UTC)
    first = write_backup(base, [(base / "config.toml", b"a = 1\n")], tmp_path / "bk", now)
    second = write_backup(base, [(Path("/elsewhere/x.toml"), b"x\n")], tmp_path / "bk", now)
    assert (first.name, second.name) == ("20261020T000000Z", "20261020T000000Z-1")
    assert (second / "x.toml").read_bytes() == b"x\n"

    real = fileio.atomic_write
    calls: list[Path] = []

    def flaky(path: Path, data: bytes, like: Path | None = None) -> None:
        """Fail on the second operator-file write.

        Args:
            path: Destination.
            data: Content.
            like: Mode template.

        Returns:
            None

        Raises:
            OSError: On the second call for an operator file.
        """
        if path.parent == base:
            calls.append(path)
            if len(calls) == 2:
                raise OSError("disk full")
        real(path, data, like)

    monkeypatch.setattr(fileio, "atomic_write", flaky)
    changes = [
        (base / "new.toml", None, b"n = 1\n"),
        (base / "config.toml", b"a = 1\n", b"a = 9\n"),
    ]
    with pytest.raises(OSError, match="disk full"):
        commit(base, changes, tmp_path / "bk", now)
    assert not (base / "new.toml").exists()
    assert (base / "config.toml").read_text() == "a = 1\n"


def test_deployment_state_tracks_the_previous_release(tmp_path: Path) -> None:
    """CFGM-004: recording a new release shifts the old one to ``previous``; repeats change nothing.

    Args:
        tmp_path: Temporary directory.

    Returns:
        None

    Raises:
        None
    """
    path = tmp_path / "state/config-state.json"
    assert read_state(path) == DeploymentState()
    assert record_version(path, "1.0.0") == DeploymentState("1.0.0", None)
    assert record_version(path, "1.1.0") == DeploymentState("1.1.0", "1.0.0")
    assert record_version(path, "1.1.0") == DeploymentState("1.1.0", "1.0.0")
    assert json.loads(path.read_text())["previous_version"] == "1.0.0"
    path.write_text("[1, 2]")
    with pytest.raises(TypeError, match="expected a JSON object"):
        read_state(path)
    path.write_text("{oops")
    with pytest.raises(ValueError, match="not valid JSON"):
        read_state(path)
    assert record_version(path, "1.2.0") == DeploymentState("1.2.0", None)
    path.write_text(json.dumps({"current_version": 3, "previous_version": "1.0.0"}))
    assert read_state(path) == DeploymentState(None, "1.0.0")


def test_durations_and_redaction() -> None:
    """Durations parse to seconds; URL credentials are masked for display.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    assert parse_duration("200ms") == 0.2
    assert parse_duration("1h30m") == 5400
    assert parse_duration("2d") == 172800
    for bad in ("", "15", "15 m", "m", 15):
        with pytest.raises(ValueError, match="expected a duration"):
            parse_duration(bad)
    url = ValueSpec("str", text_format="url")
    throwaway = secrets.token_hex(8)  # generated per run; never a real credential
    assert redact(url, f"http://ops:{throwaway}@proxy:3128/x") == "http://***@proxy:3128/x"
    assert redact(url, "http://proxy:3128") == "http://proxy:3128"
    assert redact(url, "mailto@x") == "mailto@x"
    assert redact(url, "http://[bad@x") == "<redacted>"
    plain = f"http://ops:{throwaway}@h"
    assert redact(ValueSpec("str"), plain) == plain


def test_lifecycle_code_imports_nothing_else_from_vakr() -> None:
    """Section 29: ``vakr.lifecycle.configmerge`` stays portable.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    offenders = []
    for path in sorted((LIB / "configmerge").glob("*.py")):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            names = [a.name for a in node.names] if isinstance(node, ast.Import) else []
            if isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                names = [node.module]
            offenders += [
                f"{path.name}: {name}"
                for name in names
                if name.split(".")[0] == "vakr" and not name.startswith("vakr.lifecycle")
            ]
    assert offenders == []
