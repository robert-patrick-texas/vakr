# Copyright 2026 Robert Patrick
# SPDX-License-Identifier: Apache-2.0
"""Tests for the release-time dependency currency check (TOOLS-005, ADR-036).

A release must ship on the newest dependency releases, or on versions held
by a recorded exception, as they stand when the release starts; a release
published hours after preparation counts. The configured ``update_check``
command runs in preflight and must exit 0 (TOOLS-T08). Real ``sh``
commands stand in for check-updates, so exit codes are real; ``git`` and
``gh`` are faked.

Dependencies:
    pytest: Test runner; ``fake_runner`` comes from ``conftest.py``.
    release, release_currency, release_config: Modules under test.

Safety:
    Offline: the only real commands are ``sh -c`` one-liners that print a
    line and exit.

Example:
    $ uv run pytest tools/release/test_release_currency.py
"""

from pathlib import Path
from typing import Any

import pytest
import release
import release_currency as currency
from release_config import EXAMPLE, ConfigError, ReleaseConfig
from release_version import ReleaseVersion

PASSED = ["sh", "-c", "echo 'table'; echo 'check-updates: gate passed'"]
FAILED = ["sh", "-c", "echo 'check-updates: gate failed: 1 update available (ruff)'; exit 1"]
GREEN = {"check_runs": [{"name": "gates", "status": "completed", "conclusion": "success"}]}


def test_passing_check_reports_its_last_line(tmp_path: Path, fake_runner: Any) -> None:
    """Exit 0 is OK, and the detail is the command's verdict line (TOOLS-T08).

    Args:
        tmp_path: Working directory for the runner.
        fake_runner: ``FakeRunner`` class.

    Returns:
        None

    Raises:
        AssertionError: If the outcome differs.
    """
    outcome = currency.updates_current(fake_runner(tmp_path, {}), PASSED)
    assert (outcome.ok, outcome.detail) == (True, "check-updates: gate passed")


def test_failing_or_missing_check_blocks(tmp_path: Path, fake_runner: Any) -> None:
    """A non-zero exit or a missing program fails, naming what blocks (TOOLS-T08).

    Args:
        tmp_path: Working directory for the runner.
        fake_runner: ``FakeRunner`` class.

    Returns:
        None

    Raises:
        AssertionError: If a failure passes.
    """
    runner = fake_runner(tmp_path, {})
    failed = currency.updates_current(runner, FAILED)
    assert not failed.ok
    assert failed.detail.startswith("check-updates: gate failed: 1 update available (ruff)")
    assert "(exit 1; apply each update or record a failed test)" in failed.detail
    missing = currency.updates_current(runner, ["no-such-update-checker", "--gate"])
    assert (missing.ok, missing.detail) == (False, "command not found: no-such-update-checker")
    silent = currency.updates_current(runner, ["sh", "-c", "exit 3"])
    assert (silent.ok, silent.detail.split(" (")[0]) == (False, "no output")


def test_update_check_is_optional_and_typed() -> None:
    """``update_check`` may be absent or empty; a string instead of a list is refused.

    Args:
        None

    Returns:
        None

    Raises:
        AssertionError: If a value is resolved wrongly or a bad one is accepted.
    """
    root = Path("/repo")
    assert ReleaseConfig.from_mapping(EXAMPLE, root).update_check == ()
    assert ReleaseConfig.from_mapping({**EXAMPLE, "update_check": []}, root).update_check == ()
    cfg = ReleaseConfig.from_mapping({**EXAMPLE, "update_check": ["make", "updates"]}, root)
    assert cfg.update_check == ("make", "updates")
    with pytest.raises(ConfigError, match="update_check must be a list"):
        ReleaseConfig.from_mapping({**EXAMPLE, "update_check": "make updates"}, root)


@pytest.mark.parametrize(("command", "ok"), [(PASSED, True), (FAILED, False)])
def test_preflight_runs_the_update_check_last(
    tmp_path: Path, fake_runner: Any, command: list[str], ok: bool
) -> None:
    """Preflight ends with the update check, so a real release stops on it (TOOLS-T08).

    Args:
        tmp_path: Repository root (no files needed; missing ones are tolerated).
        fake_runner: ``FakeRunner`` class.
        command: The configured ``update_check``.
        ok: Expected outcome.

    Returns:
        None

    Raises:
        AssertionError: If the check is missing or misjudged.
    """
    cfg = ReleaseConfig.from_mapping({**EXAMPLE, "update_check": command}, tmp_path)
    runner = fake_runner(tmp_path, {("git",): "", ("gh", "api"): GREEN})
    outcomes = release.preflight(cfg, runner, ReleaseVersion.parse("1.2.3"), {})
    assert (outcomes[-1].name, outcomes[-1].ok) == ("dependencies current at release time", ok)
    assert command in runner.calls
    plain = ReleaseConfig.from_mapping(EXAMPLE, tmp_path)
    names = [o.name for o in release.preflight(plain, runner, ReleaseVersion.parse("1.2.3"), {})]
    assert "dependencies current at release time" not in names


def test_shipped_release_config_runs_the_gate() -> None:
    """The shipped release.toml runs check-updates --gate at release time (TOOLS-005, ADR-036).

    Args:
        None

    Returns:
        None

    Raises:
        AssertionError: If the shipped configuration drops the gate.
    """
    here = Path(release.__file__).resolve().parent
    cfg = release.load_config(here / "release.toml", here.parents[1])
    assert cfg.update_check[-2:] == ("tools/check-updates/check_updates.py", "--gate")
