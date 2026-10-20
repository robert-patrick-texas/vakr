"""``vakr inventory validate|plan|apply|list``: the file-managed inventory (META-008, META-010).

NetOps keeps devices, sites, and interface annotations in
``/etc/vakr/inventory.toml``. ``validate`` checks the file and its relation
to storage; ``plan`` shows exactly what ``apply`` would change, without
changing anything (CFG-010); ``apply`` makes those changes in one
transaction with audit records (SEC-018); ``list`` shows stored devices and
who manages them. Devices removed from the file are disabled, never
deleted (11.2).

Exit status: 0 success; 1 the file is invalid, conflicts with storage
(CONF-008), or the change was refused; 2 usage error; 4 the metadata
database is unavailable (missing for ``apply``/``list``, locked, or at
another schema; ``vakr db status`` explains).

Dependencies:
    vakr.inventory: Parsing, validation, plan, apply, and rendering.
    vakr.adapters.sqlite.metadata: The installed metadata database.

Safety:
    ``validate`` and ``plan`` are read-only. Credential *names* are checked
    against the protected file; no secret is read or printed.

Example:
    $ sudo vakr inventory plan
    inventory.toml: 2 create, 1 update
      create device edge1.example.net: 192.0.2.10 at site dal1
"""

import argparse
import sys
from pathlib import Path

from vakr.adapters.sqlite.metadata import sqlite_uow_factory
from vakr.cli.common import SubParsers
from vakr.cli.inventory_support import (
    EXIT_INVALID,
    EXIT_OK,
    EXIT_USAGE,
    emit,
    findings_for,
    guarded,
    load_file,
    local_actor,
    open_store,
    require_store,
)
from vakr.inventory.apply import apply_inventory
from vakr.inventory.errors import InventoryError, render_issues
from vakr.inventory.plan import make_plan
from vakr.inventory.reads import all_devices, device_interfaces
from vakr.inventory.render import plan_dict, plan_lines, warning_lines
from vakr.inventory.snapshot import EMPTY, load_snapshot
from vakr.inventory.validate import validate
from vakr.ports.clock import SystemClock


def register(subparsers: SubParsers) -> None:
    """Add ``inventory`` with ``validate``, ``plan``, ``apply``, and ``list``.

    Args:
        subparsers: The top-level sub-parser collection.

    Returns:
        None

    Raises:
        None
    """
    parser = subparsers.add_parser("inventory", help="validate, plan, and apply inventory.toml")
    parser.set_defaults(func=lambda _args: _usage(parser))
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument(
        "--config-dir",
        type=Path,
        default=None,
        help="configuration directory (default: /etc/vakr, relocated by VAKR_ROOT)",
    )
    common.add_argument(
        "--file",
        type=Path,
        default=None,
        metavar="FILE",
        help="inventory file (default: inventory.file, else inventory.toml there)",
    )
    common.add_argument(
        "--dev",
        action="store_true",
        help="development mode: warn about unsafe credential-file permissions",
    )
    actions = parser.add_subparsers(title="subcommands", metavar="SUBCOMMAND")
    commands = (
        ("validate", run_validate, "check the file and its conflicts"),
        ("plan", run_plan, "show what apply would change (dry run)"),
        ("apply", run_apply, "apply the file to storage, with audit"),
    )
    for name, func, text in commands:
        action = actions.add_parser(name, parents=[common], help=text)
        action.add_argument("--json", action="store_true", help="print JSON instead of text")
        action.set_defaults(func=func)
    listing = actions.add_parser("list", help="list stored devices and who manages them")
    listing.add_argument("--json", action="store_true", help="print JSON instead of text")
    listing.add_argument("--all", action="store_true", help="include retired devices")
    listing.set_defaults(func=run_list)


def _usage(parser: argparse.ArgumentParser) -> int:
    """Print ``vakr inventory`` help when no subcommand is given.

    Args:
        parser: The ``inventory`` parser.

    Returns:
        int: 2, the usage-error status.

    Raises:
        None
    """
    parser.print_help(sys.stderr)
    return EXIT_USAGE


