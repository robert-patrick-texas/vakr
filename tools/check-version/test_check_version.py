# Copyright 2026 Robert Patrick
# SPDX-License-Identifier: Apache-2.0
"""Tests for check-version: hard-coded copies, allowlists, and other packages' pins (VER-001).

The project's version lives in one file. A copy anywhere else fails the
gate, but a dependency pinned at the same number does not: when vakr's
next patch version equalled the ``mdit-py-plugins`` pin in ``pyproject.toml``
and ``tools/md2html``, those pins named that package's release, not vakr's.

Dependencies:
    pytest: Test runner; ``tmp_path`` holds the files.
    check_version: Module under test, on the pytest path (pyproject.toml).

Safety:
    Tests only write beneath ``tmp_path``.

Example:
    $ uv run pytest tools/check-version
"""

from pathlib import Path

from check_version import find_copies

VERSION = "1.4.0"


def _write(tmp_path: Path, name: str, text: str) -> Path:
    """Write one file for the check to read.

    Args:
        tmp_path: The test directory.
        name: The file name.
        text: Its contents.

    Returns:
        Path: The file.

    Raises:
        None
    """
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return path


def test_ver001_a_copy_is_found_and_an_allowed_file_is_skipped(tmp_path: Path) -> None:
    """VER-001: a pasted version fails unless its file is allowlisted.

    Args:
        tmp_path: The test directory.

    Returns:
        None

    Raises:
        None
    """
    readme = _write(tmp_path, "README.md", "Install vakr 1.4.0 from the release.\n")
    notes = _write(tmp_path, "CHANGELOG.md", "## 1.4.0\n")
    found = find_copies(VERSION, [readme, notes], [notes.as_posix()], "vakr")
    assert found == [f"{readme.as_posix()}:1"]


def test_ver001_the_version_inside_a_longer_one_is_not_a_copy(tmp_path: Path) -> None:
    """VER-001: ``11.4.0`` and ``1.4.01`` do not contain ``1.4.0``.

    Args:
        tmp_path: The test directory.

    Returns:
        None

    Raises:
        None
    """
    path = _write(tmp_path, "notes.txt", "runs on 11.4.0 and 1.4.01\n")
    assert find_copies(VERSION, [path], [], "vakr") == []


def test_ver001_another_packages_pin_is_not_a_copy(tmp_path: Path) -> None:
    """VER-001: ``other==1.4.0``, ``other>=1.4.0``, and ``--with other==1.4.0`` are skipped.

    Args:
        tmp_path: The test directory.

    Returns:
        None

    Raises:
        None
    """
    path = _write(
        tmp_path,
        "pyproject.toml",
        '"mdit-py-plugins>=1.4.0",\n# "mdit-py-plugins==1.4.0",\n'
        "uv run --with mdit-py-plugins==1.4.0 pytest\nfoo ~= 1.4.0\n",
    )
    assert find_copies(VERSION, [path], [], "vakr") == []


def test_ver001_the_projects_own_pin_and_markup_still_count(tmp_path: Path) -> None:
    """VER-001: ``vakr==1.4.0``, ``<td>1.4.0``, and a pin followed by a bare copy fail.

    Args:
        tmp_path: The test directory.

    Returns:
        None

    Raises:
        None
    """
    path = _write(
        tmp_path,
        "docs.md",
        "pip install vakr==1.4.0\n<td>1.4.0</td>\nother==1.4.0 but vakr 1.4.0\nVAKR>=1.4.0\n",
    )
    found = find_copies(VERSION, [path], [], "vakr")
    assert found == [f"{path.as_posix()}:{n}" for n in (1, 2, 3, 4)]
