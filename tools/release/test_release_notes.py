# Copyright 2026 Robert Patrick
# SPDX-License-Identifier: Apache-2.0
"""Tests of the release-note layout and the published notes (VER-005..007, VER-T01).

Notes open with a short summary, then the topics New, Changed, Fixed, and
Additional Notes in that order, leaving out empty ones, and name every
held dependency under Additional Notes (TOOLS-004). A release whose
changelog breaks the layout stops in preflight; one that follows it
publishes the section with the lines-of-code and versions tables after it.

Dependencies:
    pytest: Test runner; ``repo``, ``run_git``, ``fake_runner`` fixtures.
    release, release_notes, release_notes_config, release_config: Under test.

Safety:
    Throwaway repositories under ``tmp_path``; ``gh`` and ``git push`` are
    faked, and cloc is faked so the test runs without it.

Example:
    $ uv run pytest tools/release/test_release_notes.py
"""

import shutil
import tomllib
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
import release
from release_config import ConfigError, load_config
from release_notes import held_items, layout_problems
from release_notes_config import NotesConfig, NotesError, parse_notes
from test_release import FAKES, write_config

CFG = NotesConfig(("New", "Changed", "Fixed", "Additional Notes"), held_section="Additional Notes")
HERE = Path(__file__).parent


@pytest.mark.parametrize(
    "notes",
    [
        "One fix.\n\n### Fixed\n\n- It.",
        "Two. Things `vakr` does. Three!\n\n### New\n\n- A.\n\n### Additional Notes\n\n- B.",
    ],
)
def test_ver_t01_valid_layouts_pass(notes: str) -> None:
    """VER-005: a one-topic fix release and a skipped-topic release both pass.

    Args:
        notes: A changelog section body.

    Returns:
        None

    Raises:
        AssertionError: If a problem is reported.
    """
    assert layout_problems(notes, CFG) == []


@pytest.mark.parametrize(
    ("notes", "problem"),
    [
        ("### Fixed\n\n- It.", "summary"),
        ("One. Two. Three. Four.\n\n### Fixed\n\n- It.", "4 found"),
        ("Sum.\n\nMore.\n\n### Fixed\n\n- It.", "summary paragraph"),
        ("Sum.\n\n### Fixed\n\n- A.\n\n### New\n\n- B.", "in that order"),
        ("Sum.\n\n### Added\n\n- A.", "in that order"),
        ("Sum.\n\n### Fixed\n\n- A.\n\n### Fixed\n\n- B.", "each once"),
        ("Sum.\n\nNo topics at all.", "summary paragraph"),
        ("Sum.\n\n### New\n\n### Fixed\n\n- B.", "New is empty"),
    ],
)
def test_ver_t01_layout_problems_are_reported(notes: str, problem: str) -> None:
    """VER-005: missing or long summaries, unknown, unordered, repeated, or empty topics fail.

    Args:
        notes: A changelog section body that breaks the layout.
        problem: Expected part of a reported problem.

    Returns:
        None

    Raises:
        AssertionError: If the problem is not reported.
    """
    assert any(problem in message for message in layout_problems(notes, CFG))


def test_ver_t01_held_dependency_must_be_named_under_additional_notes(tmp_path: Path) -> None:
    """TOOLS-004: each recorded hold is named in Additional Notes, not elsewhere.

    Args:
        tmp_path: Per-test temporary directory.

    Returns:
        None

    Raises:
        AssertionError: If a missing hold passes or a named one fails.
    """
    path = tmp_path / "holds.toml"
    path.write_text('[[exception]]\nitem = "typescript"\nversion = "7.0.2"\n', encoding="utf-8")
    held = held_items(path)
    assert held == ("typescript",)
    assert held_items(None) == ()
    named = "Sum.\n\n### Fixed\n\n- A.\n\n### Additional Notes\n\n- **TypeScript 7.0.2** held."
    assert layout_problems(named, CFG, held) == []
    wrong_place = "Sum.\n\n### Fixed\n\n- TypeScript held."
    assert layout_problems(wrong_place, CFG, held) == [
        "name held dependencies under Additional Notes: typescript"
    ]
    path.write_text("not toml [", encoding="utf-8")
    with pytest.raises(NotesError):
        held_items(path)


@pytest.mark.parametrize(
    "notes",
    [
        {"sections": []},
        {"sections": ["Fixed"], "bogus": 1},
        {"sections": ["Fixed"], "held_file": "x.toml", "held_section": "Other"},
        {"sections": ["Fixed"], "summary_sentences": 0},
        {"sections": ["Fixed"], "versions": [{"name": "A", "file": "f"}]},
        {
            "sections": ["Fixed"],
            "versions": [{"name": "A", "file": "f", "regex": "x", "lock": "l"}],
        },
        {"sections": ["Fixed"], "versions": [{"name": "A", "file": "f", "regex": "("}]},
        {"sections": ["Fixed"], "versions": [{"name": "A", "lock": "l", "package": "p"}] * 2},
    ],
)
def test_ver_t01_bad_notes_config_is_rejected(notes: dict[str, Any]) -> None:
    """A typo in ``[notes]`` stops the release before anything is built.

    Args:
        notes: An invalid ``[notes]`` table.

    Returns:
        None

    Raises:
        AssertionError: If it is accepted.
    """
    with pytest.raises(NotesError):
        parse_notes(notes, Path("/"))


