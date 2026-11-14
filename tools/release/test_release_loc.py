# Copyright 2026 Robert Patrick
# SPDX-License-Identifier: Apache-2.0
"""Tests of the release notes' lines-of-code table (VER-006, VER-T02).

cloc counts only first-party content: the files git tracks, minus the
generated files that record third-party packages. Untracked files (a
virtual environment, ``node_modules``, build output) never reach it.

Dependencies:
    pytest: Test runner; ``repo``, ``run_git``, ``fake_runner`` fixtures.
    release_loc, release_notes_config: Modules under test.
    External: cloc for the real-count test, which is skipped without it.

Safety:
    Everything happens in a throwaway repository under ``tmp_path``.

Example:
    $ uv run pytest tools/release/test_release_loc.py
"""

import shutil
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from release_loc import count_loc, first_party_files, render_loc
from release_notes_config import NotesConfig, NotesError
from release_proc import Runner

EXCLUDE = ("uv.lock", "web/package-lock.json")


def add_sources(repo: Path, run_git: Callable[..., str]) -> None:
    """Commit first-party sources and a lockfile, and leave dependencies untracked.

    Args:
        repo: Throwaway repository.
        run_git: Helper that runs git in a repository.

    Returns:
        None

    Raises:
        subprocess.CalledProcessError: If git fails.
    """
    (repo / "web").mkdir()
    (repo / "app.py").write_text("# why\nx = 1\n\ny = 2\n", encoding="utf-8")
    (repo / "uv.lock").write_text("version = 1\n" * 50, encoding="utf-8")
    (repo / "web/package-lock.json").write_text("{}\n", encoding="utf-8")
    (repo / "node_modules").mkdir()
    (repo / "node_modules/dep.js").write_text("var a = 1;\n" * 99, encoding="utf-8")
    run_git(repo, "add", "app.py", "uv.lock", "web/package-lock.json")
    run_git(repo, "commit", "-q", "-m", "sources")


def test_ver_t02_only_tracked_first_party_files_are_listed(
    repo: Path, run_git: Callable[..., str]
) -> None:
    """VER-006: lockfiles are excluded, and untracked dependencies never appear.

    Args:
        repo: Throwaway repository.
        run_git: Helper that runs git in a repository.

    Returns:
        None

    Raises:
        AssertionError: If a lockfile or dependency is listed.
    """
    add_sources(repo, run_git)
    files = first_party_files(Runner(repo), EXCLUDE)
    assert "app.py" in files
    assert "CHANGELOG.md" in files
    assert not {"uv.lock", "web/package-lock.json", "node_modules/dep.js"} & set(files)


@pytest.mark.skipif(shutil.which("cloc") is None, reason="needs cloc")
def test_ver_t02_real_cloc_counts_first_party_python(
    repo: Path, run_git: Callable[..., str], tmp_path: Path
) -> None:
    """VER-006: cloc counts the committed Python and none of the dependency's lines.

    Args:
        repo: Throwaway repository.
        run_git: Helper that runs git in a repository.
        tmp_path: Private directory for the file list.

    Returns:
        None

    Raises:
        AssertionError: If the counts are wrong.
    """
    add_sources(repo, run_git)
    counts, version, files = count_loc(
        Runner(repo), NotesConfig(("Fixed",), loc_exclude=EXCLUDE), tmp_path
    )
    assert counts["Python"] == {"nFiles": 1, "blank": 1, "comment": 1, "code": 2}
    assert "JavaScript" not in counts
    assert "TOML" not in counts
    assert version
    assert files == len(first_party_files(Runner(repo), EXCLUDE))


def test_ver_t02_cloc_failure_is_an_error(repo: Path, fake_runner: Any, tmp_path: Path) -> None:
    """A cloc that fails or prints no totals stops the notes instead of publishing zero.

    Args:
        repo: Throwaway repository.
        fake_runner: ``FakeRunner`` class from ``conftest.py``.
        tmp_path: Private directory for the file list.

    Returns:
        None

    Raises:
        AssertionError: If a broken count is accepted.
    """
    cfg = NotesConfig(("Fixed",), loc_command=("cloc",))
    with pytest.raises(NotesError, match="no totals"):
        count_loc(fake_runner(repo, {("cloc",): {"header": {}}}), cfg, tmp_path)
    with pytest.raises(NotesError, match="lines of code"):
        count_loc(fake_runner(repo, {("cloc",): "not json"}), cfg, tmp_path)


def test_ver_t02_table_totals_and_statement() -> None:
    """VER-006: languages by code size, a bold total, and the first-party statement.

    Args:
        None

    Returns:
        None

    Raises:
        AssertionError: If the table or statement is wrong.
    """
    counts = {
        "Markdown": {"nFiles": 3, "blank": 10, "comment": 0, "code": 900},
        "Python": {"nFiles": 2, "blank": 5, "comment": 7, "code": 1500},
        "SUM": {"nFiles": 5, "blank": 15, "comment": 7, "code": 2400},
    }
    lines = render_loc(counts, "2.06", 6, EXCLUDE)
    assert lines[4:7] == [
        "| Python | 2 | 5 | 7 | 1,500 |",
        "| Markdown | 3 | 10 | 0 | 900 |",
        "| **Total** | **5** | **15** | **7** | **2,400** |",
    ]
    statement = lines[-1]
    assert "cloc](https://github.com/AlDanial/cloc) 2.06" in statement
    assert "first-party content only" in statement
    assert "No dependencies or external components" in statement
    assert "(`uv.lock`, `web/package-lock.json`) are left out." in statement
