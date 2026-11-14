# Copyright 2026 Robert Patrick
# SPDX-License-Identifier: Apache-2.0
"""Tests of the release notes' versions table (VER-007, VER-T03).

The table lists what a release really contains: a pin read from a
repository file, a version locked in ``uv.lock`` or ``package-lock.json``,
or a file inside the built ``.deb``. Once a previous release published a
table, the next one shows old and new columns with every change in bold.

Dependencies:
    pytest: Test runner; ``fake_runner`` fixture from ``conftest.py``.
    release_versions, release_notes, release_notes_config: Modules under test.

Safety:
    Files and packages are written under ``tmp_path``; ``gh`` is faked.

Example:
    $ uv run pytest tools/release/test_release_versions.py
"""

import io
import json
import tarfile
from pathlib import Path
from typing import Any

import pytest
from release_notes import previous_release
from release_notes_config import NotesError, parse_notes
from release_version import ReleaseVersion
from release_versions import deb_member, previous_versions, read_versions, render_versions

UV_LOCK = 'version = 1\n\n[[package]]\nname = "fastapi"\nversion = "0.162.0"\n'
NPM_LOCK = {"packages": {"": {}, "node_modules/svelte": {"version": "5.57.3"}}}


def _ar_member(name: str, data: bytes) -> bytes:
    """Return one ``ar`` member: a 60-byte header, the data, and padding.

    Args:
        name: Member name.
        data: Member content.

    Returns:
        bytes: The encoded member.

    Raises:
        None
    """
    header = f"{name + '/':<16}{0:<12}{0:<6}{0:<6}{'100644':<8}{len(data):<10}`\n".encode()
    return header + data + (b"\n" if len(data) % 2 else b"")


def make_deb(path: Path, files: dict[str, str]) -> Path:
    """Write a minimal ``.deb`` whose ``data.tar.zst`` holds ``files``.

    Args:
        path: Package file to create.
        files: Member path (``./opt/...``) to text content.

    Returns:
        Path: ``path``.

    Raises:
        OSError: If the file cannot be written.
    """
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:zst") as tar:
        for name, text in files.items():
            info = tarfile.TarInfo(name)
            info.size = len(text.encode())
            tar.addfile(info, io.BytesIO(text.encode()))
    parts = [_ar_member("debian-binary", b"2.0\n"), _ar_member("control.tar.gz", b"x")]
    path.write_bytes(b"!<arch>\n" + b"".join(parts) + _ar_member("data.tar.zst", buffer.getvalue()))
    return path


def items(*rows: dict[str, Any]) -> Any:
    """Validate ``[[notes.versions]]`` rows through the real parser.

    Args:
        *rows: Item tables.

    Returns:
        Any: The parsed ``VersionItem`` tuple.

    Raises:
        NotesError: If a row is invalid.
    """
    return parse_notes({"sections": ["Fixed"], "versions": list(rows)}, Path("/")).versions


def test_ver_t03_reads_file_lock_and_deb_sources(tmp_path: Path) -> None:
    """VER-007: a pin, a uv and an npm locked version, and a .deb member are read.

    Args:
        tmp_path: Per-test temporary directory.

    Returns:
        None

    Raises:
        AssertionError: If a version is wrong.
    """
    (tmp_path / "uv.lock").write_text(UV_LOCK, encoding="utf-8")
    (tmp_path / "package-lock.json").write_text(json.dumps(NPM_LOCK), encoding="utf-8")
    (tmp_path / "Dockerfile").write_text("FROM golang:1.27.3@sha256:ab AS go\n", encoding="utf-8")
    (tmp_path / "dist").mkdir()
    sysconfig = "./opt/p/python/lib/python3.15/_sysconfig_vars_x.json"
    make_deb(tmp_path / "dist/pkg_1.2.3_amd64.deb", {sysconfig: '{"py_version": "3.15.2"}'})
    rows = items(
        {
            "name": "Python",
            "deb": "dist/pkg_{v}_amd64.deb",
            "member": "./opt/*/python3.*/_sysconfig_vars_*.json",
            "regex": '"py_version": "([^"]+)"',
        },
        {"name": "FastAPI", "lock": "uv.lock", "package": "fastapi"},
        {"name": "Svelte", "lock": "package-lock.json", "package": "svelte"},
        {"name": "Go", "file": "Dockerfile", "regex": r"golang:(\d+\.\d+\.\d+)@"},
    )
    got = read_versions(rows, tmp_path, lambda p: p.replace("{v}", "1.2.3"))
    assert got == [
        ("Python", "3.15.2"),
        ("FastAPI", "0.162.0"),
        ("Svelte", "5.57.3"),
        ("Go", "1.27.3"),
    ]


