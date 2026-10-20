"""The install transaction: backup, install, post-install hooks, rollback.

Given verified artifacts, this module changes the system in the order
UPD-002 requires and undoes it when anything fails (UPD-005, UPD-006):
the previous package is reinstalled, configuration and databases are
restored from the pre-update backup, services are restarted, and the failed
version is recorded so the timer will not retry it.

Dependencies:
    vakr.lifecycle.update.backup, cache, state: Durable side effects.
    vakr.lifecycle.update.model: Toolkit, hooks, and outcome types.

Safety:
    Callers must pass only verified artifacts. Every step and its result is
    emitted as an event; every outcome is recorded in the state file before
    returning, so ``check health`` sees it even if the process is killed
    afterwards.

Example:
    >>> outcome = apply_update(kit, artifacts, target, current, rollback, notes)  # doctest: +SKIP
    >>> outcome.status  # doctest: +SKIP
    'updated'
"""

import tarfile
from dataclasses import replace
from pathlib import Path

from vakr.lifecycle.update import backup as backups
from vakr.lifecycle.update import cache
from vakr.lifecycle.update.journal import PRIORITY_ERROR, PRIORITY_INFO, PRIORITY_WARNING
from vakr.lifecycle.update.model import Hook, Outcome, Toolkit
from vakr.lifecycle.update.state import StateError, UpdateState, load_state, save_state
from vakr.lifecycle.update.verify import Artifacts
from vakr.lifecycle.update.versions import Version


def current_state(kit: Toolkit) -> UpdateState:
    """Load the state file, treating a corrupt one as empty.

    Args:
        kit: Toolkit with the state file location.

    Returns:
        UpdateState: Recorded or default state.

    Raises:
        None
    """
    try:
        return load_state(kit.layout.state_file)
    except StateError as exc:
        kit.emit("update.state.unreadable", f"{exc}; starting a new record", PRIORITY_WARNING)
        return UpdateState()


def record(kit: Toolkit, update: UpdateState) -> None:
    """Persist ``update`` stamped with the current time.

    Args:
        kit: Toolkit with the state file and clock.
        update: Complete new record (result, message, versions, failed
            versions, backup).

    Returns:
        None

    Raises:
        OSError: When the state file cannot be written.
    """
    stamp = kit.now().strftime("%Y-%m-%dT%H:%M:%SZ")
    save_state(kit.layout.state_file, replace(update, at=stamp))


def run_hooks(kit: Toolkit, hooks: tuple[Hook, ...]) -> str | None:
    """Run hooks in order, stopping at the first failure.

    Args:
        kit: Toolkit with the runner and emitter.
        hooks: Hooks to run.

    Returns:
        str | None: Failure description, or ``None`` when all passed.

    Raises:
        None
    """
    for hook in hooks:
        result = kit.run(hook.argv)
        if result.returncode in hook.skip_codes:
            kit.emit(
                "update.hook.skipped", f"{hook.name}: not available (exit {result.returncode})"
            )
        elif result.returncode in hook.ok_codes:
            kit.emit("update.hook.ok", f"{hook.name}: ok")
        else:
            detail = result.tail(5)
            return f"{hook.name} failed (exit {result.returncode}){': ' + detail if detail else ''}"
    return None


