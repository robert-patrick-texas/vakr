"""Which databases a ``vakr db`` command acts on, and how their status is shown (5.6, 16.3).

A node has two SQLite databases in its state directory: ``metadata.db``
(inventory, policies, audit) and ``telemetry.db`` (observations and
rollups). ``vakr db`` acts on both by default, metadata first, or on the
one named. ``--database FILE`` points at another file, such as a backup;
its kind is read from the file header (``PRAGMA application_id``), so a
telemetry backup is checked as telemetry without naming it.

Dependencies:
    vakr.adapters.sqlite.metadata, telemetry: Migration managers and health.
    vakr.paths: The state directory.

Safety:
    Read-only helpers; nothing here migrates or writes.

Example:
    >>> KINDS
    ('metadata', 'telemetry')
"""

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from vakr.adapters.sqlite.metadata import METADATA_FILE
from vakr.adapters.sqlite.migrations import SqliteMigrationManager
from vakr.adapters.sqlite.telemetry import TELEMETRY_FILE
from vakr.adapters.sqlite.telemetry.health import FIXED_TABLES, measure
from vakr.domain.errors import StorageError
from vakr.paths import InstallPaths
from vakr.ports.migrations import IntegrityReport, SchemaState, SchemaStatus

EXIT_OK = 0
EXIT_PROBLEM = 1
EXIT_TOO_NEW = 2
KINDS = ("metadata", "telemetry")
FILES = {"metadata": METADATA_FILE, "telemetry": TELEMETRY_FILE}

type ManagerFactory = Callable[[Path], SqliteMigrationManager]


@dataclass(frozen=True, slots=True)
class Target:
    """One database a command acts on.

    Attributes:
        kind: ``metadata`` or ``telemetry``.
        path: The file.
    """

    kind: str
    path: Path


def targets(
    kind: str | None, database: Path | None, factories: dict[str, ManagerFactory]
) -> list[Target]:
    """Resolve the databases a command acts on.

    Args:
        kind: The database named on the command line, or ``None`` for all.
        database: ``--database FILE``, or ``None`` for the installed files.
        factories: Manager factory per kind, used to recognise a file's kind.

    Returns:
        list[Target]: Metadata first, then telemetry.

    Raises:
        None
    """
    if database is not None:
        return [Target(kind or detect(database, factories), database)]
    state = InstallPaths.from_environment().state_dir
    return [Target(k, state / FILES[k]) for k in KINDS if kind in {None, k}]


def detect(path: Path, factories: dict[str, ManagerFactory]) -> str:
    """Return the kind of an existing database file, defaulting to metadata.

    Args:
        path: A database file.
        factories: Manager factory per kind.

    Returns:
        str: The first kind whose family recognises the file, else ``metadata``.

    Raises:
        None
    """
    for kind in KINDS:
        try:
            if factories[kind](path).status().state not in {SchemaState.MISMATCH,
                                                             SchemaState.MISSING}:  # fmt: skip
                return kind
        except StorageError:
            continue
    return "metadata"


def integrity(
    target: Target, manager: SqliteMigrationManager, *, full: bool
) -> tuple[IntegrityReport, str]:
    """Check a database: fully, or for telemetry just its fixed tables unless ``full``.

    A full ``quick_check`` of a 40 GB telemetry file takes minutes, so the
    default checks the dictionaries, catalog, and staging tables only.

    Args:
        target: The database.
        manager: Its migration manager.
        full: Check every partition too.

    Returns:
        tuple[IntegrityReport, str]: The result and a label for what was checked.

    Raises:
        StorageError: If the file cannot be read.
    """
    if target.kind == "metadata" or full:
        return manager.check_integrity(), "PRAGMA quick_check"
    problems = measure(target.path, 0.0).problems
    label = f"PRAGMA quick_check of the {len(FIXED_TABLES)} fixed tables; --full checks all"
    return IntegrityReport(not problems, problems), label


def vitals_lines(target: Target, now_s: float) -> list[str]:
    """Return the telemetry WAL and staging lines of a status report.

    Args:
        target: A current telemetry database.
        now_s: Current Unix time in seconds.

    Returns:
        list[str]: Output lines.

    Raises:
        StorageError: If the file cannot be read.
    """
    v = measure(target.path, now_s)
    return [f"wal:       {v.wal_bytes / 2**20:.1f} MiB",
            f"staging:   oldest staged row {v.mover_lag_s:.0f} s old"]  # fmt: skip


def render_status(status: SchemaStatus, report: IntegrityReport | None, label: str) -> list[str]:
    """Format a status report.

    Args:
        status: The schema status.
        report: The integrity result, or ``None`` when there is no file.
        label: What the integrity check covered.

    Returns:
        list[str]: Output lines.

    Raises:
        None
    """
    supported = status.supported
    lines = [
        f"database:  {status.location}",
        f"schema:    {status.version} (this release runs {supported.newest}; "
        f"upgrades from {supported.oldest})",
        f"state:     {status.state.value}: {status.detail}",
        "pending:   none" if not status.pending else "pending:",
    ]
    for m in status.pending:
        flag = " [destructive]" if m.destructive else ""
        lines.append(f"  {m.version:04d} {m.name}{flag}: {m.description}")
    if report is None:
        lines.append("integrity: not checked (no database)")
    elif report.ok:
        lines.append(f"integrity: ok ({label})")
    else:
        lines.append(f"integrity: FAILED ({label}): {'; '.join(report.problems[:3])}")
    if status.applied:
        lines.append("history:")
    for a in status.applied:
        when = a.applied_at.strftime("%Y-%m-%dT%H:%M:%SZ")
        seconds = a.duration.total_seconds()
        lines.append(
            f"  {a.version:04d} {a.name} applied {when} in {seconds:.3f} s by {a.applied_by}"
        )
    return lines


def combined(codes: list[int]) -> int:
    """Combine per-database exit statuses: any problem, else any newer schema, else OK.

    Args:
        codes: One status per database.

    Returns:
        int: The command's exit status.

    Raises:
        None
    """
    if EXIT_PROBLEM in codes:
        return EXIT_PROBLEM
    return EXIT_TOO_NEW if EXIT_TOO_NEW in codes else EXIT_OK
