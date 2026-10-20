# Copyright 2026 Robert Patrick
# SPDX-License-Identifier: Apache-2.0
"""Tests for reading current versions: pins, locks, workflows, and dry-run output.

The "current" side of every comparison is read from files the project
already maintains: ``.python-version``, ``pyproject.toml``, ``uv.lock``,
workflow ``uses:`` lines, and the change list of ``uv lock --upgrade
--dry-run`` (TOOLS-001, TOOLS-003). Workflow parsing covers tag
pins, commit pins with and without a version comment, quoting, sub-path
actions, local actions, and Docker images.

Dependencies:
    pytest: ``tmp_path`` for throwaway repository files.
    updates_local, updates_pypi, updates_actions: Modules under test.

Safety:
    Writes only beneath ``tmp_path``; no network or processes.

Example:
    $ uv run pytest tools/check-updates/test_updates_local.py
"""

from pathlib import Path

import pytest
import updates_local as local
from updates_actions import action_pin, action_pins, parse_uses
from updates_pypi import (
    LockChange,
    direct_dependencies,
    locked_versions,
    normalize_name,
    parse_lock_changes,
)

SHA = "1c37ad07a6a961277cf70c0d37d6f313000f5884"
WORKFLOW = f"""\
jobs:
  gates:
    steps:
      - uses: actions/checkout@v7
        with:
          persist-credentials: false
      # A comment line that mentions uses: other/action@v1 is ignored.
      - uses: astral-sh/setup-uv@{SHA} # v10.3.0
      - uses: "github/codeql-action/init@v4.2"
      - uses: org/no-comment@{SHA}
      - uses: org/branch@main
      - uses: ./.github/actions/local
      - uses: docker://alpine:3.22
  reuse:
    uses: org/workflows/.github/workflows/build.yml@v2
"""


def test_parse_uses_reads_targets_and_comments() -> None:
    """Every ``uses:`` line yields its target and trailing comment (TOOLS-001).

    Args:
        None

    Returns:
        None

    Raises:
        AssertionError: If a line is missed or misread.
    """
    found = parse_uses(WORKFLOW)
    assert found[0] == ("actions/checkout@v7", "")
    assert found[1] == (f"astral-sh/setup-uv@{SHA}", "v10.3.0")
    assert found[2] == ("github/codeql-action/init@v4.2", "")
    assert len(found) == 8


def test_action_pins_classify_tag_sha_branch_and_local(tmp_path: Path) -> None:
    """Tag pins keep their tag, SHA pins take the comment version, local refs are dropped.

    Args:
        tmp_path: Throwaway repository.

    Returns:
        None

    Raises:
        AssertionError: If a pin is misclassified (TOOLS-001).
    """
    workflows = tmp_path / ".github/workflows"
    workflows.mkdir(parents=True)
    (workflows / "ci.yml").write_text(WORKFLOW, encoding="utf-8")
    pins = action_pins(tmp_path, [".github/workflows/*.yml"])
    summary = [(p.repo, p.version, p.is_sha, p.file) for p in pins]
    assert summary == [
        ("actions/checkout", "v7", False, ".github/workflows/ci.yml"),
        ("astral-sh/setup-uv", "v10.3.0", True, ".github/workflows/ci.yml"),
        ("github/codeql-action", "v4.2", False, ".github/workflows/ci.yml"),
        ("org/no-comment", "", True, ".github/workflows/ci.yml"),
        ("org/branch", "main", False, ".github/workflows/ci.yml"),
        ("org/workflows", "v2", False, ".github/workflows/ci.yml"),
    ]


@pytest.mark.parametrize(
    ("note", "version"),
    [("v4.1.1", "v4.1.1"), ("pin@v4.1.1", "v4.1.1"), ("tag=v3", "v3"), ("latest", "")],
)
def test_sha_pin_comment_spellings(note: str, version: str) -> None:
    """Common comment styles after a SHA pin yield the version (TOOLS-001).

    Args:
        note: Trailing comment text.
        version: Expected version.

    Returns:
        None

    Raises:
        AssertionError: If the version is not found.
    """
    pin = action_pin(f"o/r@{SHA}", note, "ci.yml")
    assert pin is not None
    assert pin.version == version
    assert action_pin("o/r", "", "ci.yml") is None


