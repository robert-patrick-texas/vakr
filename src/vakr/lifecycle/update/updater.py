"""High-level self-update operations: check, update, offline install, rollback.

This is the entry point a command-line front end calls. Each changing
operation runs under the update lock (UPD-007), applies the install rules
in ``policy``, verifies artifacts before touching the system (UPD-001),
makes sure a verified package of the running version is available for
rollback, and hands off to the ``engine`` transaction.

Dependencies:
    vakr.lifecycle.update.sources: Release lookup, verification, rollback
        packages.
    vakr.lifecycle.update.engine: The install transaction.

Safety:
    ``http`` may be ``None`` for offline hosts; then only cached packages are
    used. Nothing is installed unless ``confirm`` returns ``True``.

Example:
    >>> updater = SelfUpdater(project, kit, client)  # doctest: +SKIP
    >>> updater.update(Version.parse("0.2.0"), Request(), lambda q: True).status  # doctest: +SKIP
    'updated'
"""

from collections.abc import Sequence

from vakr.lifecycle.update import cache, engine
from vakr.lifecycle.update.console import Confirm
from vakr.lifecycle.update.github import HttpGetter
from vakr.lifecycle.update.lock import exclusive_lock
from vakr.lifecycle.update.model import Outcome, Toolkit
from vakr.lifecycle.update.policy import Request, Skip, gate, no_release
from vakr.lifecycle.update.releases import Release, asset_names, newest, notes_excerpt
from vakr.lifecycle.update.sources import (
    Project,
    Sources,
    acquire,
    copy_given,
    previous_version,
    refuse,
    rollback_artifacts,
    version_from_name,
)
from vakr.lifecycle.update.verify import Artifacts, VerificationError
from vakr.lifecycle.update.versions import Version


