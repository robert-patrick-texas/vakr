# Copyright 2026 Robert Patrick
# SPDX-License-Identifier: Apache-2.0
"""Parse and compare version strings from several ecosystems, pre-releases included.

check-updates compares versions that come from very different places:
CPython (``3.15.0``), Go (``go1.27.3``), PyPI (``2.0.0rc1``, ``1.0.post1``),
GitHub release tags (``v7.0.1``, ``v3.0.2-node.24``, ``20261013``), and pins
written at reduced precision (``.python-version`` says ``3.15``, an Actions
tag says ``v7``). This module turns them all into one comparable ``Version``
and decides whether a pinned version is current, behind the latest stable
release, or impossible to judge.

Pre-release rule: PEP 440 markers (``a``, ``b``, ``rc``, ``dev``) and any
SemVer ``-suffix`` make a version a pre-release, so it is never chosen as the
"latest stable". A pinned pre-release is behind the final release it
precedes.

Dependencies:
    re, dataclasses: Standard library only, so the tool runs anywhere uv does.

Safety:
    Pure functions on strings; nothing is executed or fetched here.

Example:
    >>> status(parse_version("3.15"), parse_version("3.15.2"), precision=2)
    'current'
    >>> latest_stable(["v7.0.1", "v8.0.0-beta.1", "v6.1.0"]).text
    'v7.0.1'
"""

import re
import sys
from collections.abc import Iterable
from dataclasses import dataclass

CURRENT = "current"
UPDATE = "update available"
UNKNOWN = "unknown"
# Assigned outside ``status``: HELD by a recorded exception (updates_exceptions),
# REFERENCE to a row whose item the repository does not pin.
HELD = "held"
REFERENCE = "reference"

_VERSION_RE = re.compile(
    r"^\s*(?:v|go|python-?)?(?P<release>\d+(?:\.\d+)*)(?P<suffix>[^+\s]*)(?:\+\S*)?\s*$",
    re.IGNORECASE,
)
_PRE_RE = re.compile(r"^[-_.]?(alpha|beta|preview|pre|rc|a|b|c)[-_.]?(\d*)", re.IGNORECASE)
_POST_RE = re.compile(r"^(?:[-_.]?(?:post|rev|r)[-_.]?(\d*)|-(\d+)(?![.\w]))", re.IGNORECASE)
_DEV_RE = re.compile(r"^[-_.]?dev[-_.]?(\d*)", re.IGNORECASE)
_PRE_LABELS = {"alpha": "a", "a": "a", "beta": "b", "b": "b", "c": "rc", "rc": "rc"}
_PRE_LABELS |= {"pre": "rc", "preview": "rc"}
# "other" marks SemVer suffixes such as "-node.24" that are not PEP 440
# markers; ranking them lowest keeps them from ever beating a final release.
_PRE_RANK = {"other": -1, "a": 0, "b": 1, "rc": 2}
_FINAL_RANK = 3


@dataclass(frozen=True, slots=True)
class Version:
    """One parsed version, ordered the way PEP 440 orders releases.

    Attributes:
        text: The version as written by its source, for display.
        release: Numeric components, such as ``(3, 15, 0)``.
        pre: Pre-release label (``a``, ``b``, ``rc``, ``other``) and number.
        post: Post-release number, if any.
        dev: Development-release number, if any.
    """

    text: str
    release: tuple[int, ...]
    pre: tuple[str, int] | None = None
    post: int | None = None
    dev: int | None = None

    @property
    def is_prerelease(self) -> bool:
        """Report whether this is an alpha, beta, candidate, dev, or SemVer pre-release.

        Args:
            None

        Returns:
            bool: True when the version must not count as a stable release.

        Raises:
            None
        """
        return self.pre is not None or self.dev is not None

    def sort_key(self) -> tuple[tuple[int, ...], tuple[int, int], int, int]:
        """Return a key that sorts versions in release order.

        Trailing zeros are dropped so ``1.0`` equals ``1.0.0``. A bare
        ``.devN`` sorts before any alpha of the same release, as in PEP 440.

        Args:
            None

        Returns:
            tuple: (release, pre-release rank, post number, dev number).

        Raises:
            None
        """
        release = self.release
        while len(release) > 1 and release[-1] == 0:
            release = release[:-1]
        if self.pre is not None:
            pre = (_PRE_RANK[self.pre[0]], self.pre[1])
        elif self.dev is not None and self.post is None:
            pre = (-2, 0)
        else:
            pre = (_FINAL_RANK, 0)
        post = -1 if self.post is None else self.post
        dev = sys.maxsize if self.dev is None else self.dev
        return release, pre, post, dev


