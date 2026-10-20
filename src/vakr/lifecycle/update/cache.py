"""Package cache: one directory per version, kept for rollback and pruned.

Each release's package, ``SHA256SUMS``, and signature are stored together
in ``<cache>/<semver>/`` so a cached package can be re-verified before it is
reinstalled, and so a rollback works without network access. New files are
first written to a private staging directory inside the cache, verified
there, and only then moved into place, so a file is never verified in one
place and installed from another. After a successful update the cache keeps
the installed version plus the two most recent older versions (Appendix
E.14) and deletes the rest.

Dependencies:
    vakr.lifecycle.update.github, releases: Asset lookup and download.
    vakr.lifecycle.update.verify: ``Artifacts`` type.

Safety:
    Downloads are size-bounded and land under the canonical asset names
    that ``SHA256SUMS`` lists. Hand-transferred files are copied into the
    root-owned staging directory before verification, closing the window in
    which someone with write access to their original location could swap
    them. Pruning removes only version directories and stale staging
    directories, so nothing else in the cache directory is touched.

Example:
    >>> cached(cache_root, Version.parse("0.2.0"), names)  # doctest: +SKIP
    Artifacts(package=PosixPath('.../0.2.0/vakr_0.2.0_amd64.deb'), ...)
"""

import shutil
import tempfile
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path

from vakr.lifecycle.update.github import HttpGetter
from vakr.lifecycle.update.releases import AssetNames, Release, ReleaseError
from vakr.lifecycle.update.verify import Artifacts
from vakr.lifecycle.update.versions import Version

MAX_PACKAGE_BYTES = 512 * 1024 * 1024
MAX_SMALL_BYTES = 1024 * 1024
DOWNLOAD_HEADERS = {"Accept": "application/octet-stream"}
STAGING_PREFIX = ".incoming-"


def version_dir(cache_root: Path, version: Version) -> Path:
    """Return the cache directory for ``version``.

    Args:
        cache_root: Root of the update cache.
        version: Release version.

    Returns:
        Path: ``<cache_root>/<semver>``.

    Raises:
        None
    """
    return cache_root / version.semver()


def artifacts_in(directory: Path, names: AssetNames) -> Artifacts:
    """Name the three release files inside ``directory``.

    Args:
        directory: A version directory.
        names: Expected file names.

    Returns:
        Artifacts: Paths, which may not exist yet.

    Raises:
        None
    """
    return Artifacts(directory / names.package, directory / names.sums, directory / names.signature)


def cached(cache_root: Path, version: Version, names: AssetNames) -> Artifacts | None:
    """Return the cached files for ``version`` when all three are present.

    Args:
        cache_root: Root of the update cache.
        version: Release version.
        names: Expected file names.

    Returns:
        Artifacts | None: The files, or ``None`` when any is missing.

    Raises:
        None
    """
    found = artifacts_in(version_dir(cache_root, version), names)
    return found if all(p.is_file() for p in _triple(found)) else None


@contextmanager
def staging(cache_root: Path) -> Generator[Path]:
    """Provide a private, empty staging directory inside the cache.

    Args:
        cache_root: Root of the update cache; created if missing.

    Returns:
        Generator[Path]: The directory, removed with anything left in it
        when the block exits.

    Raises:
        OSError: When the directory cannot be created.
    """
    cache_root.mkdir(parents=True, exist_ok=True)
    directory = Path(tempfile.mkdtemp(dir=cache_root, prefix=STAGING_PREFIX))
    try:
        yield directory
    finally:
        shutil.rmtree(directory, ignore_errors=True)


def download(release: Release, names: AssetNames, directory: Path, http: HttpGetter) -> Artifacts:
    """Download a release's three files into ``directory`` (a staging area).

    Args:
        release: Release to fetch.
        names: Expected asset names.
        directory: Destination directory.
        http: HTTPS client.

    Returns:
        Artifacts: Downloaded (not yet verified) files.

    Raises:
        ReleaseError: When the release lacks an expected asset.
        EgressError: On download failure or oversize files.
        OSError: When the files cannot be written.
    """
    target = artifacts_in(directory, names)
    plan = (
        (names.package, target.package, MAX_PACKAGE_BYTES),
        (names.sums, target.sums, MAX_SMALL_BYTES),
        (names.signature, target.signature, MAX_SMALL_BYTES),
    )
    for name, dest, limit in plan:
        url = release.asset_url(name)
        if url is None:
            raise ReleaseError(f"release {release.tag} has no asset {name}")
        http.download(url, dest, limit, DOWNLOAD_HEADERS)
    return target


def copy_in(source: Artifacts, directory: Path, names: AssetNames) -> Artifacts:
    """Copy hand-transferred artifacts into ``directory`` under canonical names.

    Args:
        source: Files given with ``--file``, ``--sums``, ``--sig``.
        directory: Staging directory.
        names: Canonical file names.

    Returns:
        Artifacts: The copies, which are what gets verified and installed.

    Raises:
        OSError: When a source cannot be read or a copy written.
    """
    target = artifacts_in(directory, names)
    for src, dest in zip(_triple(source), _triple(target), strict=True):
        shutil.copyfile(src, dest)
    return target


def promote(staged: Artifacts, cache_root: Path, version: Version) -> Artifacts:
    """Move verified staged files into the version's cache directory.

    Args:
        staged: Verified files in a staging directory on the same filesystem.
        cache_root: Root of the update cache.
        version: Version the files belong to.

    Returns:
        Artifacts: The files at their cache location.

    Raises:
        OSError: When the files cannot be moved.
    """
    directory = version_dir(cache_root, version)
    directory.mkdir(parents=True, exist_ok=True)
    moved = [src.replace(directory / src.name) for src in _triple(staged)]
    return Artifacts(*moved)


def _triple(files: Artifacts) -> tuple[Path, Path, Path]:
    """Return the package, sums, and signature paths in a fixed order.

    Args:
        files: Release files.

    Returns:
        tuple[Path, Path, Path]: Package, checksum list, signature.

    Raises:
        None
    """
    return (files.package, files.sums, files.signature)


def cached_versions(cache_root: Path) -> list[Version]:
    """List versions that have a cache directory, oldest first.

    Args:
        cache_root: Root of the update cache.

    Returns:
        list[Version]: Sorted versions; non-version entries are ignored.

    Raises:
        None
    """
    if not cache_root.is_dir():
        return []
    found: list[Version] = []
    for entry in cache_root.iterdir():
        try:
            version = Version.parse(entry.name)
        except ValueError:
            continue
        if entry.is_dir():
            found.append(version)
    return sorted(found)


def prune(cache_root: Path, installed: Version, keep_previous: int = 2) -> list[Version]:
    """Keep ``installed`` and the newest ``keep_previous`` older versions.

    Args:
        cache_root: Root of the update cache.
        installed: The version now running.
        keep_previous: Older versions retained for rollback (E.14: 2).

    Returns:
        list[Version]: Versions whose directories were removed.

    Raises:
        OSError: When a directory cannot be removed.
    """
    versions = cached_versions(cache_root)
    older = [v for v in versions if v < installed]
    keep = {installed, *older[-keep_previous:]} if keep_previous > 0 else {installed}
    removed = [v for v in versions if v not in keep]
    for version in removed:
        shutil.rmtree(version_dir(cache_root, version))
    # Staging directories left behind by an interrupted run hold nothing useful.
    for leftover in cache_root.glob(f"{STAGING_PREFIX}*"):
        shutil.rmtree(leftover, ignore_errors=True)
    return removed
