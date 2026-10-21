# Copyright 2026 Robert Patrick
# SPDX-License-Identifier: Apache-2.0
"""Tests for the Node.js checks: LTS choice, ``engines`` floors, and rows (TOOLS-001, ADR-035 #18).

The web UI's build host pins Node by its LTS major line. These tests feed
a trimmed recording of ``https://nodejs.org/dist/index.json`` (2026-10-21:
v26.12.1 is Current, v24.22.1 the newest LTS) to the parser and the
check, and cover the precision rules: the version file as written, the
``engines`` floor by major line, the binary exactly.

Dependencies:
    pytest: ``tmp_path`` for throwaway repositories.
    updates_node, updates_upstream: Modules under test.

Safety:
    Offline: ``node`` is never run; command results and the index are canned.

Example:
    $ uv run pytest tools/check-updates/test_updates_node.py
"""

import json
from pathlib import Path
from typing import Any

import pytest
from updates_checks import Context
from updates_fetch import CommandResult, FetchError
from updates_node import check_node, engines_floor, engines_floor_text, node_latest
from updates_upstream import NODE_RELEASES_URL, Upstream
from updates_versions import CURRENT, UNKNOWN, UPDATE

NODE_CFG = {
    "version_file": "web/.node-version",
    "engines": "web/package.json",
    "binary": ["node", "--version"],
}


def _repo(root: Path, pin: str = "24\n", engines: str = ">=24.22.0 <25") -> None:
    """Write a web build's Node pin and ``package.json`` under ``root``.

    Args:
        root: Repository root.
        pin: ``.node-version`` contents.
        engines: ``engines.node`` range.

    Returns:
        None

    Raises:
        None
    """
    (root / "web").mkdir()
    (root / "web/.node-version").write_text(pin, encoding="utf-8")
    package = {"name": "w", "version": "0.0.0", "engines": {"node": engines}}
    (root / "web/package.json").write_text(json.dumps(package), encoding="utf-8")


def _ctx(root: Path, fetcher: Any) -> Context:
    """Build a task context on the fake fetcher.

    Args:
        root: Repository root.
        fetcher: ``FakeFetcher`` instance.

    Returns:
        Context: The context.

    Raises:
        None
    """
    return Context(root, fetcher, Upstream(fetcher))


def test_node_latest_picks_the_newest_lts_not_the_newest_current(recorded: Any) -> None:
    """LTS releases name a codename; Current ones are never the latest LTS (TOOLS-001).

    Args:
        recorded: Fixture loader.

    Returns:
        None

    Raises:
        AssertionError: If the wrong release is chosen.
    """
    index = recorded("node-index.json")
    lts = node_latest(index)
    assert lts.version.text == "v24.22.1"
    assert lts.notes_url == "https://nodejs.org/en/blog/release/v24.22.1"
    assert node_latest(index, lts=False).version.text == "v26.12.1"
    with pytest.raises(FetchError, match="no LTS release"):
        node_latest([{"version": "v27.0.0", "lts": False}])


@pytest.mark.parametrize(
    ("spec", "floor"),
    [
        (">=24.22.0 <25", "24.22.0"),
        ("^24.1.0", "24.1.0"),
        ("~24.3", "24.3"),
        ("24.x", "24"),
        ("v24.0.0", "24.0.0"),
        ("*", None),
    ],
)
def test_engines_floor_reads_common_ranges(spec: str, floor: str | None) -> None:
    """The lowest admitted version is read from the usual ``engines`` spellings (TOOLS-001).

    Args:
        spec: ``engines.node`` range.
        floor: Expected floor.

    Returns:
        None

    Raises:
        AssertionError: If the floor differs.
    """
    assert engines_floor_text(spec) == floor


def test_engines_floor_needs_an_engines_entry(tmp_path: Path) -> None:
    """A ``package.json`` without ``engines.node`` is a reason, not a crash (TOOLS-001).

    Args:
        tmp_path: Throwaway directory.

    Returns:
        None

    Raises:
        AssertionError: If no error is raised.
    """
    path = tmp_path / "package.json"
    path.write_text('{"name": "w"}', encoding="utf-8")
    with pytest.raises(ValueError, match=r"no engines\.node"):
        engines_floor(path)


def test_check_node_rows_follow_the_pin_precision(
    tmp_path: Path, fake_fetcher: Any, recorded: Any
) -> None:
    """Major pin and floor are current on the newest LTS line; the binary is exact (ADR-035 #18).

    Args:
        tmp_path: Throwaway repository.
        fake_fetcher: Offline fetcher class.
        recorded: Fixture loader.

    Returns:
        None

    Raises:
        AssertionError: If a row's status or detail differs.
    """
    _repo(tmp_path)
    fetcher = fake_fetcher(
        {NODE_RELEASES_URL: recorded("node-index.json")},
        {("node", "--version"): CommandResult(0, "v24.22.0\n", "")},
    )
    rows = check_node(_ctx(tmp_path, fetcher), NODE_CFG)
    summary = [(r.item, r.current, r.latest, r.status) for r in rows]
    assert summary == [
        ("Node (web/.node-version)", "24", "v24.22.1", CURRENT),
        ("Node (engines.node in web/package.json)", "24.22.0", "v24.22.1", CURRENT),
        ("Node (installed)", "24.22.0", "v24.22.1", UPDATE),
    ]
    assert rows[2].detail == "newest Current (not LTS): v26.12.1"
    assert fetcher.requests == [NODE_RELEASES_URL]  # one fetch serves every row


def test_check_node_reports_an_older_line_and_missing_pieces(
    tmp_path: Path, fake_fetcher: Any, recorded: Any
) -> None:
    """A pin on an older LTS line is behind; missing files and binaries are unknown (TOOLS-001).

    Args:
        tmp_path: Throwaway repository.
        fake_fetcher: Offline fetcher class.
        recorded: Fixture loader.

    Returns:
        None

    Raises:
        AssertionError: If a row's status or reason differs.
    """
    _repo(tmp_path, pin="22\n", engines="*")
    fetcher = fake_fetcher({NODE_RELEASES_URL: recorded("node-index.json")})
    rows = check_node(_ctx(tmp_path, fetcher), NODE_CFG)
    assert [r.status for r in rows] == [UPDATE, UNKNOWN, UNKNOWN]
    assert rows[1].detail == "engines.node has no lower bound"
    assert rows[2].detail == "node is not installed"
    missing = check_node(_ctx(tmp_path / "absent", fetcher), {"version_file": ".node-version"})
    assert missing[0].status == UNKNOWN
    assert "No such file" in missing[0].detail


def test_check_node_when_nodejs_org_is_unreachable(tmp_path: Path, fake_fetcher: Any) -> None:
    """An unreachable index makes every row unknown with the reason (TOOLS-001).

    Args:
        tmp_path: Throwaway repository.
        fake_fetcher: Offline fetcher class.

    Returns:
        None

    Raises:
        AssertionError: If a row is not unknown.
    """
    _repo(tmp_path)
    fetcher = fake_fetcher({NODE_RELEASES_URL: FetchError("nodejs.org: timed out")})
    rows = check_node(_ctx(tmp_path, fetcher), {"version_file": "web/.node-version"})
    assert [(r.status, r.detail) for r in rows] == [(UNKNOWN, "nodejs.org: timed out")]
