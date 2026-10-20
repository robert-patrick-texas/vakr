"""Value types shared by the updater: layout, hooks, toolkit, and outcome.

The update transaction and the command-line front end exchange these
immutable records. Keeping them separate from the transaction logic lets a
reusing project build its own front end against the same types.

Dependencies:
    vakr.lifecycle.update.process: ``Runner`` and ``RunResult``.

Safety:
    Plain data; no I/O at import or construction.

Example:
    >>> Outcome("updated", "updated 0.2.0 -> 0.3.0").exit_code
    0
"""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Protocol

from vakr.lifecycle.update.process import Runner, RunResult

type Installer = Callable[[Path], RunResult]
SUCCESS_STATUSES = frozenset({"updated", "up-to-date", "skipped", "available", "rolled-back"})


class Emit(Protocol):
    """Event emitter signature (see ``journal.EventSink``)."""

    def __call__(self, event_id: str, message: str, priority: int = 6) -> None:
        """Emit one event.

        Args:
            event_id: Stable event ID.
            message: Human-readable message.
            priority: syslog priority.

        Returns:
            None

        Raises:
            None
        """
        ...


@dataclass(frozen=True, slots=True)
class Layout:
    """Where the updater keeps its files.

    Attributes:
        cache_root: Package cache (``<cache_dir>/updates``).
        backups_dir: Pre-update backups.
        state_file: ``update-state.json``.
        lock_file: Update lock in the runtime directory.
        config_dir: Configuration directory to back up and restore.
        db_dir: Directory whose databases are backed up.
        db_names: Database file names to back up; empty means every ``*.db``.
        keep_packages: Older cached versions kept (E.14: 2).
        keep_backups: Backups kept.
    """

    cache_root: Path
    backups_dir: Path
    state_file: Path
    lock_file: Path
    config_dir: Path
    db_dir: Path
    db_names: tuple[str, ...] = ()
    keep_packages: int = 2
    keep_backups: int = 5


@dataclass(frozen=True, slots=True)
class Hook:
    """One post-install (or post-rollback) command.

    Attributes:
        name: Short name for messages, e.g. ``"config upgrade"``.
        argv: Argument list.
        ok_codes: Exit statuses that count as success.
        skip_codes: Exit statuses meaning "not applicable", logged and ignored.
    """

    name: str
    argv: tuple[str, ...]
    ok_codes: frozenset[int] = frozenset({0})
    skip_codes: frozenset[int] = frozenset()


@dataclass(frozen=True, slots=True)
class Toolkit:
    """Side-effecting collaborators, all injectable for tests.

    Attributes:
        layout: File locations.
        hooks: Post-install hooks, run through the new binary.
        rollback_hooks: Commands run after a rollback (service restart).
        installer: Installs a package file.
        run: Command runner for hooks.
        emit: Event emitter.
        now: Clock returning aware UTC datetimes.
    """

    layout: Layout
    hooks: tuple[Hook, ...]
    rollback_hooks: tuple[Hook, ...]
    installer: Installer
    run: Runner
    emit: Emit
    now: Callable[[], datetime]


@dataclass(frozen=True, slots=True)
class Outcome:
    """Result of a self-update command.

    Attributes:
        status: ``updated``, ``available``, ``up-to-date``, ``skipped``,
            ``rolled-back``, ``cancelled``, ``refused``, or ``failed``.
        message: Operator-readable summary.
        from_version: Installed version before the command.
        to_version: Target version, when there was one.
        notes: Release-notes excerpt for the summary.
    """

    status: str
    message: str
    from_version: str = ""
    to_version: str = ""
    notes: str = ""

    @property
    def exit_code(self) -> int:
        """Return 0 for successful or no-op outcomes and 1 otherwise.

        Args:
            None

        Returns:
            int: Process exit status.

        Raises:
            None
        """
        return 0 if self.status in SUCCESS_STATUSES else 1

    def summary(self, package: str) -> list[str]:
        """Render the end-of-command summary, ending with the notes excerpt.

        Args:
            package: Package name for the heading.

        Returns:
            list[str]: Lines to print.

        Raises:
            None
        """
        lines = [f"{package} self-update: {self.status}: {self.message}"]
        if self.notes:
            lines.append(f"release notes ({self.to_version}):")
            lines += [f"  {line}" for line in self.notes.splitlines()]
        return lines
