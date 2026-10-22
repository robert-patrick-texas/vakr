"""``vakr db``: status, migrations, and backups of the metadata and telemetry databases (5.6).

``vakr db status`` shows each database's path, schema version, the range
this release supports, pending migrations, the applied history with
durations, and ``PRAGMA quick_check``; for telemetry it also shows the WAL
size and the age of the oldest staged row, and checks only the fixed
tables unless ``--full`` is given (a full check of 40 GB takes minutes).
``vakr db migrate`` first writes a verified online backup of each database
it migrates to ``backups/`` beside it, then applies pending migrations;
destructive ones also need ``--allow-destructive`` and ``--backup-confirmed``
(MIG-005). ``vakr db backup`` writes verified online backups on demand.

Every subcommand acts on both databases, metadata first, unless one is
named (``vakr db status telemetry``). ``--database FILE`` points at another
file, such as a backup to check before restoring it; its kind is read from
the file. The package's postinst runs ``vakr db migrate``, so both
databases are migrated on every install and upgrade.

Dependencies:
    vakr.adapters.sqlite.metadata, telemetry: The migration managers.
    vakr.cli.db_targets: Target selection and status rendering.

Safety:
    ``status`` and ``backup`` never change a database. Run as root, the
    command first switches to the ``vakr`` service user. Exit status: 0 OK;
    1 a problem with any database (missing, pending or failed migrations, a
    failed integrity check, an unreadable or foreign file); otherwise 2 if
    any schema is newer than this release supports (MIG-004); 2 also for a
    usage error.

Example:
    $ vakr db status telemetry
    database:  /var/lib/vakr/telemetry.db
    schema:    1 (this release runs 1; upgrades from 0)
    state:     current: schema 1 is current
"""

import argparse
import sys
import time
from pathlib import Path

from vakr.adapters.sqlite.metadata import metadata_migrations
from vakr.adapters.sqlite.migrations import SqliteMigrationManager
from vakr.adapters.sqlite.telemetry import telemetry_migrations
from vakr.cli.common import SubParsers
from vakr.cli.db_targets import (
    EXIT_OK,
    EXIT_PROBLEM,
    EXIT_TOO_NEW,
    KINDS,
    ManagerFactory,
    Target,
    combined,
    integrity,
    render_status,
    vitals_lines,
)
from vakr.cli.db_targets import targets as resolve_targets
from vakr.cli.service_user import drop_to_service_user
from vakr.domain.errors import StorageError
from vakr.ports.migrations import MigrationError, SchemaState, SchemaTooNewError

BACKUPS_DIR = "backups"
"""Backup directory beside the databases: ``/var/lib/vakr/backups``."""


def register(subparsers: SubParsers) -> None:
    """Add the ``db`` command with ``status``, ``migrate``, and ``backup``.

    Args:
        subparsers: The top-level sub-parser collection.

    Returns:
        None

    Raises:
        None
    """
    parser = subparsers.add_parser("db", help="database status, migrations, and backups")
    parser.set_defaults(func=lambda _args: _usage(parser))
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("which", nargs="?", choices=KINDS, help="one database (default: both)")
    common.add_argument("--database", type=Path, metavar="FILE",
                        help="another database file, such as a backup (default: the installed "
                        "files in /var/lib/vakr, relocated by VAKR_ROOT)")  # fmt: skip
    actions = parser.add_subparsers(title="subcommands", metavar="SUBCOMMAND")
    status = actions.add_parser("status", parents=[common], help="schema, migrations, integrity")
    status.add_argument(
        "--full", action="store_true", help="check every telemetry partition (slow when large)"
    )
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


def _factories() -> dict[str, ManagerFactory]:
    """Return the manager factory of each database kind.

    Args:
        None

    Returns:
        dict[str, ManagerFactory]: Looked up at call time, so tests can
        substitute a later release's migrations.

    Raises:
        None
    """
    return {"metadata": metadata_migrations, "telemetry": telemetry_migrations}


def _targets(args: argparse.Namespace) -> list[tuple[Target, SqliteMigrationManager]]:
    """Drop to the service user, then resolve the databases and their managers.

    Args:
        args: Parsed arguments with ``which`` and ``database``.

    Returns:
        list[tuple[Target, SqliteMigrationManager]]: Metadata first.

    Raises:
        SystemExit: When running as root and the switch to the service user
            fails, so no root-owned database files are ever created.
    """
    if (problem := drop_to_service_user()) is not None:
        _fail(problem)
        raise SystemExit(EXIT_PROBLEM)
    factories = _factories()
    chosen = resolve_targets(args.which, args.database, factories)
    return [(t, factories[t.kind](t.path)) for t in chosen]


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
    """Print the schema status and integrity of each database.

    Args:
        args: Parsed arguments with ``full``.

    Returns:
        int: 0 all current and sound, 2 a newer schema, otherwise 1.

    Raises:
        None
    """
    blocks, codes = [], []
    for target, manager in _targets(args):
        try:
            status = manager.status()
            report, label = (None, "")
            extra: list[str] = []
            if status.state is not SchemaState.MISSING:
                report, label = integrity(target, manager, full=args.full)
            if target.kind == "telemetry" and status.state is SchemaState.CURRENT:
                extra = vitals_lines(target, time.time())
        except StorageError as exc:
            _fail(f"cannot read {target.path}: {exc}")
            codes.append(EXIT_PROBLEM)
            continue
        blocks.append("\n".join([*render_status(status, report, label), *extra]))
        sound = report is not None and report.ok
        current = status.state is SchemaState.CURRENT
        too_new = status.state is SchemaState.TOO_NEW
        codes.append(EXIT_TOO_NEW if too_new else EXIT_OK if current and sound else EXIT_PROBLEM)
    if blocks:
        print("\n\n".join(blocks))
    return combined(codes)


def run_migrate(args: argparse.Namespace) -> int:
    """Back up each database that needs it, then apply pending migrations.

    Args:
        args: Parsed arguments with ``allow_destructive`` and ``backup_confirmed``.

    Returns:
        int: 0 all current afterwards, 2 a newer schema, otherwise 1.

    Raises:
        None
    """
    return combined([_migrate_one(t.path, m, args) for t, m in _targets(args)])


def _migrate_one(path: Path, manager: SqliteMigrationManager, args: argparse.Namespace) -> int:
    """Back up one database if it holds data, then migrate it.

    Args:
        path: The database file.
        manager: Its migration manager.
        args: Parsed arguments with ``allow_destructive`` and ``backup_confirmed``.

    Returns:
        int: 0 current afterwards, 2 newer schema, otherwise 1.

    Raises:
        None
    """
    if not path.parent.is_dir():
        _fail(f"{path.parent} does not exist; the package creates it (for development, create it)")
        return EXIT_PROBLEM
    try:
        status = manager.status()
        if status.state is SchemaState.CURRENT:
            print(f"{path}: {status.detail}; nothing to migrate")  # also a newer, readable one
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
    """Write a verified online backup of each database and print its path.

    Args:
        args: Parsed arguments with ``output``.

    Returns:
        int: 0 on success, 1 if any backup failed.

    Raises:
        None
    """
    codes = []
    for target, manager in _targets(args):
        try:
            copy = manager.backup(args.output or target.path.parent / BACKUPS_DIR)
        except StorageError as exc:
            _fail(str(exc))
            codes.append(EXIT_PROBLEM)
            continue
        print(copy)
        codes.append(EXIT_OK)
    return combined(codes)
