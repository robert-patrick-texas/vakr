# Copyright 2026 Robert Patrick
# SPDX-License-Identifier: Apache-2.0
"""Test that the repository's own check-updates configuration and exceptions file are valid.

The configuration and the exceptions file are the only project-specific
inputs (blueprint section 29), and the release gate reads both. A typo in
either must fail here, offline, rather than at release time (TOOLS-001,
TOOLS-004).

Dependencies:
    pytest: ``capsys``.
    check_updates: Entry point, run against the repository root.

Safety:
    Offline: ``FakeFetcher`` answers nothing, so every row is unknown and
    no network or command is used.

Example:
    $ uv run pytest tools/check-updates/test_updates_shipped.py
"""

import json
from pathlib import Path
from typing import Any

import check_updates
import pytest


def test_shipped_config_is_valid_and_plans_offline(
    fake_fetcher: Any, capsys: pytest.CaptureFixture[str]
) -> None:
    """The repository's own config and exceptions file load and run offline (TOOLS-001).

    Args:
        fake_fetcher: Offline fetcher class.
        capsys: Output capture.

    Returns:
        None

    Raises:
        AssertionError: If the shipped files are invalid or the run fails.
    """
    root = Path(check_updates.__file__).resolve().parents[2]
    argv = ["--root", str(root), "--json"]
    assert check_updates.main(argv, fake_fetcher()) == 0
    report = json.loads(capsys.readouterr().out)
    items = {row["item"] for row in report["items"]}
    assert {"Python (.python-version)", "uv", "ruff", "ty", "uv.lock (all packages)"} <= items
    assert {"uv (packaging/Dockerfile)", "Go (packaging/Dockerfile)", "@types/node"} <= items
    assert any(item.startswith("astral-sh/setup-uv@") for item in items)
    # Offline, nothing is judged, so no recorded exception can look stale.
    assert report["problems"] == []
