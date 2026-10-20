"""Release records, channel selection, and expected asset names.

Self-update picks the newest release on the configured channel:
``prerelease`` accepts release candidates, ``stable`` only final releases.
Each release must carry the package, ``SHA256SUMS``, and ``SHA256SUMS.sig``
(blueprint 28.7). This module turns GitHub API objects into ``Release``
records and chooses among them; ``github`` does the fetching.

Dependencies:
    vakr.lifecycle.update.versions: Tag parsing and ordering.

Safety:
    Pure data handling. Only HTTPS asset URLs are kept. Nothing here is
    trusted until the signature and checksum are verified (``verify``).

Example:
    >>> asset_names("vakr", Version.parse("0.2.0-rc.1"), "amd64").package
    'vakr_0.2.0~rc.1_amd64.deb'
"""

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from vakr.lifecycle.update.versions import Version

CHANNELS = ("prerelease", "stable")


class ReleaseError(Exception):
    """Release discovery failed; the message is operator-readable."""


class RateLimitError(ReleaseError):
    """GitHub refused the request because the unauthenticated quota is used up."""


@dataclass(frozen=True, slots=True)
class AssetNames:
    """File names a release must provide.

    Attributes:
        package: The installable package, for example ``vakr_0.2.0_amd64.deb``.
        sums: Checksum list.
        signature: SSH signature over the checksum list.
    """

    package: str
    sums: str = "SHA256SUMS"
    signature: str = "SHA256SUMS.sig"


@dataclass(frozen=True, slots=True)
class Release:
    """One published release.

    Attributes:
        version: Version parsed from the tag.
        tag: Git tag, for example ``v0.2.0``.
        prerelease: GitHub's pre-release flag.
        draft: GitHub's draft flag.
        body: Release notes (Markdown).
        assets: Asset name to HTTPS download URL.
    """

    version: Version
    tag: str
    prerelease: bool = False
    draft: bool = False
    body: str = ""
    assets: Mapping[str, str] = field(default_factory=dict)

    def asset_url(self, name: str) -> str | None:
        """Find an asset's download URL, allowing for GitHub's renaming.

        GitHub replaces characters such as ``~`` in uploaded asset names
        with ``.``, so a Debian pre-release package may appear under the
        sanitized name; the local file keeps the canonical name that
        ``SHA256SUMS`` lists.

        Args:
            name: Canonical asset name.

        Returns:
            str | None: Download URL, or ``None`` when absent.

        Raises:
            None
        """
        sanitized = re.sub(r"[^A-Za-z0-9._-]", ".", name)
        return self.assets.get(name) or self.assets.get(sanitized)


def asset_names(package: str, version: Version, arch: str) -> AssetNames:
    """Return the asset names expected for ``package`` at ``version``.

    Args:
        package: Package name, for example ``"vakr"``.
        version: Release version.
        arch: Debian architecture, for example ``"amd64"``.

    Returns:
        AssetNames: Package, checksum, and signature file names.

    Raises:
        None
    """
    return AssetNames(f"{package}_{version.debian()}_{arch}.deb")


def parse_release(item: Any) -> Release | None:
    """Convert one API release object into a ``Release``.

    Args:
        item: Decoded JSON object from the releases API.

    Returns:
        Release | None: The release, or ``None`` when the tag is not a
        release version or the object is malformed.

    Raises:
        None
    """
    if not isinstance(item, dict):
        return None
    try:
        version = Version.parse(str(item.get("tag_name", "")))
    except ValueError:
        return None
    assets = {
        str(a["name"]): str(a["browser_download_url"])
        for a in item.get("assets") or []
        if isinstance(a, dict) and str(a.get("browser_download_url", "")).startswith("https://")
    }
    return Release(
        version=version,
        tag=str(item["tag_name"]),
        prerelease=bool(item.get("prerelease")),
        draft=bool(item.get("draft")),
        body=str(item.get("body") or ""),
        assets=assets,
    )


def newest(
    releases: Sequence[Release], channel: str, ceiling: Version | None = None
) -> Release | None:
    """Pick the newest non-draft release allowed by ``channel`` and ``ceiling``.

    Args:
        releases: Candidates.
        channel: ``"prerelease"`` (release candidates allowed) or ``"stable"``.
        ceiling: Highest acceptable version (``hold_version``), or ``None``.

    Returns:
        Release | None: The newest match, or ``None``.

    Raises:
        ReleaseError: For an unknown channel.
    """
    if channel not in CHANNELS:
        raise ReleaseError(f"unknown update channel {channel!r}; use one of {', '.join(CHANNELS)}")
    allowed = [
        r
        for r in releases
        if not r.draft
        and (channel == "prerelease" or not (r.prerelease or r.version.is_prerelease))
        and (ceiling is None or r.version <= ceiling)
    ]
    return max(allowed, key=_version_of) if allowed else None


def notes_excerpt(body: str, max_lines: int = 12, max_chars: int = 1200) -> str:
    """Shorten release notes for the end-of-update summary.

    Args:
        body: Release notes Markdown.
        max_lines: Maximum non-blank lines kept.
        max_chars: Maximum characters kept.

    Returns:
        str: The excerpt, with ``...`` appended when truncated.

    Raises:
        None
    """
    lines = [line.rstrip() for line in body.strip().splitlines() if line.strip()]
    text = "\n".join(lines[:max_lines])
    truncated = len(lines) > max_lines or len(text) > max_chars
    return text[:max_chars] + ("\n..." if truncated else "")


def _version_of(release: Release) -> Version:
    """Sort key for releases.

    Args:
        release: A release.

    Returns:
        Version: Its version.

    Raises:
        None
    """
    return release.version