class SelfUpdater:
    """Self-update operations for one installed project; single-threaded."""

    def __init__(self, project: Project, kit: Toolkit, http: HttpGetter | None) -> None:
        """Bind parameters, collaborators, and the (optional) HTTPS client.

        Args:
            project: Project parameters.
            kit: Side-effecting collaborators.
            http: HTTPS client, or ``None`` when offline.

        Returns:
            None

        Raises:
            None
        """
        self.project, self.kit = project, kit
        self.sources = Sources(project, kit, http)

    def check(self, current: Version, request: Request) -> Outcome:
        """Report whether an installable release exists, changing nothing.

        Args:
            current: Installed version.
            request: Channel, hold, and target options.

        Returns:
            Outcome: ``available``, ``up-to-date``, or ``skipped``.

        Raises:
            ReleaseError: On API errors (including rate limiting).
            EgressError: When GitHub cannot be reached.
        """
        release, skip = self._select(current, request, self.sources.online().get_releases())
        if skip is not None or release is None:
            reason = skip or no_release(request)
            return Outcome(reason.status, reason.reason, current.semver())
        notes = notes_excerpt(release.body)
        message = f"{release.version} is available (installed {current})"
        return Outcome("available", message, current.semver(), release.version.semver(), notes)

    def update(self, current: Version, request: Request, confirm: Confirm) -> Outcome:
        """Find, download, verify, and install the selected release.

        Args:
            current: Installed version.
            request: Channel, hold, target, and mode options.
            confirm: Asks the operator; returns ``True`` to proceed.

        Returns:
            Outcome: Result of the attempt.

        Raises:
            LockBusyError: When another update is running.
            ReleaseError: On API errors (including rate limiting).
            EgressError: When GitHub cannot be reached.
            OSError: When the cache or backup cannot be written.
        """
        with exclusive_lock(self.kit.layout.lock_file):
            online = self.sources.online()
            releases = online.get_releases()
            release, skip = self._select(current, request, releases)
            if skip is not None or release is None:
                reason = skip or no_release(request)
                return Outcome(reason.status, reason.reason, current.semver())
            target, notes = release.version, notes_excerpt(release.body)
            if not confirm(f"Install {self.project.package} {target} (installed {current})?"):
                return Outcome("cancelled", "cancelled by operator", current.semver())
            self.kit.emit("update.download.start", f"downloading {release.tag}")
            names = asset_names(self.project.package, target, self.project.arch)
            try:
                artifacts = acquire(
                    self.sources, target, lambda d: cache.download(release, names, d, online.http)
                )
            except VerificationError as exc:
                return refuse(self.sources, exc, target, current)
            rollback = rollback_artifacts(self.sources, current, releases)
            return engine.apply_update(self.kit, artifacts, target, current, rollback, notes)

    def install_files(
        self, current: Version, request: Request, files: Artifacts, confirm: Confirm
    ) -> Outcome:
        """Install hand-transferred artifacts after verifying them (UPD-004).

        Args:
            current: Installed version.
            request: Mode options (``if_newer``, ``unattended``, hold).
            files: Package, checksum list, and signature given by path.
            confirm: Asks the operator; returns ``True`` to proceed.

        Returns:
            Outcome: Result of the attempt.

        Raises:
            LockBusyError: When another update is running.
            OSError: When the cache or backup cannot be written.
        """
        with exclusive_lock(self.kit.layout.lock_file):
            target = version_from_name(self.project, files.package.name)
            if target is None:
                expected = f"{self.project.package}_<version>_{self.project.arch}.deb"
                return Outcome("refused", f"package file must be named {expected}")
            failed = engine.current_state(self.kit).failed_versions
            if (skip := gate(target, current, request, failed, explicit=True)) is not None:
                return Outcome(skip.status, skip.reason, current.semver())
            names = asset_names(self.project.package, target, self.project.arch)
            try:
                stored = acquire(self.sources, target, lambda d: copy_given(files, d, names))
            except VerificationError as exc:
                return refuse(self.sources, exc, target, current)
            if not confirm(f"Install {stored.package.name} (installed {current})?"):
                return Outcome("cancelled", "cancelled by operator", current.semver())
            rollback = rollback_artifacts(self.sources, current, None)
            return engine.apply_update(self.kit, stored, target, current, rollback)

    def rollback(self, current: Version, confirm: Confirm) -> Outcome:
        """Reinstall the previous version and restore its backup (UPD-005).

        Args:
            current: Installed version.
            confirm: Asks the operator; returns ``True`` to proceed.

        Returns:
            Outcome: ``rolled-back``, ``cancelled``, or ``failed``.

        Raises:
            LockBusyError: When another update is running.
            OSError: When the safety backup cannot be written.
        """
        with exclusive_lock(self.kit.layout.lock_file):
            previous = previous_version(self.sources, current, engine.current_state(self.kit))
            if previous is None:
                return Outcome("failed", "no previous version is recorded or cached")
            artifacts = rollback_artifacts(self.sources, previous, None)
            if artifacts is None:
                return Outcome("failed", f"no verified package for {previous} is available")
            if not confirm(f"Roll back {self.project.package} {current} -> {previous}?"):
                return Outcome("cancelled", "cancelled by operator", current.semver())
            return engine.manual_rollback(self.kit, artifacts, previous, current)

    def _select(
        self, current: Version, request: Request, releases: Sequence[Release]
    ) -> tuple[Release | None, Skip | None]:
        """Choose the target release and apply the install rules.

        Args:
            current: Installed version.
            request: Operator or timer request.
            releases: Recent releases.

        Returns:
            tuple: The release (or ``None``) and the reason to skip (or ``None``).

        Raises:
            ReleaseError: For an unknown channel or a missing ``--version``.
            EgressError: When fetching a tag outside the recent list fails.
        """
        explicit = request.version is not None
        if request.version is not None:
            wanted = request.version
            match = [r for r in releases if r.version == wanted and not r.draft]
            release = match[0] if match else self.sources.online().get_tag(wanted)
        else:
            release = newest(releases, request.channel, request.hold)
        if release is None:
            return None, None
        failed = engine.current_state(self.kit).failed_versions
        rules = {"explicit": explicit, "notes": release.body, "marker": self.project.manual_marker}
        return release, gate(release.version, current, request, failed, **rules)
