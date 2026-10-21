# Copyright 2026 Robert Patrick
# SPDX-License-Identifier: Apache-2.0
"""Parse each upstream release listing into its latest stable release and notes.

Each supported source publishes releases differently. python.org mixes
CPython with "Python install manager" entries and flags several releases
``is_latest``; go.dev lists the current and previous stable series; PyPI
keeps pre-releases and yanked files beside finals; GitHub tags may carry
SemVer suffixes or be dates (python-build-standalone); npm names its
newest stable version with the ``latest`` dist-tag. The parsers here reduce
each decoded document to one ``Latest``. They never fetch anything, so
tests feed them recorded responses.

Dependencies:
    updates_versions: Version parsing and pre-release filtering.

Safety:
    Documents are treated as data: values are read, never executed, and
    missing fields raise ``FetchError`` or a lookup error that the caller
    turns into an "unknown" row.

Example:
    >>> python_latest([{"name": "Python 3.15.0", "pre_release": False,
    ...                 "release_notes_url": "https://docs.python.org/r"}]).version.text
    '3.15.0'
"""

import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from updates_fetch import FetchError
from updates_versions import Version, latest_stable

SOURCES = ("pypi", "github", "npm")
_CPYTHON_NAME = re.compile(r"^Python (\d+\.\d+\.\d+\S*)$")
# Preferred project_urls labels for release notes, best first.
_NOTES_LABELS = ("releases", "release notes", "changelog", "changes", "history", "news")
# npm's repository shorthand: "github:OWNER/REPO" or a bare "OWNER/REPO".
_NPM_SHORTHAND = re.compile(r"^(?:github:)?([\w.-]+/[\w.-]+)$")


@dataclass(frozen=True, slots=True)
class Latest:
    """The newest stable upstream release of one item.

    Attributes:
        version: Parsed version, keeping the upstream spelling in ``text``.
        notes_url: Where to read what changed in that release.
    """

    version: Version
    notes_url: str


def _require(version: Version | None, what: str) -> Version:
    """Return ``version`` or fail when a source listed no stable release.

    Args:
        version: Result of ``latest_stable``.
        what: Source description for the error message.

    Returns:
        Version: The same version, now known to be present.

    Raises:
        FetchError: If ``version`` is None.
    """
    if version is None:
        raise FetchError(f"{what}: no stable release found")
    return version


def python_latest(data: Any) -> Latest:
    """Pick the newest stable CPython from the python.org release API.

    ``is_latest`` is ignored because python.org sets it on one release per
    product line (2.7.18, the current 3.x, the install manager).

    Args:
        data: Decoded list from ``/api/v2/downloads/release/``.

    Returns:
        Latest: Newest CPython release and its changelog URL.

    Raises:
        FetchError: If the list has no stable CPython release.
    """
    notes: dict[str, str] = {}
    for entry in data:
        match = _CPYTHON_NAME.match(entry.get("name", ""))
        if match and not entry.get("pre_release") and entry.get("is_published", True):
            notes[match[1]] = entry.get("release_notes_url") or "https://www.python.org/downloads/"
    best = _require(latest_stable(notes), "python.org")
    return Latest(best, notes[best.text])


def go_latest(data: Any) -> Latest:
    """Pick the newest stable Go release from ``go.dev/dl/?mode=json``.

    Args:
        data: Decoded list of ``{"version": "go1.27.3", "stable": true}``.

    Returns:
        Latest: Newest Go release without its ``go`` prefix, with notes URL.

    Raises:
        FetchError: If no stable release is listed.
    """
    tags = [e["version"].removeprefix("go") for e in data if e.get("stable")]
    best = _require(latest_stable(tags), "go.dev")
    return Latest(best, f"https://go.dev/doc/devel/release#go{best.text}")


