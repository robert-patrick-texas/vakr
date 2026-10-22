# Copyright 2026 Robert Patrick
# SPDX-License-Identifier: Apache-2.0
"""Tests for the VERSION-to-package version mapping (VER-001, VER-002).

A wrong mapping breaks upgrades silently: if ``7.3.0-rc.1`` became the
Debian version ``7.3.0-rc.1``, dpkg would sort it *after* ``7.3.0`` and the
final release would never install over the candidate. These tests check the
spellings and, where the tools exist, check ordering with ``dpkg`` itself
and with the ``packaging`` library that pip and uv follow.

Dependencies:
    pytest: Test runner.
    packaging: PEP 440 reference implementation (a pytest dependency).
    release_version: Module under test.

Safety:
    Pure computation plus read-only ``dpkg --compare-versions`` calls.

Example:
    $ uv run pytest tools/release/test_release_version.py
"""

import shutil
import subprocess
from pathlib import Path

import pytest
from packaging.version import Version
from release_version import ReleaseVersion, VersionError, read_version_file


@pytest.mark.parametrize(
    ("text", "semver", "pep440", "debian", "tag"),
    [
        ("1.2.3", "1.2.3", "1.2.3", "1.2.3", "v1.2.3"),
        ("7.3.0-rc.1\n", "7.3.0-rc.1", "7.3.0rc1", "7.3.0~rc.1", "v7.3.0-rc.1"),
        (" 10.20.30-rc.0 ", "10.20.30-rc.0", "10.20.30rc0", "10.20.30~rc.0", "v10.20.30-rc.0"),
    ],
)
def test_forms_derive_mechanically(
    text: str, semver: str, pep440: str, debian: str, tag: str
) -> None:
    """VER-002: each spelling follows from the VERSION string alone.

    Args:
        text: Content as it might appear in ``VERSION``.
        semver: Expected canonical form.
        pep440: Expected Python packaging form.
        debian: Expected Debian/RPM form.
        tag: Expected git tag.

    Returns:
        None

    Raises:
        AssertionError: If a derived form differs.
    """
    version = ReleaseVersion.parse(text)
    assert (version.semver, version.pep440, version.debian, version.tag) == (
        semver,
        pep440,
        debian,
        tag,
    )
    # pip and uv normalise the same way, so `vakr --version` matches pep440.
    assert str(Version(version.semver)) == version.pep440


@pytest.mark.parametrize(
    "text", ["", "1.2", "1.2.3.4", "01.2.3", "1.2.3-beta.1", "1.2.3-rc1", "v1.2.3", "1.2.3-rc.01"]
)
def test_rejects_versions_outside_the_accepted_form(text: str) -> None:
    """VER-002: anything but MAJOR.MINOR.PATCH[-rc.N] is refused.

    Args:
        text: Invalid version string.

    Returns:
        None

    Raises:
        AssertionError: If the string is accepted.
    """
    with pytest.raises(VersionError):
        ReleaseVersion.parse(text)


@pytest.mark.skipif(shutil.which("dpkg") is None, reason="dpkg not installed")
@pytest.mark.parametrize(
    ("older", "newer"),
    [
        ("7.3.0-rc.1", "7.3.0"),
        ("7.3.0-rc.1", "7.3.0-rc.2"),
        ("7.3.0-rc.9", "7.3.0-rc.10"),
        ("1.2.3", "1.2.4"),
        ("1.9.0", "1.10.0"),
    ],
)
def test_debian_versions_sort_like_semver(older: str, newer: str) -> None:
    """VER-002: dpkg orders the mapped versions as SemVer does.

    Args:
        older: Earlier version.
        newer: Later version.

    Returns:
        None

    Raises:
        AssertionError: If dpkg orders them the other way.
    """
    old, new = ReleaseVersion.parse(older).debian, ReleaseVersion.parse(newer).debian
    result = subprocess.run(["dpkg", "--compare-versions", old, "lt", new], check=False)  # noqa: S603, S607
    assert result.returncode == 0, f"dpkg does not sort {old} before {new}"
    assert Version(ReleaseVersion.parse(older).pep440) < Version(ReleaseVersion.parse(newer).pep440)


def test_next_patch_is_newer_and_final() -> None:
    """The N+1 test version sorts after N, including after a candidate.

    Args:
        None

    Returns:
        None

    Raises:
        AssertionError: If the bump is wrong.
    """
    assert ReleaseVersion.parse("1.2.3").next_patch().semver == "1.2.4"
    assert ReleaseVersion.parse("7.3.0-rc.1").render("next-patch") == "7.3.1"


@pytest.mark.parametrize(
    ("text", "rule", "expected"),
    [
        ("0.9.0", "rc-or-major-zero", True),
        ("1.0.0-rc.2", "rc-or-major-zero", True),
        ("1.0.0", "rc-or-major-zero", False),
        ("0.9.0", "rc", False),
        ("1.0.0-rc.2", "rc", True),
        ("1.0.0", "always", True),
        ("0.9.0-rc.1", "never", False),
    ],
)
def test_prerelease_rules(text: str, rule: str, expected: bool) -> None:
    """Section 28.7: -rc and 0.x releases are pre-releases under the MVP rule.

    Args:
        text: Version string.
        rule: Configured prerelease rule.
        expected: Whether the release is marked pre-release.

    Returns:
        None

    Raises:
        AssertionError: If the rule gives the wrong answer.
    """
    assert ReleaseVersion.parse(text).is_prerelease(rule) is expected


def test_unknown_rule_and_form_are_errors() -> None:
    """A typo in a rule or form fails loudly instead of guessing.

    Args:
        None

    Returns:
        None

    Raises:
        AssertionError: If either typo is accepted.
    """
    version = ReleaseVersion.parse("1.2.3")
    with pytest.raises(VersionError, match="prerelease rule"):
        version.is_prerelease("sometimes")
    with pytest.raises(VersionError, match="version form"):
        version.render("rpm")


def test_read_version_file(tmp_path: Path) -> None:
    """VER-001: the VERSION file is read and parsed, newline and all.

    Args:
        tmp_path: Per-test temporary directory.

    Returns:
        None

    Raises:
        AssertionError: If the file is misread.
    """
    path = tmp_path / "VERSION"
    path.write_text("3.4.5-rc.6\n", encoding="utf-8")
    assert read_version_file(path) == ReleaseVersion(3, 4, 5, 6)
