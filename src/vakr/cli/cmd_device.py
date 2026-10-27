"""``vakr device add|set|remove|show``: manual device entry from the shell (27.4, META-008).

The CLI's side of the web UI's device form, through the same service
(``vakr.inventory.edits.devices``) with the same checks as ``vakr inventory
apply`` and an audit event naming the operator (``cli:<login>``):

- ``add NAME ADDRESS`` creates an API-managed device;
- ``set DEVICE`` changes one (an option given as ``""`` clears its field);
- ``remove DEVICE`` retires it, the soft delete (META-009): history stays,
  and ``set DEVICE --admin-state enabled`` restores it;
- ``show DEVICE`` prints the record.

``DEVICE`` is a name or UUID. Devices ``inventory.toml`` manages are
refused, naming the file. On a peer every change is refused, naming the
primary (MPOS-003). Collectors pick changes up within seconds (6.2).

Exit status: 0 success; 1 refused or invalid (the message says why); 2
usage error; 4 the metadata database is unavailable.

Dependencies:
    vakr.inventory.edits: The editor.
    vakr.cli.inventory_support, device_edit_support: Storage, options, output.

Safety:
    Names credentials, never their secrets (SEC-001).

Example:
    $ sudo vakr device add edge9.example.net 192.0.2.9 --credential network-v2c --tags wan
    edge9.example.net (0199...): enabled, API-managed, version 1
"""

import argparse
import sys
from pathlib import Path

from vakr.adapters.sqlite.metadata import sqlite_uow_factory
from vakr.cli.common import SubParsers
from vakr.cli.device_edit_support import edit_checks, fields_from, print_record
from vakr.cli.inventory_support import (
    EXIT_INVALID,
    EXIT_OK,
    EXIT_USAGE,
    guarded,
    local_actor,
    require_store,
)
from vakr.cli.node_guard import refused_on_peer
from vakr.domain.enums import AddressFamily, ReachabilityStrategy
from vakr.domain.errors import NotFoundError
from vakr.domain.ids import DeviceId
from vakr.inventory.edits.devices import DeviceEditor
from vakr.inventory.edits.records import DeviceRecord, find_device
from vakr.inventory.validate import Checks
from vakr.ports.clock import SystemClock

SCALARS = ("name", "address", "site", "credential", "comment", "admin_state", "interval",
           "discovery_interval", "reachability", "address_family")  # fmt: skip
"""Options that set one ``[[devices]]`` field each."""


def _fields(parser: argparse.ArgumentParser, *, rename: bool) -> None:
    """Add the device field options.

    Args:
        parser: ``add`` or ``set``.
        rename: Whether ``--name`` and ``--address`` are options (``set``).

    Returns:
        None

    Raises:
        None
    """
    if rename:
        parser.add_argument("--name", help="new name")
        parser.add_argument("--address", help="new poll address: IP literal or DNS name")
    parser.add_argument("--site", help="an existing site's name")
    parser.add_argument("--credential", help="SNMP credential name in the credentials file")
    parser.add_argument("--tags", help="comma-separated tags; replaces them")
    parser.add_argument("--groups", help="comma-separated groups; replaces them")
    parser.add_argument("--comment", help="operator comment")
    parser.add_argument("--admin-state", choices=("enabled", "maintenance", "disabled"))
    parser.add_argument("--interval", help="poll interval override, such as 120s")
    parser.add_argument("--discovery-interval", help="rediscovery interval override, such as 6h")
    parser.add_argument("--reachability", choices=[*(s.value for s in ReachabilityStrategy), ""])
    parser.add_argument("--address-family", choices=[*(f.value for f in AddressFamily), ""])
    _common(parser)


def _common(parser: argparse.ArgumentParser) -> None:
    """Add ``--json``, ``--config-dir``, and the hidden ``--dev``.

    Args:
        parser: A subcommand.

    Returns:
        None

    Raises:
        None
    """
    parser.add_argument("--json", action="store_true", help="print JSON instead of text")
    parser.add_argument("--config-dir", default=None, type=Path, help="configuration directory")
    parser.add_argument("--dev", action="store_true", help=argparse.SUPPRESS)