def _number(text: str) -> int:
    """Convert an optional digit string to an int, treating empty as zero.

    Args:
        text: Digits or an empty string (``rc`` means ``rc0``).

    Returns:
        int: The number.

    Raises:
        None
    """
    return int(text) if text else 0


def parse_version(text: str | None) -> Version | None:
    """Parse a version string, tolerating ``v``, ``go``, and ``python-`` prefixes.

    Args:
        text: Version text such as ``v10.3.0``, ``go1.27rc1``, or ``2.0.0.post1``.

    Returns:
        Version | None: The parsed version, or None when ``text`` has no
        leading numeric release (branch names, empty strings, None).

    Raises:
        None
    """
    raw = (text or "").strip()
    match = _VERSION_RE.match(raw)
    if match is None:
        return None
    release = tuple(int(part) for part in match["release"].split("."))
    suffix = match["suffix"]
    pre = post = dev = None
    if found := _PRE_RE.match(suffix):
        pre = (_PRE_LABELS[found[1].lower()], _number(found[2]))
        suffix = suffix[found.end() :]
    if found := _POST_RE.match(suffix):
        post = _number(found[1] or found[2] or "")
        suffix = suffix[found.end() :]
    if found := _DEV_RE.match(suffix):
        dev = _number(found[1])
        suffix = suffix[found.end() :]
    if suffix and pre is None:
        pre = ("other", 0)
    return Version(raw, release, pre, post, dev)


def latest_stable(candidates: Iterable[str]) -> Version | None:
    """Pick the highest stable version from version strings, ignoring pre-releases.

    Args:
        candidates: Version strings; unparseable ones are skipped.

    Returns:
        Version | None: The highest stable version, or None if there is none.

    Raises:
        None
    """
    parsed = (parse_version(text) for text in candidates)
    stable = [v for v in parsed if v is not None and not v.is_prerelease]
    return max(stable, key=Version.sort_key, default=None)


def _padded(release: tuple[int, ...], width: int) -> tuple[int, ...]:
    """Cut or zero-pad a release tuple to ``width`` components.

    Args:
        release: Numeric release components.
        width: Number of components to keep.

    Returns:
        tuple[int, ...]: Exactly ``width`` components.

    Raises:
        None
    """
    return (release + (0,) * width)[:width]


def status(current: Version | None, latest: Version | None, precision: int | None = None) -> str:
    """Decide whether ``current`` is up to date with ``latest``.

    A pin is compared only at the precision it is written with, or at
    ``precision`` when given: ``3.15`` is current while 3.15.x is the newest
    series, and ``v7`` is current while 7.y.z is. A pin newer than the latest
    stable release (for example a release candidate of the next version) is
    current. A pre-release pin of the latest stable release is behind it.

    Args:
        current: Version in use, or None when it could not be determined.
        latest: Latest stable upstream version, or None when unavailable.
        precision: Number of release components to compare, overriding the
            precision of ``current``.

    Returns:
        str: ``"current"``, ``"update available"``, or ``"unknown"``.

    Raises:
        None
    """
    if current is None or latest is None:
        return UNKNOWN
    width = precision or len(current.release)
    pinned, upstream = _padded(current.release, width), _padded(latest.release, width)
    if upstream != pinned:
        return UPDATE if upstream > pinned else CURRENT
    coarse = precision is not None or len(current.release) < len(latest.release)
    if coarse:
        return UPDATE if current.is_prerelease else CURRENT
    return UPDATE if latest.sort_key() > current.sort_key() else CURRENT
