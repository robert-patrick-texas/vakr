# Copyright 2026 Robert Patrick
# SPDX-License-Identifier: Apache-2.0
"""Tests for the build-host toolchain preflight: Node and npm present, Node on its pin (ADR-035).

The web UI joins the build (ADR-035 consequences), so a release host
without Node or npm, or with Node on another line than ``web/.node-version``
pins, must be stopped in preflight. Commands are answered by the fake
runner; nothing is installed or executed.

Dependencies:
    pytest: Test runner; ``fake_runner`` comes from ``conftest.py``.
    release, release_checks, release_config: Modules under test.

Safety:
    Offline: ``node``, ``npm``, ``git``, and ``gh`` are all faked.

Example:
    $ uv run pytest tools/release/test_release_toolchain.py
"""

from pathlib import Path
from typing import Any

import pytest
import release
import release_checks as checks
from release_config import EXAMPLE, ConfigError, ReleaseConfig
from release_proc import CommandError
from release_version import ReleaseVersion

NODE = ("node", "--version")
NPM = ("npm", "--version")
TOOLS = {NODE: "v24.22.0\n", NPM: "11.19.1\n"}


def test_tools_present_reports_each_version(tmp_path: Path, fake_runner: Any) -> None:
    """Both tools answer ``--version``: OK, and the detail names their versions (ADR-035).

    Args:
        tmp_path: Working directory for the fake runner.
        fake_runner: ``FakeRunner`` class.

    Returns:
        None

    Raises:
        AssertionError: If the outcome differs.
    """
    outcome = checks.tools_present(fake_runner(tmp_path, TOOLS), ["node", "npm"])
    assert outcome == checks.Outcome(
        "build tools present (node, npm)", True, "node v24.22.0, npm 11.19.1"
    )


def test_a_missing_tool_stops_the_release(tmp_path: Path, fake_runner: Any) -> None:
    """A tool that is not installed fails with an operator sentence naming it (ADR-035).

    Args:
        tmp_path: Working directory for the fake runner.
        fake_runner: ``FakeRunner`` class.

    Returns:
        None

    Raises:
        AssertionError: If the check passes or the detail differs.
    """
    runner = fake_runner(tmp_path, {NODE: "v24.22.0\n", NPM: CommandError("not found: npm")})
    outcome = checks.tools_present(runner, ["node", "npm"])
    assert not outcome.ok
    assert outcome.detail == "missing or not working: npm; install them on this host"


@pytest.mark.parametrize(
    ("pin", "node", "ok"),
    [
        ("24\n", "v24.22.0", True),
        ("# Node LTS line\n24\n", "v24.22.1", True),
        ("v24\n", "v24.0.0", True),
        ("24\n", "v26.12.1", False),
        ("24.22.1\n", "v24.22.0", False),
    ],
)
def test_node_matches_pin_at_the_written_precision(
    tmp_path: Path, fake_runner: Any, pin: str, node: str, ok: bool
) -> None:
    """The pin is compared as written: a major admits its line, a full version only itself.

    Args:
        tmp_path: Directory holding the pin file.
        fake_runner: ``FakeRunner`` class.
        pin: ``.node-version`` contents.
        node: What ``node --version`` prints.
        ok: Expected result.

    Returns:
        None

    Raises:
        AssertionError: If the result differs.
    """
    path = tmp_path / ".node-version"
    path.write_text(pin, encoding="utf-8")
    outcome = checks.node_matches_pin(fake_runner(tmp_path, {NODE: node}), path)
    assert outcome.ok is ok
    assert outcome.detail.startswith(f"node {node.removeprefix('v')}; .node-version pins ")
    if not ok:
        assert outcome.detail.endswith(": install Node " + pin.strip().removeprefix("v"))


def test_node_pin_problems_are_failed_outcomes(tmp_path: Path, fake_runner: Any) -> None:
    """A missing or empty pin file, or no node, fails cleanly instead of raising (ADR-035).

    Args:
        tmp_path: Directory for the pin file.
        fake_runner: ``FakeRunner`` class.

    Returns:
        None

    Raises:
        AssertionError: If any problem passes or raises.
    """
    path = tmp_path / ".node-version"
    runner = fake_runner(tmp_path, {NODE: "v24.22.0"})
    assert checks.node_matches_pin(runner, path).detail.startswith(f"cannot read {path}")
    path.write_text("# nothing\n", encoding="utf-8")
    assert checks.node_matches_pin(runner, path).detail == ".node-version names no version"
    path.write_text("24\n", encoding="utf-8")
    absent = fake_runner(tmp_path, {NODE: CommandError("command not found: node")})
    assert checks.node_matches_pin(absent, path) == checks.Outcome(
        "node matches .node-version", False, "command not found: node"
    )


def test_config_keys_are_optional_and_typed() -> None:
    """``build_tools`` and ``node_version_file`` may be absent or empty, never mistyped.

    Args:
        None

    Returns:
        None

    Raises:
        AssertionError: If a value is resolved wrongly or a bad one is accepted.
    """
    root = Path("/repo")
    plain = ReleaseConfig.from_mapping(EXAMPLE, root)
    assert (plain.build_tools, plain.node_version_file) == ((), None)
    data = {**EXAMPLE, "build_tools": ["node", "npm"], "node_version_file": "web/.node-version"}
    cfg = ReleaseConfig.from_mapping(data, root)
    assert cfg.build_tools == ("node", "npm")
    assert cfg.node_version_file == root / "web/.node-version"
    assert (
        ReleaseConfig.from_mapping({**EXAMPLE, "node_version_file": ""}, root).node_version_file
        is None
    )
    with pytest.raises(ConfigError, match="build_tools must be a list"):
        ReleaseConfig.from_mapping({**EXAMPLE, "build_tools": "node"}, root)


def test_preflight_ends_with_the_toolchain_checks(tmp_path: Path, fake_runner: Any) -> None:
    """``preflight`` runs the configured toolchain checks after the others (ADR-035).

    Args:
        tmp_path: Repository root (no files needed; missing ones are tolerated).
        fake_runner: ``FakeRunner`` class.

    Returns:
        None

    Raises:
        AssertionError: If the toolchain outcomes are missing or wrong.
    """
    (tmp_path / "web").mkdir()
    (tmp_path / "web/.node-version").write_text("24\n", encoding="utf-8")
    data = {**EXAMPLE, "build_tools": ["node", "npm"], "node_version_file": "web/.node-version"}
    cfg = ReleaseConfig.from_mapping(data, tmp_path)
    green = {"check_runs": [{"name": "gates", "status": "completed", "conclusion": "success"}]}
    runner = fake_runner(tmp_path, {**TOOLS, ("git",): "", ("gh", "api"): green})
    outcomes = release.preflight(cfg, runner, ReleaseVersion.parse("1.2.3"), {})
    assert [(o.name, o.ok) for o in outcomes[-2:]] == [
        ("build tools present (node, npm)", True),
        ("node matches .node-version", True),
    ]
    assert ["node", "--version"] in runner.calls
    assert ["npm", "--version"] in runner.calls