def pypi_notes_url(info: Mapping[str, Any], name: str, version: str) -> str:
    """Choose the best release-notes link a PyPI project advertises.

    Args:
        info: The ``info`` table of the PyPI JSON API document.
        name: Project name, used for the PyPI fallback link.
        version: Release the link should describe.

    Returns:
        str: A releases or changelog URL, the GitHub releases page of the
        source repository, or the PyPI release page.

    Raises:
        None
    """
    urls = {label.lower(): url for label, url in (info.get("project_urls") or {}).items()}
    for label in _NOTES_LABELS:
        if label in urls:
            return urls[label]
    for label in ("source", "repository", "homepage"):
        url = urls.get(label, "")
        if url.startswith("https://github.com/"):
            return url.rstrip("/") + "/releases"
    return f"https://pypi.org/project/{name}/{version}/"


def pypi_latest(data: Any, name: str) -> Latest:
    """Pick the newest stable, non-yanked release from the PyPI JSON API.

    Args:
        data: Decoded ``https://pypi.org/pypi/NAME/json`` document.
        name: Project name.

    Returns:
        Latest: Newest stable release and its release-notes URL.

    Raises:
        FetchError: If no stable release exists.
    """
    releases: Mapping[str, list[Mapping[str, Any]]] = data.get("releases") or {}
    usable = [v for v, files in releases.items() if any(not f.get("yanked") for f in files)]
    # PyPI's own "latest" (info.version) also skips pre-releases and yanks; it
    # is the fallback if the deprecated per-release listing ever disappears.
    best = latest_stable(usable) or latest_stable([data["info"]["version"]])
    best = _require(best, f"PyPI {name}")
    return Latest(best, pypi_notes_url(data["info"], name, best.text))


def github_latest(data: Any, repo: str) -> Latest:
    """Pick the highest stable release from a GitHub releases listing.

    Highest version rather than newest date, because projects publish
    backports (v6.1.0 after v7.0.1) that GitHub may list first.

    Args:
        data: Decoded ``repos/OWNER/REPO/releases`` list.
        repo: ``OWNER/REPO``, used for the fallback notes link.

    Returns:
        Latest: Highest non-draft, non-pre-release tag and its release page.

    Raises:
        FetchError: If no stable release is listed.
    """
    pages = {
        r["tag_name"]: r.get("html_url")
        or f"https://github.com/{repo}/releases/tag/{r['tag_name']}"
        for r in data
        if not r.get("draft") and not r.get("prerelease")
    }
    best = _require(latest_stable(pages), f"GitHub {repo}")
    return Latest(best, pages[best.text])


def npm_notes_url(manifest: Mapping[str, Any], name: str, version: str) -> str:
    """Choose a release-notes link from an npm manifest's ``repository`` field.

    npm writes repositories in several spellings: ``git+https://...git``,
    ``git://``, ``github:OWNER/REPO``, or a bare ``OWNER/REPO``. GitHub
    repositories link to their releases page, other HTTPS repositories to
    themselves, and anything else to the npm page of that version.

    Args:
        manifest: Decoded ``https://registry.npmjs.org/NAME/latest`` manifest.
        name: Package name, for the npm fallback link.
        version: Release the link should describe.

    Returns:
        str: A GitHub releases page, a repository URL, or the npm page.

    Raises:
        None
    """
    repo = manifest.get("repository")
    url = repo.get("url", "") if isinstance(repo, Mapping) else repo
    url = url.strip() if isinstance(url, str) else ""
    if match := _NPM_SHORTHAND.match(url):
        url = f"https://github.com/{match[1]}"
    url = re.sub(r"^git\+", "", url)
    url = re.sub(r"^(?:git|ssh)://(?:git@)?", "https://", url)
    url = url.removesuffix("/").removesuffix(".git")
    if url.startswith("https://github.com/"):
        return "/".join(url.split("/")[:5]) + "/releases"
    if url.startswith("https://"):
        return url
    return f"https://www.npmjs.com/package/{name}/v/{version}"


def npm_latest(data: Any, name: str) -> Latest:
    """Read the version behind npm's ``latest`` dist-tag (TOOLS-001, ADR-035 #18).

    Args:
        data: Decoded ``https://registry.npmjs.org/NAME/latest`` manifest.
        name: Package name.

    Returns:
        Latest: That version and its release notes (``npm_notes_url``).

    Raises:
        FetchError: If the tagged version is missing or a pre-release.
    """
    best = _require(latest_stable([data.get("version", "")]), f"npm {name}")
    return Latest(best, npm_notes_url(data, name, best.text))