def test_ver_t01_vakr_config_enforces_the_owner_layout() -> None:
    """Vakr's release.toml: the four topics in order, cloc required, lockfiles excluded.

    Args:
        None

    Returns:
        None

    Raises:
        AssertionError: If the configuration drifts from the owner's rule.
    """
    data = tomllib.loads((HERE / "release.toml").read_text(encoding="utf-8"))
    notes = parse_notes(data["notes"], HERE)
    assert notes.sections == ("New", "Changed", "Fixed", "Additional Notes")
    assert notes.summary_sentences == 3
    assert notes.held_section == "Additional Notes"
    assert "cloc" in data["build_tools"]
    assert {"uv.lock", "web/package-lock.json"} <= set(notes.loc_exclude)
    assert notes.versions[0].name.startswith("Python")


def with_notes(config: Path, changelog: str, repo: Path, run_git: Callable[..., str]) -> None:
    """Add a ``[notes]`` table and a changelog to the throwaway repository, and commit.

    Args:
        config: ``release.toml`` written by ``write_config``.
        changelog: Full changelog text.
        repo: Repository root.
        run_git: Helper that runs git in a repository.

    Returns:
        None

    Raises:
        subprocess.CalledProcessError: If committing fails.
    """
    table = '\n[notes]\nsections = ["New", "Fixed"]\n\n[[notes.versions]]\n'
    table += 'name = "Tool"\nfile = "VERSION"\nregex = "(.+)"\n'
    config.write_text(config.read_text("utf-8") + table, "utf-8")
    (repo / "CHANGELOG.md").write_text(changelog, encoding="utf-8")
    run_git(repo, "commit", "-q", "-am", "notes layout")


@pytest.mark.skipif(shutil.which("ssh-keygen") is None, reason="needs ssh-keygen")
def test_ver_t01_published_notes_end_with_both_tables(
    repo: Path, run_git: Callable[..., str], fake_runner: Any, tmp_path: Path
) -> None:
    """VER-005..007: a good layout publishes summary, topics, then the two tables.

    Args:
        repo: Throwaway repository.
        run_git: Helper that runs git in a repository.
        fake_runner: ``FakeRunner`` class from ``conftest.py``.
        tmp_path: Per-test temporary directory.

    Returns:
        None

    Raises:
        AssertionError: If the notes or the preflight outcome are wrong.
    """
    config = write_config(repo, run_git, tmp_path / "absent_key")
    with_notes(config, "## 1.2.3\n\nOne fix.\n\n### Fixed\n\n- It.\n", repo, run_git)
    sums = {"nFiles": 1, "blank": 0, "comment": 0, "code": 3}
    fakes = {**FAKES, ("cloc", "--version"): "2.06", ("cloc",): {"Python": sums, "SUM": sums}}
    assert (
        release.release(
            load_config(config, repo), fake_runner(repo, fakes), dry_run=True, environ={}
        )
        == 1
    )
    notes = (repo / "dist/release/v1.2.3/release-notes.txt").read_text("utf-8")
    assert notes.startswith("One fix.\n\n### Fixed\n\n- It.\n\n### Lines of code\n")
    assert notes.index("### Lines of code") < notes.index("### Versions")
    assert notes.rstrip().endswith("| Component | Version |\n|---|---|\n| Tool | 1.2.3 |")


@pytest.mark.skipif(shutil.which("ssh-keygen") is None, reason="needs ssh-keygen")
def test_ver_t01_bad_layout_stops_a_real_release_before_building(
    repo: Path,
    run_git: Callable[..., str],
    fake_runner: Any,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """VER-005: topics out of order fail preflight; nothing is built or published.

    Args:
        repo: Throwaway repository.
        run_git: Helper that runs git in a repository.
        fake_runner: ``FakeRunner`` class from ``conftest.py``.
        tmp_path: Per-test temporary directory.
        capsys: Captures the tool's report.

    Returns:
        None

    Raises:
        AssertionError: If the release goes ahead.
    """
    config = write_config(repo, run_git, tmp_path / "absent_key")
    with_notes(config, "## 1.2.3\n\nSum.\n\n### Fixed\n\n- A.\n\n### New\n\n- B.\n", repo, run_git)
    fake = fake_runner(repo, FAKES)
    assert release.release(load_config(config, repo), fake, dry_run=False, environ={}) == 1
    assert (
        "[FAIL] release notes layout: use topics New, Fixed, in that order"
        in capsys.readouterr().out
    )
    assert not (repo / "dist").exists()
    bad = config.read_text("utf-8").replace('sections = ["New", "Fixed"]', 'sections = "New"')
    config.write_text(bad, "utf-8")
    with pytest.raises(ConfigError, match="sections"):
        load_config(config, repo)
