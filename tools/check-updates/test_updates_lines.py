# Copyright 2026 Robert Patrick
# SPDX-License-Identifier: Apache-2.0
"""Tests of pins that do not follow a plain "latest": major lines, extra Go pins, reference rows.

Some items are current against something other than the newest release
of their own: Node's type definitions follow the Node line the build runs,
a builder image names its own Go release, and a reference row pins nothing
at all. These tests check each against recorded documents (TOOLS-T09) and
that the new configuration keys are validated (TOOLS-T05).

Dependencies:
    pytest: ``tmp_path`` and parametrisation.
    check_updates, updates_config, updates_sources: Code under test.

Safety:
    Offline: every request is answered by ``FakeFetcher``. Writes only
    beneath ``tmp_path``.

Example:
    $ uv run pytest tools/check-updates/test_updates_lines.py
"""

import json
from pathlib import Path
from typing import Any

import check_updates
import pytest
from updates_config import ConfigError, load_config
from updates_fetch import FetchError
from updates_sources import npm_line_latest
from updates_upstream import GO_RELEASES_URL

TYPES = "https://registry.npmjs.org/@types%2Fnode"
PACKUMENT = {"versions": {v: {} for v in ("24.19.2", "24.20.0-beta.1", "24.9.9", "26.6.5")}}


def test_npm_line_picks_the_newest_stable_release_of_the_major() -> None:
    """The newest 24.x wins over 26.x and over a 24.x pre-release (TOOLS-T09).

    Args:
        None

    Returns:
        None

    Raises:
        AssertionError: If the wrong release is chosen.
    """
    found = npm_line_latest(PACKUMENT, "@types/node", 24)
    assert found.version.text == "24.19.2"
    assert found.notes_url == "https://www.npmjs.com/package/@types/node/v/24.19.2"
    with pytest.raises(FetchError, match=r"npm @types/node 22\.x"):
        npm_line_latest(PACKUMENT, "@types/node", 22)


def _web(root: Path, node_pin: str) -> Path:
    """Write a front end that locks ``@types/node`` 24.9.9 and a config following ``.node-version``.

    Args:
        root: Empty directory.
        node_pin: Content of ``web/.node-version``.

    Returns:
        Path: The config file.

    Raises:
        OSError: If a file cannot be written.
    """
    web = root / "web"
    web.mkdir()
    (web / ".node-version").write_text(node_pin, encoding="utf-8")
    (web / "package.json").write_text('{"devDependencies": {"@types/node": "24.9.9"}}')
    lock = {"packages": {"node_modules/@types/node": {"version": "24.9.9"}}}
    (web / "package-lock.json").write_text(json.dumps(lock))
    config = root / "check-updates.toml"
    config.write_text(
        '[npm]\npackage_json = "web/package.json"\nlockfile = "web/package-lock.json"\n'
        'lines = { "@types/node" = "web/.node-version" }\n',
        encoding="utf-8",
    )
    return config


@pytest.mark.parametrize(
    ("node_pin", "status", "latest", "detail"),
    [
        ("24.22.1\n", "update available", "24.19.2", "follows the 24.x line of web/.node-version"),
        ("v26\n", "update available", "26.6.5", "follows the 26.x line"),
        ("lts/*\n", "unknown", "", "web/.node-version: cannot parse 'lts/*'"),
    ],
)
def test_npm_line_follows_the_version_file(
    tmp_path: Path,
    fake_fetcher: Any,
    capsys: pytest.CaptureFixture[str],
    node_pin: str,
    status: str,
    latest: str,
    detail: str,
) -> None:
    """A ``lines`` package is compared within the pinned major line, never npm latest (TOOLS-T09).

    Args:
        tmp_path: Repository root.
        fake_fetcher: Offline fetcher class.
        capsys: Output capture.
        node_pin: Content of ``web/.node-version``.
        status: Expected row status.
        latest: Expected LATEST column.
        detail: Expected part of the detail.

    Returns:
        None

    Raises:
        AssertionError: If the row differs.
    """
    config = _web(tmp_path, node_pin)
    fetcher = fake_fetcher({TYPES: PACKUMENT})
    argv = ["--config", str(config), "--root", str(tmp_path), "--json"]
    assert check_updates.main(argv, fetcher) == 0
    (row,) = json.loads(capsys.readouterr().out)["items"]
    assert (row["status"], row["latest"]) == (status, latest)
    assert detail in row["detail"]
    assert all(not url.endswith("/latest") for url in fetcher.requests)


