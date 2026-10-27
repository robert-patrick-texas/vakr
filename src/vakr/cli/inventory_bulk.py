"""``vakr inventory import`` and ``export``: CSV and TOML bulk inventory (META-010, E.38).

``import FILE`` plans a bulk write of API-managed records from a device
CSV, an interface-annotation CSV, or a TOML file in the ``inventory.toml``
format, and changes nothing (CFG-010); ``--apply`` writes it in one
transaction with audit records (SEC-018). An import never touches what
``inventory.toml`` owns and never changes ownership (E.38 decision 1).
``export`` writes stored sites, devices, and annotations with UUIDs and
owners to standard output, in a fixed order, so planning an export
against the same storage shows no changes.

The import file is read before the command switches to the service user,
so ``sudo vakr inventory import ~/devices.csv`` works whatever the
permissions of the operator's home directory.

Exit status: 0 success; 1 the file is invalid, conflicts with storage, or
the change was refused; 2 usage error; 4 the metadata database is
unavailable (``--apply`` and ``export`` need it).

Dependencies:
    vakr.inventory: Import and export.
    vakr.cli.inventory_support: Exit codes, storage, output.

Safety:
    The dry run is read-only. Credential names are checked against the
    protected file; no secret is read or printed.

Example:
    $ sudo vakr inventory import devices.csv
    devices.csv: 2 create, 98 unchanged
"""

import argparse
import sys
from pathlib import Path

from vakr.adapters.sqlite.metadata import sqlite_uow_factory
from vakr.cli.inventory_support import (
    EXIT_INVALID,
    EXIT_OK,
    EXIT_USAGE,
    emit,
    guarded,
    local_actor,
    open_store,
    require_store,
)
from vakr.cli.node_guard import peer_refusal
from vakr.config.loader import load_effective_config
from vakr.inventory.errors import InventoryError
from vakr.inventory.export_data import OWNERS, load_export
from vakr.inventory.export_render import export_csv, export_toml
from vakr.inventory.import_apply import apply_import
from vakr.inventory.import_file import FORMATS, KINDS, parse_import, read_bounded
from vakr.inventory.import_plan import import_snapshot, plan_import
from vakr.inventory.render import plan_dict, plan_lines, warning_lines
from vakr.inventory.settings import checks_for
from vakr.inventory.snapshot import EMPTY
from vakr.inventory.validate import Checks
from vakr.paths import InstallPaths
from vakr.ports.clock import SystemClock

OWNER_NOTE = "note: an import writes API-managed records; new ones are api:import (E.38)"


def register_bulk(actions: argparse._SubParsersAction) -> None:
    """Add ``import`` and ``export`` to ``vakr inventory``.

    Args:
        actions: The ``inventory`` sub-parser collection.

    Returns:
        None

    Raises:
        None
    """
    imp = actions.add_parser("import", help="plan, or --apply, a CSV or TOML bulk import")
    imp.add_argument("path", type=Path, metavar="FILE", help="a .csv or .toml file")
    imp.add_argument("--format", choices=FORMATS, help="default: from the file name")
    imp.add_argument("--kind", choices=KINDS, help="what a CSV holds (default: from its header)")
    imp.add_argument("--apply", action="store_true", help="write the plan (default: dry run)")
    imp.add_argument("--config-dir", type=Path, default=None, help="configuration directory")
    imp.add_argument("--dev", action="store_true", help="development mode: warn about unsafe "
                     "credential-file permissions")  # fmt: skip
    imp.add_argument("--json", action="store_true", help="print JSON instead of text")
    imp.set_defaults(func=run_import)
    exp = actions.add_parser("export", help="write stored devices and annotations as TOML or CSV")
    exp.add_argument("--format", choices=FORMATS, default="toml", help="default: toml")
    exp.add_argument("--kind", choices=KINDS, help="devices or annotations (required for csv)")
    exp.add_argument("--owner", choices=OWNERS, default="all", help="whose records (default: all)")
    exp.add_argument("--unannotated", action="store_true",
                     help="also list interfaces with no annotation (to classify them)")  # fmt: skip
    exp.set_defaults(func=run_export)


def run_import(args: argparse.Namespace) -> int:
    """Read the import file as the invoking user, then plan or apply it.

    Args:
        args: Parsed arguments.

    Returns:
        int: Exit status.

    Raises:
        None
    """
    try:
        args.raw = read_bounded(args.path)
    except InventoryError as exc:
        print("\n".join([*exc.lines, f"{exc.source}: invalid"]), file=sys.stderr)
        return EXIT_INVALID
    return _import(args)


def _checks(args: argparse.Namespace) -> Checks:
    """Build the validation checks from the configuration.

    Args:
        args: ``config_dir`` and ``dev``.

    Returns:
        Checks: Credential names and target guardrails.

    Raises:
        ConfigError: If the configuration is invalid.
        ValueError: If a guardrail setting is invalid.
    """
    paths = InstallPaths.from_environment()
    config = load_effective_config(args.config_dir or paths.config_dir)
    return checks_for(config, paths.secrets_dir, development=args.dev)


@guarded
def _import(args: argparse.Namespace) -> int:
    """Plan the import (dry run) or apply it in one audited transaction.

    Args:
        args: Parsed arguments with ``raw``, the file's bytes.

    Returns:
        int: Exit status.

    Raises:
        InventoryError, StorageUnavailableError, ValueError, DomainError:
            Translated by ``guarded``; a peer's refusal is a ValueError.
    """
    imp, checks = parse_import(args.raw, args.path.name, args.format, args.kind), _checks(args)
    if args.apply:
        if (refusal := peer_refusal()) is not None:
            raise ValueError(refusal)
        store = require_store()
        try:
            plan, found = apply_import(sqlite_uow_factory(store), imp, checks, local_actor(),
                                       SystemClock())  # fmt: skip
        finally:
            store.close()
        done = f"{imp.source}: {'applied' if plan.changes else 'nothing to apply'}"
    else:
        store, snapshot = open_store(required=False), EMPTY
        if store is not None:
            try:
                with sqlite_uow_factory(store)() as uow:
                    snapshot = import_snapshot(uow, imp)
            finally:
                store.close()
        plan, found = plan_import(imp, snapshot, checks)
        done = f"{imp.source}: dry run; nothing changed (apply with --apply)"
        if store is None:
            done = f"note: no metadata database yet; planned against nothing\n{done}"
    note = [OWNER_NOTE] if plan.changes else []
    lines = [*warning_lines(imp.source, found), *note, *plan_lines(plan), done]
    emit(args, lines, plan_dict(plan, found) | {"applied": args.apply and bool(plan.changes)})
    return EXIT_OK


@guarded
def run_export(args: argparse.Namespace) -> int:
    """Write the export to standard output.

    Args:
        args: Parsed arguments.

    Returns:
        int: Exit status.

    Raises:
        StorageUnavailableError, ExportError: Translated by ``guarded``.
    """
    if args.format == "csv" and args.kind is None:
        print("vakr: --format csv needs --kind devices or --kind annotations", file=sys.stderr)
        return EXIT_USAGE
    store = require_store()
    try:
        with sqlite_uow_factory(store)() as uow:
            found = load_export(uow, args.owner, unannotated=args.unannotated)
    finally:
        store.close()
    text = export_toml(found, args.kind) if args.format == "toml" else export_csv(found, args.kind)
    sys.stdout.write(text)
    return EXIT_OK