def test_python_pins(tmp_path: Path) -> None:
    """``.python-version`` and the ``requires-python`` floor are read (TOOLS-003).

    Args:
        tmp_path: Throwaway repository.

    Returns:
        None

    Raises:
        AssertionError: If a pin is misread.
    """
    (tmp_path / ".python-version").write_text("# pinned\n\n3.15\n", encoding="utf-8")
    assert local.read_version_file(tmp_path / ".python-version") == "3.15"
    (tmp_path / "empty").write_text("# nothing\n", encoding="utf-8")
    with pytest.raises(ValueError, match="holds no version"):
        local.read_version_file(tmp_path / "empty")
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text('[project]\nrequires-python = ">=3.15, <4"\n', encoding="utf-8")
    assert local.requires_python_floor(pyproject) == "3.15"
    pyproject.write_text('[project]\nrequires-python = "<4"\n', encoding="utf-8")
    with pytest.raises(ValueError, match="lower bound"):
        local.requires_python_floor(pyproject)


@pytest.mark.parametrize(
    ("text", "regex", "expected"),
    [
        ("uv 0.13.3 (x86_64-unknown-linux-gnu)", None, "0.13.3"),
        ("go version go1.27.3 linux/amd64", None, "1.27.3"),
        ("v8.30.1", None, "8.30.1"),
        ("GitVersion:    2.48.0\nGoVersion: go1.27.3", r"GitVersion:\s*v?(\S+)", "2.48.0"),
        ('    "markdown-it-py==4.2.0",', r'"markdown-it-py==([^"]+)"', "4.2.0"),
        ("no version here", None, None),
    ],
)
def test_find_version(text: str, regex: str | None, expected: str | None) -> None:
    """Command output and pinned files yield their version (TOOLS-001).

    Args:
        text: Output or file text.
        regex: Optional extraction pattern.
        expected: Expected version.

    Returns:
        None

    Raises:
        AssertionError: If extraction differs.
    """
    assert local.find_version(text, regex) == expected


def test_direct_dependencies_and_locked_versions(tmp_path: Path) -> None:
    """Runtime, extra, and group dependencies are listed and matched to the lock (TOOLS-001).

    Args:
        tmp_path: Throwaway repository.

    Returns:
        None

    Raises:
        AssertionError: If names or versions differ.
    """
    (tmp_path / "pyproject.toml").write_text(
        "[project]\n"
        'dependencies = ["cryptography>=50", "Tomlkit[extra]>=0.15 ; python_version>\'3\'"]\n'
        '[project.optional-dependencies]\nfast = ["Mdit_Py.Plugins"]\n'
        '[dependency-groups]\ndev = ["ruff>=0.17", {include-group = "lint"}, "ruff"]\n'
        'lint = ["ty"]\n',
        encoding="utf-8",
    )
    names = direct_dependencies(tmp_path / "pyproject.toml")
    assert names == ["cryptography", "tomlkit", "mdit-py-plugins", "ruff", "ty"]
    (tmp_path / "uv.lock").write_text(
        'version = 1\n[[package]]\nname = "Tomlkit"\nversion = "0.15.2"\n'
        '[[package]]\nname = "demo"\nsource = { editable = "." }\n',
        encoding="utf-8",
    )
    assert locked_versions(tmp_path / "uv.lock") == {"tomlkit": "0.15.2"}
    assert normalize_name("Mdit_Py..Plugins") == "mdit-py-plugins"


def test_parse_lock_changes_reads_update_add_remove() -> None:
    """Dry-run output, with colour codes and noise, yields each change (TOOLS-001).

    Args:
        None

    Returns:
        None

    Raises:
        AssertionError: If a change is missed.
    """
    output = (
        "Using CPython 3.15.0\nResolved 18 packages in 888ms\n"
        "\x1b[1mUpdate\x1b[0m tomlkit v0.13.0 -> v0.15.2\nAdd idna v3.11\nRemove six v1.17.0\n"
    )
    assert parse_lock_changes(output) == [
        LockChange("Update", "tomlkit", "0.13.0", "0.15.2"),
        LockChange("Add", "idna", "3.11", ""),
        LockChange("Remove", "six", "1.17.0", ""),
    ]
    assert parse_lock_changes("Resolved 18 packages\nNo lockfile changes detected\n") == []