@guarded
def run_validate(args: argparse.Namespace) -> int:
    """Validate the file, against storage when the database exists (CONF-008).

    Args:
        args: Parsed arguments.

    Returns:
        int: Exit status.

    Raises:
        InventoryError, Unavailable: Translated by ``guarded``.
    """
    inv, path, checks = load_file(args)
    if inv is None:
        emit(
            args, [f"{path}: not found; nothing to validate"], {"source": str(path), "found": False}
        )
        return EXIT_OK
    found, note = findings_for(inv, checks)
    if found.problems:
        raise InventoryError(inv.source, found.problems)
    lines = [*warning_lines(inv.source, found), *note, f"{inv.source}: valid "
             f"({len(inv.sites)} site(s), {len(inv.devices)} device(s), "
             f"{len(inv.annotations)} annotation(s))"]  # fmt: skip
    emit(args, lines, {"source": inv.source, "found": True, "valid": True,
                        "warnings": render_issues(inv.source, found.warnings)})  # fmt: skip
    return EXIT_OK


@guarded
def run_plan(args: argparse.Namespace) -> int:
    """Print what ``apply`` would change; change nothing (CFG-010).

    Args:
        args: Parsed arguments.

    Returns:
        int: Exit status.

    Raises:
        InventoryError, Unavailable: Translated by ``guarded``.
    """
    inv, path, checks = load_file(args)
    if inv is None:
        emit(args, [f"{path}: not found; nothing to plan"], {"source": str(path), "found": False})
        return EXIT_OK
    store = open_store(required=False)
    snapshot = EMPTY
    if store is not None:
        try:
            with sqlite_uow_factory(store)() as uow:
                snapshot = load_snapshot(uow, inv)
        finally:
            store.close()
    found = validate(inv, snapshot, checks)
    if found.problems:
        raise InventoryError(inv.source, found.problems)
    plan = make_plan(inv, snapshot)
    note = [] if store else ["note: no metadata database yet; everything would be created"]
    emit(
        args, [*warning_lines(inv.source, found), *note, *plan_lines(plan)], plan_dict(plan, found)
    )
    return EXIT_OK


@guarded
def run_apply(args: argparse.Namespace) -> int:
    """Apply the file in one transaction, with audit (META-008, SEC-018).

    Args:
        args: Parsed arguments.

    Returns:
        int: Exit status.

    Raises:
        InventoryError, Unavailable, DomainError: Translated by ``guarded``.
    """
    inv, path, checks = load_file(args)
    if inv is None:
        # Refusing protects against a mistyped path disabling every file-managed device.
        print(f"vakr: {path} not found; nothing applied (use an empty file to disable all)",
              file=sys.stderr)  # fmt: skip
        return EXIT_INVALID
    store = require_store()
    try:
        plan, found = apply_inventory(sqlite_uow_factory(store), inv, checks, local_actor(),
                                      SystemClock())  # fmt: skip
    finally:
        store.close()
    applied = "applied" if plan.changes else "nothing to apply"
    lines = [*warning_lines(inv.source, found), *plan_lines(plan), f"{inv.source}: {applied}"]
    emit(args, lines, plan_dict(plan, found) | {"applied": bool(plan.changes)})
    return EXIT_OK


@guarded
def run_list(args: argparse.Namespace) -> int:
    """List stored devices with state, owner, and interface count.

    Args:
        args: Parsed arguments with ``json`` and ``all``.

    Returns:
        int: Exit status.

    Raises:
        Unavailable: Translated by ``guarded``.
    """
    store = require_store()
    rows: list[tuple[str, str, str, str, int, str]] = []
    try:
        with sqlite_uow_factory(store)() as uow:
            for d in all_devices(uow, include_retired=args.all):
                count = len(device_interfaces(uow, d.id))
                rows.append((d.name, str(d.id), d.poll_address, d.admin_state.value, count,
                             str(d.managed_by)))  # fmt: skip
    finally:
        store.close()
    width = max((len(r[0]) for r in rows), default=4)
    lines = [f"{'NAME':<{width}}  {'ADDRESS':<39}  {'STATE':<11}  {'INTERFACES':>10}  MANAGED BY"]
    lines += [f"{n:<{width}}  {a:<39}  {st:<11}  {c:>10}  {m}" for n, _, a, st, c, m in rows]
    keys = ("name", "id", "address", "state", "interfaces", "managed_by")
    emit(
        args,
        lines if rows else ["no devices"],
        {"devices": [dict(zip(keys, r, strict=True)) for r in rows]},
    )
    return EXIT_OK


__all__ = ["EXIT_USAGE", "register", "render_issues"]
