# Copyright 2026 Robert Patrick
# SPDX-License-Identifier: Apache-2.0
"""Tests for the Go checks: ``go.mod`` directives and module requirements (TOOLS-001).

Go modules are reported through Go's own ``go list -m -u -json all``,
whose output is a stream of JSON objects rather than one document. These
tests cover discovery, the directive, the stream parser, and the rows built
from it, including failure of the command.

Dependencies:
    pytest: ``tmp_path`` for throwaway modules.
    updates_go, updates_local: Modules under test.

Safety:
    Offline: ``go`` is never run; command results are canned.

Example:
    $ uv run pytest tools/check-updates/test_updates_go.py
"""

from pathlib import Path
from typing import Any

import pytest
import updates_local as local
from updates_checks import Context
from updates_fetch import CommandResult
from updates_go import GO_LIST, GoModule, check_go, go_directive, parse_go_list
from updates_upstream import Upstream


def test_go_directive_and_discovery_skip_vendored_trees(tmp_path: Path) -> None:
    """``go.mod`` directives are found outside vendored and virtualenv trees (TOOLS-001).

    Args:
        tmp_path: Throwaway repository.

    Returns:
        None

    Raises:
        AssertionError: If discovery or parsing differs.
    """
    for rel in ("cmd/probe/go.mod", "vendor/x/go.mod", ".venv/y/go.mod"):
        (tmp_path / rel).parent.mkdir(parents=True)
        (tmp_path / rel).write_text("module x\n\ngo 1.27.0\n", encoding="utf-8")
    found = local.discover(tmp_path, ["**/go.mod"])
    assert [p.relative_to(tmp_path).as_posix() for p in found] == ["cmd/probe/go.mod"]
    assert go_directive(found[0]) == "1.27.0"
    (tmp_path / "bad.mod").write_text("module x\n", encoding="utf-8")
    with pytest.raises(ValueError, match="no go directive"):
        go_directive(tmp_path / "bad.mod")


def test_parse_go_list_reads_the_json_stream() -> None:
    """``go list -m -u -json all`` output yields modules with their updates (TOOLS-001).

    Args:
        None

    Returns:
        None

    Raises:
        AssertionError: If a module is misread.
    """
    output = """\
{
\t"Path": "example.com/probe",
\t"Main": true,
\t"GoVersion": "1.27"
}
{
\t"Path": "golang.org/x/sys",
\t"Version": "v0.30.0",
\t"Update": {"Path": "golang.org/x/sys", "Version": "v0.31.0"}
}
{"Path": "golang.org/x/text", "Version": "v0.20.0", "Indirect": true}
"""
    assert parse_go_list(output) == [
        GoModule("example.com/probe", "", "", main=True, indirect=False),
        GoModule("golang.org/x/sys", "v0.30.0", "v0.31.0", main=False, indirect=False),
        GoModule("golang.org/x/text", "v0.20.0", "", main=False, indirect=True),
    ]
    assert parse_go_list("") == []
    with pytest.raises(ValueError, match="Expecting value"):
        parse_go_list("go: not a module")


def test_go_list_failure_and_disabled_modules(tmp_path: Path, fake_fetcher: Any) -> None:
    """A failing ``go list`` is one unknown row; ``modules = false`` skips it (TOOLS-001).

    Args:
        tmp_path: Repository with one Go module.
        fake_fetcher: Offline fetcher class.

    Returns:
        None

    Raises:
        AssertionError: If rows differ.
    """
    (tmp_path / "go.mod").write_text("module probe\n\ngo 1.27\n", encoding="utf-8")
    failing = CommandResult(1, "", "go: updates to go.mod needed\n")
    fetcher = fake_fetcher(commands={tuple(GO_LIST): failing})
    ctx = Context(tmp_path, fetcher, Upstream(fetcher))
    rows = check_go(ctx, {"go_mod": ["go.mod"]})
    assert [(r.item, r.status, r.detail) for r in rows[1:]] == [
        ("Go modules (go.mod)", "unknown", "go list failed: go: updates to go.mod needed"),
    ]
    rows = check_go(ctx, {"go_mod": ["go.mod"], "modules": False})
    assert [r.item for r in rows] == ["Go (go.mod go directive)"]
