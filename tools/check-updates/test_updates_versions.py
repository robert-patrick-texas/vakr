# Copyright 2026 Robert Patrick
# SPDX-License-Identifier: Apache-2.0
"""Tests for version parsing, ordering, pre-release handling, and status (TOOLS-001).

A wrong comparison either hides an update (the release checklist then
misses it) or invents one (noise that trains people to ignore the report).
These tests pin down the spellings each source uses, PEP 440 and SemVer
pre-releases, and precision-limited pins such as ``3.15`` and ``v7``.

Dependencies:
    pytest: Parametrized cases.
    updates_versions: Module under test.

Safety:
    Pure functions; no files, network, or processes.

Example:
    $ uv run pytest tools/check-updates/test_updates_versions.py
"""

import pytest
from updates_versions import CURRENT, UNKNOWN, UPDATE, latest_stable, parse_version, status


@pytest.mark.parametrize(
    ("text", "release", "pre", "post", "dev"),
    [
        ("3.15.0", (3, 15, 0), None, None, None),
        ("v7", (7,), None, None, None),
        ("go1.27.3", (1, 27, 3), None, None, None),
        ("go1.28rc1", (1, 28), ("rc", 1), None, None),
        ("python-3.15.0", (3, 15, 0), None, None, None),
        ("3.16.0a1", (3, 16, 0), ("a", 1), None, None),
        ("2.0.0b2", (2, 0, 0), ("b", 2), None, None),
        ("1.0.0-beta.1", (1, 0, 0), ("b", 1), None, None),
        ("1.0.0-rc", (1, 0, 0), ("rc", 0), None, None),
        ("v3.0.2-node.24", (3, 0, 2), ("other", 0), None, None),
        ("1.0.post1", (1, 0), None, 1, None),
        ("1.0.0-1", (1, 0, 0), None, 1, None),
        ("2.1.dev3", (2, 1), None, None, 3),
        ("1.2.3+local.7", (1, 2, 3), None, None, None),
        ("20261013", (20261013,), None, None, None),
    ],
)
def test_parse_version_spellings(
    text: str,
    release: tuple[int, ...],
    pre: tuple[str, int] | None,
    post: int | None,
    dev: int | None,
) -> None:
    """Each ecosystem's spelling parses into the same structure (TOOLS-001).

    Args:
        text: Version as a source writes it.
        release: Expected numeric components.
        pre: Expected pre-release label and number.
        post: Expected post-release number.
        dev: Expected development-release number.

    Returns:
        None

    Raises:
        AssertionError: If parsing differs.
    """
    version = parse_version(text)
    assert version is not None
    assert (version.release, version.pre, version.post, version.dev) == (release, pre, post, dev)
    assert version.text == text


@pytest.mark.parametrize("text", ["", None, "main", "latest", "release-candidate"])
def test_parse_version_rejects_non_versions(text: str | None) -> None:
    """Branch names and empty refs are not versions (TOOLS-001).

    Args:
        text: Non-version input.

    Returns:
        None

    Raises:
        AssertionError: If something parses.
    """
    assert parse_version(text) is None


def test_prerelease_flags() -> None:
    """Alpha, beta, candidate, dev, and SemVer suffixes are pre-releases (TOOLS-001).

    Args:
        None

    Returns:
        None

    Raises:
        AssertionError: If a flag is wrong.
    """
    flagged = {t: parse_version(t) for t in ("3.16.0a1", "2.1.dev3", "v8.0.0-beta.1", "1.0.post1")}
    assert {t: v.is_prerelease for t, v in flagged.items() if v} == {
        "3.16.0a1": True,
        "2.1.dev3": True,
        "v8.0.0-beta.1": True,
        "1.0.post1": False,
    }


def test_sort_order_follows_pep_440() -> None:
    """Releases sort dev < alpha < beta < rc < final < post; 1.0 equals 1.0.0 (TOOLS-001).

    Args:
        None

    Returns:
        None

    Raises:
        AssertionError: If the ordering differs.
    """
    ordered = ["1.0.dev1", "1.0a1", "1.0b1", "1.0rc1", "1.0", "1.0.post1", "1.0.1"]
    parsed = [parse_version(t) for t in ordered]
    keys = [v.sort_key() for v in parsed if v]
    assert keys == sorted(keys)
    assert len(set(keys)) == len(ordered)
    one, one_zero = parse_version("1.0"), parse_version("1.0.0")
    assert one is not None
    assert one_zero is not None
    assert one.sort_key() == one_zero.sort_key()


def test_latest_stable_skips_prereleases_and_garbage() -> None:
    """The newest stable version wins over newer pre-releases and non-versions (TOOLS-001).

    Args:
        None

    Returns:
        None

    Raises:
        AssertionError: If the wrong version is chosen.
    """
    best = latest_stable(["v6.1.0", "v8.0.0-beta.1", "v7.0.1", "nightly", "v7.0.0"])
    assert best is not None
    assert best.text == "v7.0.1"
    assert latest_stable(["1.0a1", "main"]) is None


@pytest.mark.parametrize(
    ("current", "latest", "precision", "expected"),
    [
        ("3.15", "3.15.2", 2, CURRENT),  # .python-version pins the minor series
        ("3.15", "3.16.0", 2, UPDATE),
        ("1.27.0", "1.27.3", 2, CURRENT),  # go directive compared by minor
        ("v7", "v7.0.1", None, CURRENT),  # tag pin compared at written precision
        ("v6", "v7.0.1", None, UPDATE),
        ("v10.3.0", "v10.3.0", None, CURRENT),
        ("v10.2.0", "v10.3.0", None, UPDATE),
        ("3.15.0rc1", "3.15.0", None, UPDATE),  # candidate is behind its final
        ("3.15rc1", "3.15.0", None, UPDATE),
        ("3.16.0a1", "3.15.2", None, CURRENT),  # ahead of the latest stable
        ("1.0", "1.0.post1", None, UPDATE),
        ("1.0.0", "1.0", None, CURRENT),
        ("0.0.89", "0.0.90", None, UPDATE),
        ("20261009", "20261013", None, UPDATE),
    ],
)
def test_status(current: str, latest: str, precision: int | None, expected: str) -> None:
    """Status compares at the pin's precision and handles pre-releases (TOOLS-001, TOOLS-003).

    Args:
        current: Pinned version.
        latest: Latest stable version.
        precision: Forced comparison precision.
        expected: Expected status.

    Returns:
        None

    Raises:
        AssertionError: If the status differs.
    """
    assert status(parse_version(current), parse_version(latest), precision) == expected


def test_status_unknown_when_a_side_is_missing() -> None:
    """Missing current or latest versions give unknown, never current (TOOLS-001).

    Args:
        None

    Returns:
        None

    Raises:
        AssertionError: If a missing side is not unknown.
    """
    assert status(None, parse_version("1.0")) == UNKNOWN
    assert status(parse_version("1.0"), None) == UNKNOWN