def register(subparsers: SubParsers) -> None:
    """Add ``device`` with ``add``, ``set``, ``remove``, and ``show``.

    Args:
        subparsers: The top-level sub-parser collection.

    Returns:
        None

    Raises:
        None
    """
    parser = subparsers.add_parser("device", help="add, change, retire, or show a device")
    parser.set_defaults(func=lambda _args: _usage(parser))
    actions = parser.add_subparsers(title="subcommands", metavar="SUBCOMMAND")
    add = actions.add_parser("add", help="add an API-managed device")
    add.add_argument("name", metavar="NAME", help="unique device name")
    add.add_argument("address", metavar="ADDRESS", help="poll address: IP literal or DNS name")
    _fields(add, rename=False)
    add.set_defaults(func=run_add)
    change = actions.add_parser("set", help="change a device; an empty value clears a field")
    change.add_argument("device", metavar="DEVICE", help="device name or UUID")
    _fields(change, rename=True)
    change.set_defaults(func=run_set)
    remove = actions.add_parser("remove", help="retire a device (soft delete; history stays)")
    remove.add_argument("device", metavar="DEVICE", help="device name or UUID")
    _common(remove)
    remove.set_defaults(func=run_remove)
    show = actions.add_parser("show", help="print a device's record")
    show.add_argument("device", metavar="DEVICE", help="device name or UUID")
    _common(show)
    show.set_defaults(func=run_show)


def _usage(parser: argparse.ArgumentParser) -> int:
    """Print help when no subcommand is given.

    Args:
        parser: The parser.

    Returns:
        int: 2, the usage-error status.

    Raises:
        None
    """
    parser.print_help(sys.stderr)
    return EXIT_USAGE


def _edit(args: argparse.Namespace, action: str) -> int:
    """Open the store, find the device, run one edit, and print the result.

    Args:
        args: Parsed options.
        action: ``add``, ``set``, ``remove``, or ``show``.

    Returns:
        int: Exit status.

    Raises:
        InventoryError, DomainError, StorageUnavailableError: Translated by ``guarded``.
    """
    checks = edit_checks(args) if action in ("add", "set") else Checks()
    store = require_store()
    try:
        factory = sqlite_uow_factory(store)
        editor = DeviceEditor(factory, SystemClock(), lambda: checks)
        if action == "add":
            fields = {"name": args.name, "address": args.address} | fields_from(args, SCALARS)
            record = editor.create(fields, actor=local_actor())
        else:
            with factory() as uow:
                device = find_device(uow, args.device)
            if device is None:
                print(f"vakr: no device named {args.device!r}", file=sys.stderr)
                return EXIT_INVALID
            record = _change(editor, action, device.id, args)
    finally:
        store.close()
    print_record(args, record)
    return EXIT_OK


def _change(editor: DeviceEditor, action: str, device_id: DeviceId, args: argparse.Namespace
            ) -> DeviceRecord:  # fmt: skip
    """Run ``set``, ``remove``, or ``show`` on a found device.

    Args:
        editor: The editor.
        action: The subcommand.
        device_id: The device.
        args: Parsed options.

    Returns:
        DeviceRecord: The record after the edit.

    Raises:
        DomainError: From the editor.
    """
    if action == "set":
        return editor.update(device_id, fields_from(args, SCALARS), version=None,
                             actor=local_actor())  # fmt: skip
    if action == "remove":
        return editor.retire(device_id, version=None, actor=local_actor())
    found = editor.get(device_id)
    if found is None:  # nothing in vakr deletes a device, so this needs outside interference
        raise NotFoundError("device", device_id)
    return found


@guarded
@refused_on_peer
def run_add(args: argparse.Namespace) -> int:
    """Add a device.

    Args:
        args: ``name``, ``address``, and the field options.

    Returns:
        int: Exit status.

    Raises:
        None
    """
    return _edit(args, "add")


@guarded
@refused_on_peer
def run_set(args: argparse.Namespace) -> int:
    """Change a device.

    Args:
        args: ``device`` and the field options.

    Returns:
        int: Exit status.

    Raises:
        None
    """
    return _edit(args, "set")


@guarded
@refused_on_peer
def run_remove(args: argparse.Namespace) -> int:
    """Retire a device.

    Args:
        args: ``device``.

    Returns:
        int: Exit status.

    Raises:
        None
    """
    return _edit(args, "remove")


@guarded
def run_show(args: argparse.Namespace) -> int:
    """Print a device's record.

    Args:
        args: ``device``.

    Returns:
        int: Exit status.

    Raises:
        None
    """
    return _edit(args, "show")