def test_go_pins_and_reference_rows(
    tmp_path: Path, fake_fetcher: Any, recorded: Any, capsys: pytest.CaptureFixture[str]
) -> None:
    """A builder image's Go tag is compared with go.dev; an unpinned tool is reference (TOOLS-T09).

    Args:
        tmp_path: Repository root.
        fake_fetcher: Offline fetcher class.
        recorded: Fixture loader.
        capsys: Output capture.

    Returns:
        None

    Raises:
        AssertionError: If a row differs.
    """
    (tmp_path / "Dockerfile").write_text("FROM golang:1.26.10@sha256:abc AS nfpm\n")
    config = tmp_path / "check-updates.toml"
    config.write_text(
        "[go]\npins = [{ file = \"Dockerfile\", regex = 'FROM golang:([0-9.]+)@' },"
        " { file = \"missing\", regex = 'x(1)' }]\n"
        '[[tools]]\nname = "gitleaks"\ngithub = "gitleaks/gitleaks"\n',
        encoding="utf-8",
    )
    gh = "https://api.github.com/repos/gitleaks/gitleaks/releases?per_page=10"
    responses = {GO_RELEASES_URL: recorded("go-dl.json"), gh: [{"tag_name": "v8.30.1"}]}
    argv = ["--config", str(config), "--root", str(tmp_path), "--json"]
    assert check_updates.main(argv, fake_fetcher(responses)) == 0
    rows = json.loads(capsys.readouterr().out)["items"]
    by_item = {row["item"]: row for row in rows}
    pinned = by_item["Go (Dockerfile)"]
    assert (pinned["current"], pinned["latest"], pinned["status"]) == (
        "1.26.10",
        "1.27.3",
        "update available",
    )
    assert by_item["Go (missing)"]["status"] == "unknown"
    reference = by_item["gitleaks"]
    assert (reference["current"], reference["latest"], reference["status"]) == (
        "",
        "v8.30.1",
        "reference",
    )
    assert reference["detail"] == "not pinned in this repository"


@pytest.mark.parametrize(
    ("text", "error"),
    [
        ("[go]\npins = { file = 'a' }\n", "'pins' must be a list of tables"),
        ("[go]\npins = [{ file = 'a' }]\n", "needs exactly 'file' and 'regex'"),
        ("[go]\npins = [{ file = 'a', regex = '(' }]\n", "invalid regex"),
        ("[go]\nversion = '1.27'\n", "unknown key(s) version"),
        ("[npm]\nlines = { '@types/node' = 24 }\n", "'lines' must map package names"),
        ("[exceptions]\npath = 'x'\n", "unknown key(s) path"),
        ("[exceptions]\n", "needs a string 'file'"),
    ],
)
def test_new_keys_are_validated(tmp_path: Path, text: str, error: str) -> None:
    """Misspelt or mistyped pins, lines, and exceptions keys are refused (TOOLS-T05).

    Args:
        tmp_path: Directory for the config.
        text: Config content.
        error: Expected part of the message.

    Returns:
        None

    Raises:
        AssertionError: If the config loads.
    """
    path = tmp_path / "check-updates.toml"
    path.write_text(text, encoding="utf-8")
    with pytest.raises(ConfigError) as caught:
        load_config(path)
    assert error in str(caught.value)
