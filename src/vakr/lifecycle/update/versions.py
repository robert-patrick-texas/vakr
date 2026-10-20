"""Parse and compare release versions in their semver, PEP 440, and Debian forms.

The one authored version is semver ``X.Y.Z[-rc.N]`` (VER-002). Python
metadata reports it in PEP 440 form (``X.Y.ZrcN``), the ``.deb`` uses the
Debian form (``X.Y.Z~rc.N``, which sorts before the final release in dpkg),
and release tags are ``v<semver>``. The updater meets all four and must
compare them consistently: a release candidate is older than its final
release, and newer than the previous final.

Dependencies:
    re, functools: Parsing and total ordering.

Safety:
    Pure functions; anything that is not one of the documented forms is
    rejected rather than guessed at.

Example:
    >>> Version.parse("v7.2.0-rc.1") < Version.parse("7.2.0") < Version.parse("7.2.1~rc.1")
    True
    >>> Version.parse("7.2.0~rc.3").tag()
    'v7.2.0-rc.3'
"""

import functools
import re
from dataclasses import dataclass

_PATTERN = re.compile(
    r"^v?(?P<major>0|[1-9]\d*)\.(?P<minor>0|[1-9]\d*)\.(?P<patch>0|[1-9]\d*)"
    r"(?:(?:-rc\.|rc|~rc\.)(?P<rc>0|[1-9]\d*))?"
    # A PEP 440 local label (``0.0.0+unknown`` from an unbuilt checkout) does
    # not change ordering between releases, so it is accepted and dropped.
    r"(?:\+[0-9A-Za-z.]+)?$"
)


@functools.total_ordering
@dataclass(frozen=True, slots=True)
class Version:
    """A release version, ordered so release candidates precede the final.

    Attributes:
        major: Major version.
        minor: Minor version.
        patch: Patch version.
        rc: Release-candidate number, or ``None`` for a final release.
    """

    major: int
    minor: int
    patch: int
    rc: int | None = None

    @classmethod
    def parse(cls, text: str) -> Version:
        """Parse any supported spelling: semver, tag, PEP 440, or Debian.

        Args:
            text: For example ``"7.2.0-rc.1"``, ``"v7.2.0-rc.1"``,
                ``"7.2.0rc1"``, or ``"7.2.0~rc.1"``.

        Returns:
            Version: The parsed version.

        Raises:
            ValueError: When ``text`` is not a supported version form.
        """
        match = _PATTERN.fullmatch(text.strip())
        if match is None:
            raise ValueError(f"not a release version: {text!r}")
        rc = match["rc"]
        return cls(int(match["major"]), int(match["minor"]), int(match["patch"]), _int(rc))

    @property
    def is_prerelease(self) -> bool:
        """Whether this is a release candidate.

        Args:
            None

        Returns:
            bool: ``True`` when ``rc`` is set.

        Raises:
            None
        """
        return self.rc is not None

    def _key(self) -> tuple[int, int, int, int, int]:
        """Return the sort key: release candidates sort before the final.

        Args:
            None

        Returns:
            tuple: ``(major, minor, patch, is_final, rc)``.

        Raises:
            None
        """
        final = 1 if self.rc is None else 0
        return (self.major, self.minor, self.patch, final, self.rc or 0)

    def __lt__(self, other: object) -> bool:
        """Order versions by release precedence.

        Args:
            other: Another ``Version``.

        Returns:
            bool: Whether ``self`` is older than ``other``.

        Raises:
            TypeError: Via ``NotImplemented`` for non-``Version`` operands.
        """
        if not isinstance(other, Version):
            return NotImplemented
        return self._key() < other._key()

    def semver(self) -> str:
        """Render the authored ``VERSION`` form.

        Args:
            None

        Returns:
            str: ``X.Y.Z`` or ``X.Y.Z-rc.N``.

        Raises:
            None
        """
        base = f"{self.major}.{self.minor}.{self.patch}"
        return base if self.rc is None else f"{base}-rc.{self.rc}"

    def pep440(self) -> str:
        """Render the Python packaging form.

        Args:
            None

        Returns:
            str: ``X.Y.Z`` or ``X.Y.ZrcN``.

        Raises:
            None
        """
        base = f"{self.major}.{self.minor}.{self.patch}"
        return base if self.rc is None else f"{base}rc{self.rc}"

    def debian(self) -> str:
        """Render the Debian package form.

        Args:
            None

        Returns:
            str: ``X.Y.Z`` or ``X.Y.Z~rc.N``.

        Raises:
            None
        """
        base = f"{self.major}.{self.minor}.{self.patch}"
        return base if self.rc is None else f"{base}~rc.{self.rc}"

    def tag(self) -> str:
        """Render the git tag that names this release.

        Args:
            None

        Returns:
            str: ``v`` followed by the semver form.

        Raises:
            None
        """
        return f"v{self.semver()}"

    def __str__(self) -> str:
        """Render the semver form.

        Args:
            None

        Returns:
            str: Same as ``semver()``.

        Raises:
            None
        """
        return self.semver()


def _int(text: str | None) -> int | None:
    """Convert an optional regex group to ``int``.

    Args:
        text: Matched digits, or ``None``.

    Returns:
        int | None: The number, or ``None``.

    Raises:
        None
    """
    return None if text is None else int(text)
