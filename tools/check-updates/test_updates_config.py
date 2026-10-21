# Copyright 2026 Robert Patrick
# SPDX-License-Identifier: Apache-2.0
"""Tests for configuration validation and failure containment in check-updates.

A mistake in ``check-updates.toml`` must stop the run with the location of
the mistake (exit 2) instead of silently dropping an item from the release
checklist (TOOLS-002). Failures at run time must stay contained: a failing
lockfile command, an unreadable ``pyproject.toml``, or a check that raises
yields an unknown row, and ``uv.lock`` is never left modified (TOOLS-001).

Dependencies:
    pytest: ``tmp_path``, ``capsys``, parametrization.
    check_updates, updates_config, updates_pypi: Modules under test.

Safety:
    Offline and confined to ``tmp_path``.

Example:
    $ uv run pytest tools/check-updates/test_updates_config.py
"""

from collections.abc import Sequence
from pathlib import Path
from typing import Any

import check_updates
import pytest
from updates_actions import ActionPin, check_action, plan_actions
from updates_checks import Context, Task, check_entry
from updates_config import ConfigError, load_config
from updates_fetch import CommandResult
from updates_pypi import check_lockfile, plan_pypi
from updates_report import render_table
from updates_upstream import Upstream

LOCK = 'version = 1\n[[package]]\nname = "cffi"\nversion = "2.1.0"\n'


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("[pythn]\n", "unknown section(s) pythn"),
        ("tools = 3\n", "'tools' must be a list"),
        ("[[tools]]\npypi = 'uv'\n", "needs a string 'name'"),
        ("[[tools]]\nname = 'uv'\n", "exactly one of pypi, github, npm"),
        ("[[tools]]\nname = 'uv'\npypi = 'uv'\nnpm = 'uv'\n", "exactly one of"),
        ("[[tools]]\nname = 'x'\npypi = 'x'\nfile = 'f'\n", "'file' needs a 'regex'"),
        ("[[tools]]\nname = 'x'\npypi = 'x'\nfile = 'f'\nversion = '1'\n", "at most one of"),
        ("[[tools]]\nname = 'x'\npypi = 'x'\ncommand = 'x --version'\n", "list of strings"),
        ("[[tools]]\nname = 'x'\npypi = 'x'\nregex = '('\n", "invalid regex"),
        ("[[tools]]\nname = 'x'\npypi = 'x'\nversoin = '1'\n", "unknown key(s) versoin"),
        ("assets = [{ name = 'echarts', npm = 'echarts' }]\n", "assets need a string 'version'"),
        ("[npm]\npackage = 'web/package.json'\n", "[npm] unknown key(s) package"),
        ("[node]\nbinary = 'node --version'\n", "[node] 'binary' must be a list of strings"),
        ("node = 3\n", "[node] must be a table"),
        ("[python\n", "check-updates.toml"),
    ],
)
def test_invalid_config_exits_2_with_location(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], text: str, message: str
) -> None:
    """Each kind of config mistake is rejected before anything runs (TOOLS-002).

    Args:
        tmp_path: Directory for the config file.
        capsys: Output capture.
        text: Invalid configuration.
        message: Expected part of the error.

    Returns:
        None

    Raises:
        AssertionError: If the mistake is accepted or misreported.
    """
    config = tmp_path / "check-updates.toml"
    config.write_text(text, encoding="utf-8")
    assert check_updates.main(["--config", str(config)]) == 2
    assert message in capsys.readouterr().err


def test_missing_config_is_a_config_error(tmp_path: Path) -> None:
    """A missing file raises ConfigError naming the path (TOOLS-002).

    Args:
        tmp_path: Directory without a config.

    Returns:
        None

    Raises:
        AssertionError: If another error type is raised.
    """
    with pytest.raises(ConfigError, match=r"absent\.toml"):
        load_config(tmp_path / "absent.toml")


class RewritingFetcher:
    """Fetcher stand-in whose lockfile command rewrites or deletes ``uv.lock``."""

    def __init__(self, lock: Path, action: str, result: CommandResult) -> None:
        """Remember what the fake command does.

        Args:
            lock: Lockfile path.
            action: ``"rewrite"`` or ``"delete"``.
            result: Result to return.

        Returns:
            None

        Raises:
            None
        """
        self.lock, self.action, self.result = lock, action, result
        self.calls: list[tuple[list[str], Path | None]] = []

    def run(self, argv: Sequence[str], cwd: Path | None = None) -> CommandResult:
        """Damage the lockfile, then return the scripted result.

        Args:
            argv: Command, recorded.
            cwd: Working directory, recorded.

        Returns:
            CommandResult: The scripted result.

        Raises:
            None
        """
        self.calls.append((list(argv), cwd))
        if self.action == "delete":
            self.lock.unlink()
        else:
            self.lock.write_text("damaged", encoding="utf-8")
        return self.result


@pytest.mark.parametrize("action", ["rewrite", "delete"])
def test_lockfile_is_restored_if_a_command_changes_it(tmp_path: Path, action: str) -> None:
    """A misconfigured upgrade command cannot change the repository (TOOLS-001).

    Args:
        tmp_path: Repository root.
        action: How the fake command damages ``uv.lock``.

    Returns:
        None

    Raises:
        AssertionError: If the lockfile is not restored byte for byte.
    """
    lock = tmp_path / "uv.lock"
    lock.write_text(LOCK, encoding="utf-8")
    output = CommandResult(0, "Update cffi v2.1.0 -> v2.1.1\n", "")
    fetcher: Any = RewritingFetcher(lock, action, output)
    ctx = Context(tmp_path, fetcher, Upstream(fetcher))
    rows = check_lockfile(ctx, {"command": ["uv", "lock", "--upgrade"]}, set())
    assert lock.read_text(encoding="utf-8") == LOCK
    assert [(r.item, r.status) for r in rows] == [
        ("uv.lock (all packages)", "update available"),
        ("cffi", "update available"),
    ]


