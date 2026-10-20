"""Where update packages come from, and checks shared by every update path.

Every install path (newest release, ``--version``, ``--file``, rollback)
needs the same three things: release lookup over HTTPS, signature and
checksum verification that records a refusal, and a verified package of a
given version for rollback, taken from the cache or downloaded by tag.

Dependencies:
    vakr.lifecycle.update.github, cache, verify, engine: Fetch, store,
        verify, and record.

Safety:
    Verification happens before any package is returned for installation.
    Network errors while obtaining a rollback package are reported as
    warnings and never abort an update that is otherwise safe.

Example:
    >>> version_from_name(project, "vakr_0.2.0~rc.1_amd64.deb")  # doctest: +SKIP
    Version(major=0, minor=2, patch=0, rc=1)
"""

import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

from vakr.lifecycle.egress.errors import EgressError
from vakr.lifecycle.update import cache, engine, github
from vakr.lifecycle.update.github import GITHUB_API, HttpGetter
from vakr.lifecycle.update.journal import PRIORITY_ERROR, PRIORITY_WARNING
from vakr.lifecycle.update.model import Outcome, Toolkit
from vakr.lifecycle.update.policy import MANUAL_MARKER
from vakr.lifecycle.update.releases import AssetNames, Release, ReleaseError, asset_names
from vakr.lifecycle.update.state import UpdateState
from vakr.lifecycle.update.verify import Artifacts, ReleaseTrust, VerificationError, verify_release
from vakr.lifecycle.update.versions import Version


@dataclass(frozen=True, slots=True)
class Project:
    """Project-specific parameters (blueprint section 29).

    Attributes:
        repository: GitHub ``owner/name``.
        package: Package name in asset file names.
        arch: Package architecture.
        trust: Release signers.
        api_base: GitHub API root.
        manual_marker: Release-notes text that blocks unattended installs.
    """

    repository: str
    package: str
    arch: str
    trust: ReleaseTrust
    api_base: str = GITHUB_API
    manual_marker: str = MANUAL_MARKER


@dataclass(frozen=True, slots=True)
class Online:
    """Release fetching bound to a client and project.

    Attributes:
        project: Project parameters.
        http: HTTPS client.
    """

    project: Project
    http: HttpGetter

    def get_releases(self) -> list[Release]:
        """Fetch recent releases.

        Args:
            None

        Returns:
            list[Release]: Recent releases.

        Raises:
            ReleaseError: On API errors.
            EgressError: When GitHub cannot be reached.
        """
        return github.fetch_releases(self.http, self.project.repository, self.project.api_base)

    def get_tag(self, version: Version) -> Release:
        """Fetch one release by version tag.

        Args:
            version: Release version.

        Returns:
            Release: The release.

        Raises:
            ReleaseError: When it does not exist.
            EgressError: When GitHub cannot be reached.
        """
        repo, base = self.project.repository, self.project.api_base
        return github.fetch_release_by_tag(self.http, repo, version.tag(), base)


@dataclass(frozen=True, slots=True)
class Sources:
    """Project parameters, collaborators, and the optional HTTPS client.

    Attributes:
        project: Project parameters.
        kit: Side-effecting collaborators.
        http: HTTPS client, or ``None`` when offline.
    """

    project: Project
    kit: Toolkit
    http: HttpGetter | None

    def online(self) -> Online:
        """Return a release fetcher, or fail when offline.

        Args:
            None

        Returns:
            Online: Release fetcher bound to the client.

        Raises:
            ReleaseError: When no HTTPS client was configured.
        """
        if self.http is None:
            raise ReleaseError("no network client configured; use --file for offline installs")
        return Online(self.project, self.http)


def acquire(src: Sources, version: Version, fill: Callable[[Path], Artifacts]) -> Artifacts:
    """Stage release files, verify them in place, then move them into the cache.

    Verifying the very files that will be installed, inside the root-owned
    cache, means nobody can swap them between the check and the install.

    Args:
        src: Project, collaborators, and HTTPS client.
        version: Version the files belong to.
        fill: Writes the three files into the staging directory it is given
            (a download or a copy) and returns their paths.

    Returns:
        Artifacts: Verified files at their cache location.

    Raises:
        VerificationError: When signature or checksum verification fails;
            the staged files are discarded and the cache is unchanged.
        ReleaseError, EgressError, OSError: From ``fill``.
    """
    root = src.kit.layout.cache_root
    with cache.staging(root) as directory:
        staged = fill(directory)
        verify_release(staged, src.project.trust, src.kit.run)
        stored = cache.promote(staged, root, version)
    src.kit.emit("update.verify.ok", f"signature and checksum verified for {version}")
    return stored


