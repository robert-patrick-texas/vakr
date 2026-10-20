"""``vakr db``: metadata database status, migrations, and backups (5.6, 16.3, MIG-001..005).

``vakr db status`` shows the database path, its schema version, the range
this release supports, pending migrations, the applied history with
durations, and ``PRAGMA quick_check``. ``vakr db migrate`` first writes a
verified online backup to ``backups/`` beside the database, then applies
pending migrations; destructive ones also need ``--allow-destructive`` and
``--backup-confirmed`` (MIG-005). ``vakr db backup`` writes a verified online
backup on demand. All three take ``--database`` to point at another file,
such as a backup to check before restoring it.

Dependencies:
    vakr.adapters.sqlite.metadata: The migration manager for ``metadata.db``.
    vakr.paths: The default database location.

Safety:
    ``status`` and ``backup`` never change the database. Exit status: 0 OK;
    1 a problem (no database, pending or failed migrations, a failed
    integrity check, an unreadable or foreign file); 2 a schema newer than
    this release supports (MIG-004), or a usage error.

Example:
    $ vakr db status
    database:  /var/lib/vakr/metadata.db
    schema:    1 (this release runs 1; upgrades from 0)
    state:     current: schema 1 is current
"""

import argparse
import sys
from pathlib import Path

from vakr.adapters.sqlite.metadata import METADATA_FILE, metadata_migrations
from vakr.cli.common import SubParsers
from vakr.domain.errors import StorageError
from vakr.paths import InstallPaths
from vakr.ports.migrations import (
    IntegrityReport,
    MigrationError,
    SchemaState,
    SchemaStatus,
    SchemaTooNewError,
)

EXIT_OK = 0
EXIT_PROBLEM = 1
EXIT_TOO_NEW = 2
BACKUPS_DIR = "backups"
"""Backup directory beside the database: ``/var/lib/vakr/backups``."""


def register(subparsers: SubParsers) -> None:
    """Add the ``db`` command with ``status``, ``migrate``, and ``backup``.

    Args:
        subparsers: The top-level sub-parser collection.

    Returns:
        None

    Raises:
        None
    """
    parser = subparsers.add_parser("db", help="metadata database status, migrations, and backups")
    parser.set_defaults(func=lambda _args: _usage(parser))
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument(
        "--database",
        type=Path,
        metavar="FILE",
        help=f"database file (default: {METADATA_FILE} in /var/lib/vakr, relocated by VAKR_ROOT)",
    )
    actions = parser.add_subparsers(title="subcommands", metavar="SUBCOMMAND")
    status = actions.add_parser("status", parents=[common], help="schema, migrations, integrity")
    status.set_defaults(func=run_status)
    migrate = actions.add_parser("migrate", parents=[common], help="back up, then migrate")
    migrate.add_argument(
        "--allow-destructive", action="store_true", help="allow migrations that remove data"
    )
    migrate.add_argument(
        "--backup-confirmed",
        action="store_true",
        help="confirm the automatic backup is kept where you can restore it",
    )
    migrate.set_defaults(func=run_migrate)
    backup = actions.add_parser("backup", parents=[common], help="write a verified online backup")
    backup.add_argument("--output", type=Path, metavar="DIR", help="directory (default: backups/)")
    backup.set_defaults(func=run_backup)


def _usage(parser: argparse.ArgumentParser) -> int:
    """Print ``vakr db`` help when no subcommand is given.

    Args:
        parser: The ``db`` parser.

    Returns:
        int: 2, the usage-error status.

    Raises:
        None
    """
    parser.print_help(sys.stderr)
    return 2


def _database(args: argparse.Namespace) -> Path:
    """Return ``--database`` or the installed ``metadata.db``.

    Args:
        args: Parsed arguments with ``database``.

    Returns:
        Path: The database file.

    Raises:
        None
    """
    return args.database or InstallPaths.from_environment().state_dir / METADATA_FILE