def test_lockfile_command_failures_are_unknown(tmp_path: Path, fake_fetcher: Any) -> None:
    """A failing or missing lockfile command yields one unknown row with the reason.

    Args:
        tmp_path: Repository root.
        fake_fetcher: Offline fetcher class.

    Returns:
        None

    Raises:
        AssertionError: If the failure is not reported (TOOLS-001).
    """
    argv = ("uv", "lock", "--upgrade", "--dry-run")
    failing = fake_fetcher(commands={argv: CommandResult(2, "", "error: network down\n")})
    rows = check_lockfile(Context(tmp_path, failing, Upstream(failing)), {}, set())
    assert [(r.status, r.detail) for r in rows] == [("unknown", "uv failed: error: network down")]
    missing = fake_fetcher()
    rows = check_lockfile(Context(tmp_path, missing, Upstream(missing)), {}, set())
    assert rows[0].detail == "uv is not installed"


def test_planning_failure_and_crashing_task_become_unknown_rows(
    tmp_path: Path, fake_fetcher: Any
) -> None:
    """An unreadable pyproject and a check that raises each yield one unknown row.

    Args:
        tmp_path: Repository without ``pyproject.toml``.
        fake_fetcher: Offline fetcher class.

    Returns:
        None

    Raises:
        AssertionError: If an exception escapes (TOOLS-001).
    """
    fetcher = fake_fetcher()
    tasks, names = plan_pypi(Context(tmp_path, fetcher, Upstream(fetcher)), {})
    assert names == set()

    def explode() -> list[Any]:
        """Simulate a bug inside a check.

        Args:
            None

        Returns:
            list[Any]: Never returns.

        Raises:
            RuntimeError: Always.
        """
        raise RuntimeError("boom")

    rows = check_updates.run_tasks([*tasks, Task("broken", "tool", explode)], workers=0)
    assert [(r.item, r.status) for r in rows] == [
        ("PyPI dependencies", "unknown"),
        ("broken", "unknown"),
    ]
    assert "RuntimeError('boom')" in rows[1].detail
    assert render_table([]).endswith(
        "check-updates: 0 items: 0 current, 0 update available, 0 unknown"
    )


def test_unversioned_pins_and_failing_commands_are_explained(
    tmp_path: Path, fake_fetcher: Any
) -> None:
    """Branch pins, bare SHAs, odd versions, and failing commands each say why (TOOLS-001).

    Args:
        tmp_path: Repository root.
        fake_fetcher: Offline fetcher class.

    Returns:
        None

    Raises:
        AssertionError: If a reason is missing or wrong.
    """
    fetcher = fake_fetcher(
        {
            "https://api.github.com/repos/o/r/releases?per_page=10": [{"tag_name": "v2.0.0"}],
            "https://pypi.org/pypi/x/json": {"info": {"version": "2.0.0"}},
        },
        {("x", "--version"): CommandResult(1, "", "boom")},
    )
    ctx = Context(tmp_path, fetcher, Upstream(fetcher))
    branch = check_action(ctx, ActionPin("o/r", "main", "main", ""), ["ci.yml"])[0]
    bare = check_action(ctx, ActionPin("o/r", "a" * 40, "", ""), ["ci.yml"])[0]
    assert (branch.status, branch.detail) == (
        "unknown",
        "ref 'main' is a branch or non-version tag",
    )
    assert bare.item == "o/r@aaaaaaa"
    assert "without a '# vX.Y.Z' comment" in bare.detail
    failing = check_entry(ctx, {"name": "x", "command": ["x", "--version"], "pypi": "x"}, "tool")
    assert failing[0].detail == "x --version exited with status 1"
    odd = check_entry(ctx, {"name": "x", "version": "nightly", "pypi": "x"}, "asset")
    assert (odd[0].status, odd[0].detail) == ("unknown", "cannot parse version 'nightly'")


def test_extra_packages_and_unreadable_workflows(tmp_path: Path, fake_fetcher: Any) -> None:
    """``extra`` adds locked names; an unreadable workflow becomes one unknown row.

    Args:
        tmp_path: Repository root.
        fake_fetcher: Offline fetcher class.

    Returns:
        None

    Raises:
        AssertionError: If planning differs (TOOLS-001).
    """
    (tmp_path / "uv.lock").write_text(LOCK, encoding="utf-8")
    fetcher = fake_fetcher()
    ctx = Context(tmp_path, fetcher, Upstream(fetcher))
    tasks, names = plan_pypi(ctx, {"direct_dependencies": False, "extra": ["CFFI", "missing"]})
    rows = check_updates.run_tasks(tasks, workers=2)
    assert names == {"cffi", "missing"}
    assert [(r.item, r.current, r.detail) for r in rows] == [
        ("cffi", "2.1.0", "https://pypi.org/pypi/cffi/json: no recorded response"),
        ("missing", "", "not in the lockfile"),
    ]
    workflow = tmp_path / ".github/workflows/ci.yml"
    workflow.parent.mkdir(parents=True)
    workflow.write_bytes(b"\xff\xfe not utf-8")
    planned = plan_actions(ctx, {})
    assert [r.item for task in planned for r in task.run()] == ["GitHub Actions"]