def copy_given(files: Artifacts, directory: Path, names: AssetNames) -> Artifacts:
    """Copy operator-supplied files into staging, refusing when any is missing.

    Args:
        files: Paths given with ``--file``, ``--sums``, ``--sig``.
        directory: Staging directory.
        names: Canonical file names.

    Returns:
        Artifacts: The staged copies.

    Raises:
        VerificationError: When a given file does not exist.
        OSError: When copying fails.
    """
    given = (files.package, files.sums, files.signature)
    if missing := [str(p) for p in given if not p.is_file()]:
        raise VerificationError(f"missing file(s): {', '.join(missing)}")
    return cache.copy_in(files, directory, names)


def refuse(src: Sources, exc: VerificationError, target: Version, current: Version) -> Outcome:
    """Record and report a verification refusal (UPD-001, UPD-T02).

    Args:
        src: Project, collaborators, and HTTPS client.
        exc: The verification failure.
        target: Version that was refused.
        current: Installed version.

    Returns:
        Outcome: ``refused``; nothing on the host was changed.

    Raises:
        OSError: When the state file cannot be written.
    """
    message = f"refused {target}: {exc}; nothing was changed"
    src.kit.emit("update.verify.failed", message, PRIORITY_ERROR)
    failed = engine.current_state(src.kit).failed_versions
    versions = (current.semver(), target.semver())
    engine.record(src.kit, UpdateState("refused", "", *versions, message, failed, ""))
    return Outcome("refused", message, *versions)


def rollback_artifacts(
    src: Sources, version: Version, releases: Sequence[Release] | None
) -> Artifacts | None:
    """Get verified files for ``version``: cached, or downloaded by tag.

    Args:
        src: Project, collaborators, and HTTPS client.
        version: Version to obtain.
        releases: Recent releases to search before fetching the tag.

    Returns:
        Artifacts | None: Verified files, or ``None`` (reported as a
        warning) when they cannot be obtained.

    Raises:
        None
    """
    names = asset_names(src.project.package, version, src.project.arch)
    root = src.kit.layout.cache_root
    found = cache.cached(root, version, names)
    try:
        if found is not None:
            verify_release(found, src.project.trust, src.kit.run)
        elif src.http is not None:
            online = src.online()
            match = [r for r in releases or () if r.version == version]
            release = match[0] if match else online.get_tag(version)
            found = acquire(src, version, lambda d: cache.download(release, names, d, online.http))
    except (ReleaseError, EgressError, VerificationError, OSError) as exc:
        src.kit.emit("update.rollback.unavailable", f"{version}: {exc}", PRIORITY_WARNING)
        return None
    if found is None:
        src.kit.emit("update.rollback.unavailable", f"{version} is not cached", PRIORITY_WARNING)
    return found


def previous_version(src: Sources, current: Version, state: UpdateState) -> Version | None:
    """Return the version to roll back to.

    Args:
        src: Project, collaborators, and HTTPS client.
        current: Installed version.
        state: Last update record.

    Returns:
        Version | None: The version updated from (when the record shows a
        successful update to ``current``), else the newest older cached
        version, else ``None``.

    Raises:
        None
    """
    if state.result == "success" and state.to_version == current.semver():
        return Version.parse(state.from_version)
    older = [v for v in cache.cached_versions(src.kit.layout.cache_root) if v < current]
    return older[-1] if older else None


def version_from_name(project: Project, name: str) -> Version | None:
    """Parse the version from ``<package>_<debver>_<arch>.deb``.

    Args:
        project: Project parameters (package name and architecture).
        name: Package file name.

    Returns:
        Version | None: The version, or ``None`` when the name differs.
        GitHub's ``.rc.`` spelling of ``~rc.`` is accepted.

    Raises:
        None
    """
    pattern = rf"{re.escape(project.package)}_(.+)_{re.escape(project.arch)}\.deb"
    match = re.fullmatch(pattern, name)
    try:
        # GitHub may serve ``0.3.0~rc.1`` as ``0.3.0.rc.1``; both name one version.
        return Version.parse(match[1].replace(".rc.", "~rc.")) if match else None
    except ValueError:
        return None