def apply_update(
    kit: Toolkit,
    artifacts: Artifacts,
    target: Version,
    current: Version,
    rollback: Artifacts | None,
    notes: str = "",
) -> Outcome:
    """Back up, install, and run hooks; roll back and record on failure.

    Args:
        kit: Side-effecting collaborators.
        artifacts: Verified release files.
        target: Version being installed.
        current: Version installed now.
        rollback: Verified previous-version files, or ``None`` if unavailable.
        notes: Release-notes excerpt for the summary.

    Returns:
        Outcome: ``updated`` or ``failed``.

    Raises:
        OSError: When the backup cannot be written (nothing changed yet).
    """
    state, layout = current_state(kit), kit.layout
    archive = backups.create_backup(
        layout.config_dir, layout.db_dir, layout.backups_dir, kit.now(), names=layout.db_names
    )
    backups.prune_backups(layout.backups_dir, layout.keep_backups)
    kit.emit("update.backup.done", f"backup written to {archive}")
    kit.emit("update.install.start", f"installing {artifacts.package.name}")
    result = kit.installer(artifacts.package)
    failure = None
    if result.returncode != 0:
        failure = f"package install failed (exit {result.returncode}): {result.tail(5)}"
    failure = failure or run_hooks(kit, kit.hooks)
    base = UpdateState(
        from_version=current.semver(), to_version=target.semver(), backup=str(archive)
    )
    if failure is not None:
        kit.emit("update.failed", failure, PRIORITY_ERROR)
        _, undone = rollback_to(kit, rollback, archive)
        failed = tuple(sorted({*state.failed_versions, target.semver()}))
        message = f"{failure}; rollback: {undone}"
        record(kit, replace(base, result="failed", message=message, failed_versions=failed))
        return Outcome("failed", message, current.semver(), target.semver(), notes)
    remaining = tuple(v for v in state.failed_versions if v != target.semver())
    message = f"updated {current} -> {target}"
    record(kit, replace(base, result="success", message=message, failed_versions=remaining))
    cache.prune(layout.cache_root, target, layout.keep_packages)
    kit.emit("update.done", message)
    return Outcome("updated", message, current.semver(), target.semver(), notes)


def rollback_to(kit: Toolkit, rollback: Artifacts | None, archive: Path | None) -> tuple[bool, str]:
    """Reinstall the previous package, restore the backup, restart services.

    Args:
        kit: Side-effecting collaborators.
        rollback: Verified previous package files, or ``None``.
        archive: Backup to restore, or ``None``.

    Returns:
        tuple[bool, str]: Whether every step succeeded, and a summary.

    Raises:
        None
    """
    ok, notes = True, []
    if rollback is None:
        ok = False
        notes.append("no verified previous package available, package not reinstalled")
    elif (code := kit.installer(rollback.package).returncode) != 0:
        ok = False
        notes.append(f"reinstalling {rollback.package.name} failed (exit {code})")
    else:
        notes.append(f"reinstalled {rollback.package.name}")
    if archive is not None:
        try:
            backups.restore_backup(archive, kit.layout.config_dir, kit.layout.db_dir)
            notes.append(f"restored {archive.name}")
        except (OSError, tarfile.TarError) as exc:
            ok = False
            notes.append(f"restoring {archive.name} failed: {exc}")
    if (hook_failure := run_hooks(kit, kit.rollback_hooks)) is not None:
        ok = False
        notes.append(hook_failure)
    summary = "; ".join(notes)
    event = "update.rollback.done" if ok else "update.rollback.incomplete"
    kit.emit(event, summary, PRIORITY_INFO if ok else PRIORITY_ERROR)
    return ok, summary


def manual_rollback(
    kit: Toolkit, rollback: Artifacts, target: Version, current: Version
) -> Outcome:
    """Return to ``target`` on operator request (``--rollback``, UPD-005).

    The newest pre-update backup is restored; the configuration being
    replaced is first saved as a ``pre-rollback`` backup.

    Args:
        kit: Side-effecting collaborators.
        rollback: Verified files of the version to return to.
        target: Version to return to.
        current: Version installed now.

    Returns:
        Outcome: ``rolled-back`` or ``failed``.

    Raises:
        OSError: When the safety backup cannot be written (nothing changed).
    """
    state, layout = current_state(kit), kit.layout
    archives = backups.list_backups(layout.backups_dir)
    safety = backups.create_backup(
        layout.config_dir,
        layout.db_dir,
        layout.backups_dir,
        kit.now(),
        prefix="pre-rollback",
        names=layout.db_names,
    )
    backups.prune_backups(layout.backups_dir, layout.keep_backups, prefix="pre-rollback")
    kit.emit("update.rollback.start", f"rolling back {current} -> {target}; saved {safety.name}")
    ok, summary = rollback_to(kit, rollback, archives[-1] if archives else None)
    status = "rolled-back" if ok else "failed"
    versions = (current.semver(), target.semver())
    record(kit, UpdateState(status, "", *versions, summary, state.failed_versions, str(safety)))
    return Outcome(status, summary, current.semver(), target.semver())
