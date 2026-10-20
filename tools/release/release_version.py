# Copyright 2026 Robert Patrick
# SPDX-License-Identifier: Apache-2.0
"""Derive every version form a release needs from one SemVer string (VER-002).

A project that keeps its version in a single ``VERSION`` file (VER-001) still
needs that version spelled several ways: Python packaging normalises
``0.2.0-rc.1`` to ``0.2.0rc1`` (PEP 440), Debian and RPM need ``0.2.0~rc.1``
so a release candidate sorts *before* the final release, and git tags use
``v0.2.0-rc.1``. Deriving them mechanically here means no build script ever
hand-edits a version, and package managers order upgrades correctly.

Dependencies:
    re, dataclasses: Standard library only, so packaging scripts can call it
        with any Python 3.15 interpreter, including the bundled one.

Safety:
    Pure functions. Anything that is not ``MAJOR.MINOR.PATCH[-rc.N]`` raises
    ``VersionError`` instead of producing a version a package manager would
    misorder.

Example:
    >>> v = ReleaseVersion.parse("0.2.0-rc.1")
    >>> v.pep440, v.debian, v.tag
    ('0.2.0rc1', '0.2.0~rc.1', 'v0.2.0-rc.1')
"""

import re
from dataclasses import dataclass
from pathlib import Path

_SEMVER = re.compile(r"(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)(?:-rc\.(0|[1-9]\d*))?")

PRERELEASE_RULES = ("rc-or-major-zero", "rc", "always", "never")
"""Accepted values for the release config ``prerelease`` rule."""


class VersionError(ValueError):
    """A version string or prerelease rule is not one this tool accepts."""


@dataclass(frozen=True, slots=True)
class ReleaseVersion:
    """One project version and its derived spellings.

    Attributes:
        major: SemVer major number.
        minor: SemVer minor number.
        patch: SemVer patch number.
        rc: Release-candidate number, or ``None`` for a final release.
    """

    major: int
    minor: int
    patch: int
    rc: int | None = None

    @classmethod
    def parse(cls, text: str) -> ReleaseVersion:
        """Parse ``MAJOR.MINOR.PATCH`` with an optional ``-rc.N`` suffix.

        Args:
            text: Version string; surrounding whitespace is ignored.

        Returns:
            ReleaseVersion: The parsed version.

        Raises:
            VersionError: If ``text`` is not in the accepted form, including
                numbers with leading zeros, which SemVer forbids.
        """
        match = _SEMVER.fullmatch(text.strip())
        if match is None:
            raise VersionError(f"not a MAJOR.MINOR.PATCH[-rc.N] version: {text.strip()!r}")
        major, minor, patch, rc = match.groups()
        return cls(int(major), int(minor), int(patch), None if rc is None else int(rc))

    @property
    def base(self) -> str:
        """Return ``MAJOR.MINOR.PATCH`` without any pre-release suffix.

        Args:
            None

        Returns:
            str: For example ``"0.2.0"``.

        Raises:
            None
        """
        return f"{self.major}.{self.minor}.{self.patch}"

    @property
    def semver(self) -> str:
        """Return the canonical form stored in ``VERSION``.

        Args:
            None

        Returns:
            str: For example ``"0.2.0-rc.1"``.

        Raises:
            None
        """
        return self.base if self.rc is None else f"{self.base}-rc.{self.rc}"

    @property
    def pep440(self) -> str:
        """Return the PEP 440 form that Python package metadata reports.

        Args:
            None

        Returns:
            str: For example ``"0.2.0rc1"``.

        Raises:
            None
        """
        return self.base if self.rc is None else f"{self.base}rc{self.rc}"

    @property
    def debian(self) -> str:
        """Return the Debian/RPM form, where ``~`` sorts before the final release.

        Args:
            None

        Returns:
            str: For example ``"0.2.0~rc.1"``.

        Raises:
            None
        """
        return self.base if self.rc is None else f"{self.base}~rc.{self.rc}"

    @property
    def tag(self) -> str:
        """Return the git tag name for this release.

        Args:
            None

        Returns:
            str: For example ``"v0.2.0-rc.1"``.

        Raises:
            None
        """
        return f"v{self.semver}"

    def next_patch(self) -> ReleaseVersion:
        """Return the next patch release, used to build an N+1 test package.

        Args:
            None

        Returns:
            ReleaseVersion: ``MAJOR.MINOR.(PATCH+1)`` with no suffix, which
            sorts after this version in every scheme.

        Raises:
            None
        """
        return ReleaseVersion(self.major, self.minor, self.patch + 1)

    def is_prerelease(self, rule: str) -> bool:
        """Decide whether a GitHub release of this version is a pre-release.

        Args:
            rule: One of ``PRERELEASE_RULES``. ``rc-or-major-zero`` (the MVP
                rule) marks release candidates and every ``0.x`` release.

        Returns:
            bool: ``True`` when the release should carry ``--prerelease``.

        Raises:
            VersionError: If ``rule`` is unknown.
        """
        if rule not in PRERELEASE_RULES:
            raise VersionError(f"unknown prerelease rule {rule!r}; use one of {PRERELEASE_RULES}")
        is_rc = self.rc is not None
        return {
            "rc-or-major-zero": is_rc or self.major == 0,
            "rc": is_rc,
            "always": True,
            "never": False,
        }[rule]

    def render(self, form: str) -> str:
        """Return the version in a named form, as used by ``--print-version``.

        Args:
            form: ``semver``, ``pep440``, ``deb``, ``tag``, or ``next-patch``.

        Returns:
            str: The requested spelling.

        Raises:
            VersionError: If ``form`` is unknown.
        """
        forms = {
            "semver": self.semver,
            "pep440": self.pep440,
            "deb": self.debian,
            "tag": self.tag,
            "next-patch": self.next_patch().semver,
        }
        if form not in forms:
            raise VersionError(f"unknown version form {form!r}; use one of {tuple(forms)}")
        return forms[form]


VERSION_FORMS = ("semver", "pep440", "deb", "tag", "next-patch")
"""Forms accepted by ``ReleaseVersion.render``."""


def read_version_file(path: Path) -> ReleaseVersion:
    """Read and parse a ``VERSION`` file.

    Args:
        path: File holding one version string and an optional newline.

    Returns:
        ReleaseVersion: The parsed version.

    Raises:
        OSError: If the file cannot be read.
        VersionError: If its content is not a valid version.
    """
    return ReleaseVersion.parse(path.read_text(encoding="utf-8"))
