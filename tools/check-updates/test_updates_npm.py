# Copyright 2026 Robert Patrick
# SPDX-License-Identifier: Apache-2.0
"""Tests for the npm checks: direct packages, lockfile, registry rows (TOOLS-001, ADR-035 #18).

The web UI's packages are locked in ``package-lock.json``. These tests
build throwaway ``package.json`` and lockfiles, answer registry requests
from canned manifests (one trimmed recording of ``@sveltejs/kit``, taken
2026-10-21), and check the rows, the scoped-name URL encoding, and the
release-notes link derived from the manifest's ``repository``.

Dependencies:
    pytest: ``tmp_path`` and ``capsys``.
    updates_npm, updates_sources, check_updates: Modules under test.

Safety:
    Offline: no npm command runs and every request is canned.

Example:
    $ uv run pytest tools/check-updates/test_updates_npm.py
"""

import json
from pathlib import Path
from typing import Any

import check_updates
import pytest
from updates_checks import Context
from updates_npm import direct_packages, locked_from, plan_npm
from updates_sources import npm_notes_url
from updates_upstream import Upstream
from updates_versions import CURRENT, UNKNOWN, UPDATE

KIT = "https://registry.npmjs.org/@sveltejs%2Fkit/latest"
UPLOT = "https://registry.npmjs.org/uplot/latest"
CFG = {"package_json": "web/package.json", "lockfile": "web/package-lock.json"}


def _web(root: Path) -> None:
    """Write a front end with one bundled and two development packages.

    Args:
        root: Repository root.

    Returns:
        None

    Raises:
        None
    """
    (root / "web").mkdir()
    package = {
        "name": "w",
        "version": "0.0.0",
        "dependencies": {"uplot": "1.6.31", "@sveltejs/kit": "3.1.0"},
        "devDependencies": {"@sveltejs/kit": "3.1.0", "vite": "8.3.5"},
    }
    lock = {
        "lockfileVersion": 3,
        "packages": {
            "": {"name": "w"},
            "node_modules/uplot": {"version": "1.6.31"},
            "node_modules/@sveltejs/kit": {"version": "3.1.0"},
            "node_modules/@sveltejs/kit/node_modules/cookie": {"version": "1.0.0"},
            "node_modules/tslib": {"version": "2.8.1"},
        },
    }
    (root / "web/package.json").write_text(json.dumps(package), encoding="utf-8")
    (root / "web/package-lock.json").write_text(json.dumps(lock), encoding="utf-8")


def test_direct_packages_keep_order_and_kind(tmp_path: Path) -> None:
    """Bundled packages come first; a name in both lists counts as bundled (ADR-035 #18).

    Args:
        tmp_path: Throwaway repository.

    Returns:
        None

    Raises:
        AssertionError: If the names or kinds differ.
    """
    _web(tmp_path)
    assert direct_packages(tmp_path / "web/package.json") == {
        "uplot": "bundled",
        "@sveltejs/kit": "bundled",
        "vite": "development",
    }


def test_lockfile_versions_skip_nested_copies_and_read_version_1() -> None:
    """Only top-level installs are the project's pins; lockfile v1 still reads (TOOLS-001).

    Args:
        None

    Returns:
        None

    Raises:
        AssertionError: If the versions differ.
    """
    v3 = {"packages": {"": {}, "node_modules/a": {"version": "1.0.0"},
                       "node_modules/a/node_modules/b": {"version": "9.9.9"}}}  # fmt: skip
    assert locked_from(v3) == {"a": "1.0.0"}
    assert locked_from({"dependencies": {"b": {"version": "2.0.0"}}}) == {"b": "2.0.0"}