@pytest.mark.parametrize(
    ("row", "message"),
    [
        ({"name": "X", "lock": "uv.lock", "package": "absent"}, "0 locked versions"),
        ({"name": "X", "file": "uv.lock", "regex": "nomatch"}, "pattern not found"),
        ({"name": "X", "file": "missing.txt", "regex": "x"}, "cannot read"),
        ({"name": "X", "deb": "dist/*.deb", "member": "x", "regex": "x"}, "matches 0 files"),
    ],
)
def test_ver_t03_missing_version_stops_with_reason(
    tmp_path: Path, row: dict[str, Any], message: str
) -> None:
    """VER-007: a source that holds no version is an error, never an empty cell.

    Args:
        tmp_path: Per-test temporary directory.
        row: A row whose source cannot give a version.
        message: Expected part of the error.

    Returns:
        None

    Raises:
        AssertionError: If no error, or the wrong one, is raised.
    """
    (tmp_path / "uv.lock").write_text(UV_LOCK, encoding="utf-8")
    with pytest.raises(NotesError, match=message):
        read_versions(items(row), tmp_path, lambda p: p)


def test_ver_t03_deb_member_rejects_non_packages(tmp_path: Path) -> None:
    """A file that is not an ar archive, or a member that is absent, is an error.

    Args:
        tmp_path: Per-test temporary directory.

    Returns:
        None

    Raises:
        AssertionError: If either is accepted.
    """
    (tmp_path / "x.deb").write_bytes(b"not a deb")
    with pytest.raises(NotesError, match="not an ar archive"):
        deb_member(tmp_path / "x.deb", "*")
    with pytest.raises(NotesError, match="no member matching"):
        deb_member(make_deb(tmp_path / "y.deb", {"./a": "1"}), "./b")


def test_ver_t03_first_table_has_one_column_then_old_and_new_with_bold_changes() -> None:
    """VER-007: one column first; then old and new, bold for changed, added, and removed rows.

    Args:
        None

    Returns:
        None

    Raises:
        AssertionError: If a cell or heading is wrong.
    """
    first = render_versions([("Python", "3.15.2"), ("uv", "0.13.6")], None)
    assert first[2:] == [
        "| Component | Version |",
        "|---|---|",
        "| Python | 3.15.2 |",
        "| uv | 0.13.6 |",
    ]
    old = previous_versions("Summary.\n\n" + "\n".join(first) + "\n\nTrailing text.\n")
    assert old == {"Python": "3.15.2", "uv": "0.13.6"}
    rows = [("Python", "3.15.3"), ("uv", "0.13.6"), ("Go", "1.27.3")]
    assert render_versions(rows, ("1.2.3", {**old, "Gone": "1"}), "1.2.4")[2:] == [
        "| Component | Old (1.2.3) | New (1.2.4) |",
        "|---|---|---|",
        "| Python | 3.15.2 | **3.15.3** |",
        "| uv | 0.13.6 | 0.13.6 |",
        "| Go | — | **1.27.3** |",
        "| Gone | 1 | **removed** |",
    ]
    two = render_versions(rows, ("1.2.3", old), "1.2.4")
    assert previous_versions("\n".join(two)) == dict(rows), "bold is stripped from the new column"
    assert previous_versions("### Fixed\n\n| a | b |\n") == {}, (
        "a table elsewhere is not the versions table"
    )


def test_ver_t03_previous_release_is_the_newest_older_one(tmp_path: Path, fake_runner: Any) -> None:
    """The old column comes from the newest published release below this version.

    Args:
        tmp_path: Per-test temporary directory.
        fake_runner: ``FakeRunner`` class from ``conftest.py``.

    Returns:
        None

    Raises:
        AssertionError: If another release is chosen.
    """
    tags = [{"tagName": t} for t in ("v1.3.0", "v1.2.3", "v1.2.4-rc.1", "v1.2.2", "nightly")]
    fake = fake_runner(
        tmp_path,
        {("gh", "release", "list"): tags, ("gh", "release", "view"): {"body": "notes"}},
    )
    assert previous_release(fake, "o/n", ReleaseVersion.parse("1.2.4")) == ("1.2.4-rc.1", "notes")
    assert fake.calls[-1][:4] == ["gh", "release", "view", "v1.2.4-rc.1"]
    assert (
        previous_release(
            fake_runner(tmp_path, {("gh", "release"): ""}), "o/n", ReleaseVersion.parse("1.0.0")
        )
        is None
    )
    broken = fake_runner(tmp_path, {("gh", "release", "list"): "not json"})
    with pytest.raises(NotesError, match="cannot list releases"):
        previous_release(broken, "o/n", ReleaseVersion.parse("1.0.0"))