def _fail(message: str) -> None:
    """Print an error for the operator.

    Args:
        message: What went wrong and what to do.

    Returns:
        None

    Raises:
        None
    """
    print(f"vakr db: {message}", file=sys.stderr)


def run_status(args: argparse.Namespace) -> int:
    """Print the schema status and integrity of the database.

    Args:
        args: Parsed arguments.

    Returns:
        int: 0 current and sound, 2 newer schema, otherwise 1.

    Raises:
        None
    """
    path = _database(args)
    manager = metadata_migrations(path)
    try:
        status = manager.status()
        integrity = None if status.state is SchemaState.MISSING else manager.check_integrity()
    except StorageError as exc:
        _fail(f"cannot read {path}: {exc}")
        return EXIT_PROBLEM
    print("\n".join(render_status(status, integrity)))
    if status.state is SchemaState.TOO_NEW:
        return EXIT_TOO_NEW
    sound = integrity is not None and integrity.ok
    return EXIT_OK if status.state is SchemaState.CURRENT and sound else EXIT_PROBLEM


def render_status(status: SchemaStatus, integrity: IntegrityReport | None) -> list[str]:
    """Format a status report.

    Args:
        status: The schema status.
        integrity: The ``quick_check`` result, or ``None`` when there is no file.

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
    if integrity is None:
        lines.append("integrity: not checked (no database)")
    elif integrity.ok:
        lines.append("integrity: ok (PRAGMA quick_check)")
    else:
        lines.append(f"integrity: FAILED (PRAGMA quick_check): {'; '.join(integrity.problems[:3])}")
    if status.applied:
        lines.append("history:")
    for a in status.applied:
        when = a.applied_at.strftime("%Y-%m-%dT%H:%M:%SZ")
        seconds = a.duration.total_seconds()
        lines.append(
            f"  {a.version:04d} {a.name} applied {when} in {seconds:.3f} s by {a.applied_by}"
        )
    return lines


def run_migrate(args: argparse.Namespace) -> int:
    """Back up the database, then apply pending migrations.

    Args:
        args: Parsed arguments with ``allow_destructive`` and ``backup_confirmed``.

    Returns:
        int: 0 current afterwards, 2 newer schema, otherwise 1.

    Raises:
        None
    """
    path = _database(args)
    manager = metadata_migrations(path)
    try:
        status = manager.status()
        if status.state is SchemaState.CURRENT:
            print(f"{path}: schema {status.version} is current; nothing to migrate")
            return EXIT_OK
        if not status.can_migrate:
            _fail(status.detail)
            return EXIT_TOO_NEW if status.state is SchemaState.TOO_NEW else EXIT_PROBLEM
        # A version-0 database holds no data, so there is nothing to back up.
        backup = manager.backup(path.parent / BACKUPS_DIR) if status.version > 0 else None
        if backup is not None:
            print(f"backup: {backup}")
        confirmed = backup if args.backup_confirmed else None
        applied = manager.migrate(
            allow_destructive=args.allow_destructive, verified_backup=confirmed
        )
    except SchemaTooNewError as exc:
        _fail(str(exc))
        return EXIT_TOO_NEW
    except (MigrationError, StorageError) as exc:
        _fail(str(exc))
        return EXIT_PROBLEM
    for a in applied:
        print(f"applied {a.version:04d} {a.name} in {a.duration.total_seconds():.3f} s")
    print(f"{path}: schema {manager.supported.newest} is current")
    return EXIT_OK


def run_backup(args: argparse.Namespace) -> int:
    """Write a verified online backup and print its path.

    Args:
        args: Parsed arguments with ``output``.

    Returns:
        int: 0 on success, 1 on failure.

    Raises:
        None
    """
    path = _database(args)
    try:
        copy = metadata_migrations(path).backup(args.output or path.parent / BACKUPS_DIR)
    except StorageError as exc:
        _fail(str(exc))
        return EXIT_PROBLEM
    print(copy)
    return EXIT_OK