def test_plan_npm_compares_locked_versions_with_the_registry(
    tmp_path: Path, fake_fetcher: Any, recorded: Any
) -> None:
    """Rows per direct package and extra, scoped names encoded, unlocked names unknown.

    Args:
        tmp_path: Throwaway repository.
        fake_fetcher: Offline fetcher class.
        recorded: Fixture loader.

    Returns:
        None

    Raises:
        AssertionError: If a row differs.
    """
    _web(tmp_path)
    fetcher = fake_fetcher(
        {
            KIT: recorded("npm-sveltejs-kit-latest.json"),
            UPLOT: {"version": "1.6.32", "repository": "github:leeoniya/uPlot"},
            "https://registry.npmjs.org/tslib/latest": {"version": "2.8.1"},
        }
    )
    ctx = Context(tmp_path, fetcher, Upstream(fetcher))
    rows = [row for task in plan_npm(ctx, {**CFG, "extra": ["tslib"]}) for row in task.run()]
    summary = [(r.item, r.current, r.latest, r.status) for r in rows]
    assert summary == [
        ("uplot", "1.6.31", "1.6.32", UPDATE),
        ("@sveltejs/kit", "3.1.0", "3.1.0", CURRENT),
        ("vite", "", "", UNKNOWN),
        ("tslib", "2.8.1", "2.8.1", CURRENT),
    ]
    assert rows[0].notes == "https://github.com/leeoniya/uPlot/releases"
    assert rows[0].detail == "bundled npm package"
    assert rows[1].notes == "https://github.com/sveltejs/kit/releases"
    assert rows[2].detail == "not in the lockfile"


def test_plan_npm_before_the_front_end_exists(tmp_path: Path, fake_fetcher: Any) -> None:
    """Missing ``package.json`` or lockfile is one unknown row with the reason (TOOLS-001).

    Args:
        tmp_path: Empty repository.
        fake_fetcher: Offline fetcher class.

    Returns:
        None

    Raises:
        AssertionError: If the row differs.
    """
    fetcher = fake_fetcher()
    tasks = plan_npm(Context(tmp_path, fetcher, Upstream(fetcher)), CFG)
    [row] = [row for task in tasks for row in task.run()]
    assert (row.item, row.category, row.status) == ("npm packages", "npm", UNKNOWN)
    assert row.detail.startswith("web/package.json or web/package-lock.json:")
    assert fetcher.requests == []


@pytest.mark.parametrize(
    ("repository", "notes"),
    [
        ({"url": "git+https://github.com/sveltejs/kit.git"}, "https://github.com/sveltejs/kit/releases"),
        ("git://github.com/o/r.git", "https://github.com/o/r/releases"),
        ("git+ssh://git@github.com/o/r.git", "https://github.com/o/r/releases"),
        ("o/r", "https://github.com/o/r/releases"),
        ("https://gitlab.com/o/r", "https://gitlab.com/o/r"),
        (None, "https://www.npmjs.com/package/p/v/1.0.0"),
        ({"url": 7}, "https://www.npmjs.com/package/p/v/1.0.0"),
    ],
)  # fmt: skip
def test_npm_notes_url_from_repository_spellings(repository: Any, notes: str) -> None:
    """The npm repository spellings become a releases page or the npm page (TOOLS-001).

    Args:
        repository: The manifest's ``repository`` value.
        notes: Expected link.

    Returns:
        None

    Raises:
        AssertionError: If the link differs.
    """
    assert npm_notes_url({"repository": repository}, "p", "1.0.0") == notes


def test_main_reports_node_then_npm_rows(
    tmp_path: Path, fake_fetcher: Any, recorded: Any, capsys: pytest.CaptureFixture[str]
) -> None:
    """``[node]`` and ``[npm]`` are wired into the run, Node first, npm after (ADR-035 #18).

    Args:
        tmp_path: Throwaway repository.
        fake_fetcher: Offline fetcher class.
        recorded: Fixture loader.
        capsys: Output capture.

    Returns:
        None

    Raises:
        AssertionError: If the rows or their order differ.
    """
    _web(tmp_path)
    (tmp_path / "web/.node-version").write_text("24\n", encoding="utf-8")
    index_url = "https://mirror.example/node/index.json"
    config = tmp_path / "check-updates.toml"
    config.write_text(
        f'[node]\nreleases_url = "{index_url}"\nversion_file = "web/.node-version"\n'
        '[npm]\npackage_json = "web/package.json"\nlockfile = "web/package-lock.json"\n',
        encoding="utf-8",
    )
    fetcher = fake_fetcher({index_url: recorded("node-index.json"), UPLOT: {"version": "1.6.31"}})
    argv = ["--config", str(config), "--root", str(tmp_path), "--json"]
    assert check_updates.main(argv, fetcher) == 0
    items = [(row["item"], row["status"]) for row in json.loads(capsys.readouterr().out)["items"]]
    assert items == [
        ("Node (web/.node-version)", CURRENT),
        ("uplot", CURRENT),
        ("@sveltejs/kit", UNKNOWN),  # no recorded manifest: reported, never fatal
        ("vite", UNKNOWN),
    ]
