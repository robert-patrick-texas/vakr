# Copyright 2026 Robert Patrick
# SPDX-License-Identifier: Apache-2.0
"""Tests for upstream release parsers and the shared lookup cache (TOOLS-001).

Each source parser is fed a trimmed recording of the real API (see
``conftest.py``) so the tests prove the tool reads today's documents,
including the traps: python.org's several ``is_latest`` flags and install
manager entries, GitHub backports listed out of order, drafts, SemVer
suffixes, and PyPI pre-releases and yanked files. Lookups go through the
offline ``FakeFetcher``.

Dependencies:
    pytest: Fixtures.
    updates_sources, updates_upstream: Modules under test.

Safety:
    No network: every document comes from ``testdata/`` or the test itself.

Example:
    $ uv run pytest tools/check-updates/test_updates_sources.py
"""

import threading
from collections.abc import Callable
from typing import Any

import pytest
from updates_fetch import FetchError
from updates_sources import (
    github_latest,
    go_latest,
    npm_latest,
    pypi_latest,
    pypi_notes_url,
    python_latest,
)
from updates_upstream import PYTHON_RELEASES_URL, Upstream

Loader = Callable[[str], Any]


def test_python_latest_ignores_is_latest_and_install_manager(recorded: Loader) -> None:
    """The newest stable CPython wins; 2.7.18, the install manager, and 3.16.0a1 do not.

    Args:
        recorded: Fixture loader.

    Returns:
        None

    Raises:
        AssertionError: If another release is chosen (TOOLS-001, TOOLS-003).
    """
    latest = python_latest(recorded("python-releases.json"))
    assert latest.version.text == "3.15.0"
    assert latest.notes_url == "https://docs.python.org/release/3.15.0/whatsnew/changelog.html"


def test_go_latest_uses_stable_releases_only(recorded: Loader) -> None:
    """``go1.28rc1`` (stable: false) is skipped and the ``go`` prefix dropped (TOOLS-001).

    Args:
        recorded: Fixture loader.

    Returns:
        None

    Raises:
        AssertionError: If the release or notes link is wrong.
    """
    latest = go_latest(recorded("go-dl.json"))
    assert latest.version.text == "1.27.3"
    assert latest.notes_url == "https://go.dev/doc/devel/release#go1.27.3"


def test_pypi_latest_skips_prereleases_yanks_and_empty_releases(recorded: Loader) -> None:
    """0.18.0rc1, yanked 0.17.2, and file-less 0.17.3 never count as latest (TOOLS-001).

    Args:
        recorded: Fixture loader.

    Returns:
        None

    Raises:
        AssertionError: If the release or notes link is wrong.
    """
    latest = pypi_latest(recorded("pypi-ruff.json"), "ruff")
    assert latest.version.text == "0.17.1"
    assert latest.notes_url == "https://github.com/astral-sh/ruff/blob/main/CHANGELOG.md"


def test_pypi_latest_falls_back_to_info_version() -> None:
    """Without a per-release listing, PyPI's own latest is used (TOOLS-001).

    Args:
        None

    Returns:
        None

    Raises:
        AssertionError: If the fallback is not used.
    """
    data = {"info": {"version": "2.10.1", "project_urls": None}}
    latest = pypi_latest(data, "pip-audit")
    assert latest.version.text == "2.10.1"
    assert latest.notes_url == "https://pypi.org/project/pip-audit/2.10.1/"


@pytest.mark.parametrize(
    ("urls", "expected"),
    [
        ({"Releases": "https://r", "Changelog": "https://c"}, "https://r"),
        ({"changelog": "https://c"}, "https://c"),
        (
            {"Source": "https://github.com/pypa/pip-audit/"},
            "https://github.com/pypa/pip-audit/releases",
        ),
        ({"Homepage": "https://example.org"}, "https://pypi.org/project/x/1.2/"),
    ],
)
def test_pypi_notes_url_preference(urls: dict[str, str], expected: str) -> None:
    """Release notes prefer releases, then changelogs, then GitHub, then PyPI (TOOLS-001).

    Args:
        urls: ``project_urls`` as published.
        expected: Link to choose.

    Returns:
        None

    Raises:
        AssertionError: If another link is chosen.
    """
    assert pypi_notes_url({"project_urls": urls}, "x", "1.2") == expected


def test_github_latest_prefers_highest_stable_tag(recorded: Loader) -> None:
    """Drafts and pre-releases are skipped; the highest tag wins over newer backports.

    Args:
        recorded: Fixture loader (recorded order plus a draft and a beta).

    Returns:
        None

    Raises:
        AssertionError: If another tag is chosen (TOOLS-001).
    """
    latest = github_latest(recorded("github-checkout.json"), "actions/checkout")
    assert latest.version.text == "v7.0.1"
    assert latest.notes_url == "https://github.com/actions/checkout/releases/tag/v7.0.1"
    pages = github_latest(recorded("github-deploy-pages.json"), "actions/deploy-pages")
    assert pages.version.text == "v5.0.1"  # v3.0.2-node.24 is a variant, not a release
    pbs = github_latest(recorded("github-python-build-standalone.json"), "x/pbs")
    assert pbs.version.text == "20261013"


def test_github_and_npm_failures() -> None:
    """A listing with no stable release, or an npm pre-release tag, is an error (TOOLS-001).

    Args:
        None

    Returns:
        None

    Raises:
        AssertionError: If no error is raised.
    """
    draft = [{"tag_name": "v1.0.0", "draft": True, "prerelease": False}]
    with pytest.raises(FetchError, match="no stable release"):
        github_latest(draft, "o/r")
    with pytest.raises(FetchError, match="npm echarts"):
        npm_latest({"version": "6.0.0-beta.1"}, "echarts")
    latest = npm_latest({"version": "6.0.0"}, "echarts")
    assert latest.notes_url == "https://www.npmjs.com/package/echarts/v/6.0.0"


def test_upstream_fetches_each_key_once_and_caches_failures(fake_fetcher: Any) -> None:
    """Concurrent lookups share one request; failures are cached and re-raised (TOOLS-001).

    Args:
        fake_fetcher: Offline fetcher class.

    Returns:
        None

    Raises:
        AssertionError: If a source is fetched twice.
    """
    fetcher = fake_fetcher({PYTHON_RELEASES_URL: [{"name": "Python 3.15.0"}]})
    upstream = Upstream(fetcher)
    threads = [threading.Thread(target=upstream.python) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert upstream.python().version.text == "3.15.0"
    assert fetcher.requests == [PYTHON_RELEASES_URL]
    for _ in range(2):
        with pytest.raises(FetchError, match="no recorded response"):
            upstream.go()
    assert len(fetcher.requests) == 2


def test_upstream_reports_unexpected_shapes_and_sources(fake_fetcher: Any) -> None:
    """A changed document shape becomes a FetchError; unknown sources are rejected.

    Args:
        fake_fetcher: Offline fetcher class.

    Returns:
        None

    Raises:
        AssertionError: If the error types differ (TOOLS-001).
    """
    fetcher = fake_fetcher({"https://pypi.org/pypi/x/json": {"unexpected": True}})
    upstream = Upstream(fetcher)
    with pytest.raises(FetchError, match="unexpected response"):
        upstream.lookup("pypi", "x")
    with pytest.raises(ValueError, match="unsupported source"):
        upstream.lookup("cargo", "x")
    fetcher.responses["https://registry.npmjs.org/htmx.org/latest"] = {"version": "2.0.7"}
    assert upstream.lookup("npm", "htmx.org").version.text == "2.0.7"
