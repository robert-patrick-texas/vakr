# Copyright 2026 Robert Patrick
# SPDX-License-Identifier: Apache-2.0
"""Tests for release notes extraction and the tag/push/release commands (28.7 step 4).

Tagging and publishing cannot be undone, so the commands are checked as
data: the right tag name, the pre-release flag under the MVP rule, notes
taken from the matching changelog section, and nothing executed in a dry
run.

Dependencies:
    pytest: Test runner.
    release_publish, release_config: Modules under test.

Safety:
    A fake runner records calls; nothing is tagged, pushed, or released.

Example:
    $ uv run pytest tools/release/test_release_publish.py
"""

from pathlib import Path
from typing import Any

import pytest
from release_config import EXAMPLE, ReleaseConfig
from release_publish import changelog_notes, publish, publish_commands
from release_version import ReleaseVersion

CHANGES = """# Changelog

## Unreleased

- Not yet.

## [1.2.30] - 2026-11-01

- Wrong section.

## 1.2.3 - 2026-10-20

### Added

- New option.

## 1.2.2

- Older.
"""


@pytest.mark.parametrize(
    ("version", "expected"),
    [
        ("1.2.3", "### Added\n\n- New option."),
        ("1.2.30", "- Wrong section."),
        ("1.2.2", "- Older."),
        ("9.9.9", None),
    ],
)
def test_changelog_notes_take_the_exact_section(version: str, expected: str | None) -> None:
    """VER-004: notes are the version's own section, never a neighbour's.

    Args:
        version: Version to look up.
        expected: Expected notes, or ``None`` when there is no section.

    Returns:
        None

    Raises:
        AssertionError: If the wrong text is extracted.
    """
    assert changelog_notes(CHANGES, version) == expected


def test_empty_section_counts_as_missing() -> None:
    """A heading with no notes would publish an empty release description.

    Args:
        None

    Returns:
        None

    Raises:
        AssertionError: If an empty section is accepted.
    """
    assert changelog_notes("## v2.0.0\n\n## 1.0.0\n- x\n", "2.0.0") is None


@pytest.mark.parametrize(("text", "prerelease"), [("0.4.0", True), ("1.0.0", False)])
def test_publish_commands_tag_push_then_release(text: str, prerelease: bool) -> None:
    """Section 28.7: annotated tag, push, then ``gh release create`` with assets.

    Args:
        text: Version being released.
        prerelease: Whether ``--prerelease`` is expected (MVP rule).

    Returns:
        None

    Raises:
        AssertionError: If the commands differ.
    """
    cfg = ReleaseConfig.from_mapping({**EXAMPLE, "prerelease": "rc-or-major-zero"}, Path("/r"))
    version = ReleaseVersion.parse(text)
    files = [Path("/r/out/pkg.deb"), Path("/r/out/SHA256SUMS"), Path("/r/out/SHA256SUMS.sig")]
    tag, push, release = publish_commands(cfg, version, Path("/r/out/notes.txt"), files)
    assert tag == ["git", "tag", "--annotate", f"v{text}", "--message", f"package {text}"]
    assert push == ["git", "push", "remote", f"refs/tags/v{text}"]
    assert release[:6] == ["gh", "release", "create", f"v{text}", "--repo", "owner/example"]
    assert ("--prerelease" in release) is prerelease
    assert release[-3:] == [str(f) for f in files]


def test_dry_run_executes_nothing(
    tmp_path: Path, fake_runner: Any, capsys: pytest.CaptureFixture[str]
) -> None:
    """Section 28.7: a dry run prints the commands and runs none of them.

    Args:
        tmp_path: Working directory for the fake runner.
        fake_runner: ``FakeRunner`` class from ``conftest.py``.
        capsys: Captures printed commands.

    Returns:
        None

    Raises:
        AssertionError: If a command runs or is not shown.
    """
    fake = fake_runner(tmp_path, {("git",): "", ("gh",): ""})
    publish(fake, [["git", "push", "origin", "refs/tags/v1.0.0"]], dry_run=True)
    assert fake.calls == []
    assert "would run: git push origin refs/tags/v1.0.0" in capsys.readouterr().out
    publish(fake, [["gh", "release", "create", "v1.0.0"]], dry_run=False)
    assert fake.calls == [["gh", "release", "create", "v1.0.0"]]
