"""``vakr interface annotate`` and ``vakr interface tag``: classify interfaces in vakr (META-013).

``annotate DEVICE IFNAME`` sets an interface's annotation: interest
(``--high-priority`` is the alias of ``--interest primary``, META-011),
sticky, WAN circuit, provider, circuit ID, contracted rate, capacity
override, comment, and vakr tags. An option given as ``""`` clears its
field. ``tag DEVICE IFNAME... --add T --remove T`` adds and removes vakr
tags on several interfaces of one device at once, all or nothing.

Both write through the same service as the web UI
(``vakr.inventory.edits.annotations``), audited as ``cli:<login>``. vakr
tags are kept apart from description-derived tags (META-013). Interfaces
``inventory.toml`` governs (claimed, E.19) are refused, naming the file;
on a peer every change is refused, naming the primary (MPOS-003).

Exit status: 0 success; 1 refused or invalid; 4 the metadata database is
unavailable.

Dependencies:
    vakr.inventory.edits: The editor and lookups.
    vakr.cli.inventory_support, device_edit_support: Storage, options, output.

Safety:
    Changes only owned annotation fields; history and telemetry are untouched.

Example:
    $ sudo vakr interface tag edge1.example.net Gi0/0/0 Gi0/0/1 --add uplink
    tagged 2 interface(s), 0 already so
"""

import argparse
from typing import Any

from vakr.adapters.sqlite.metadata import sqlite_uow_factory
from vakr.cli.device_edit_support import fields_from, print_record
from vakr.cli.inventory_support import EXIT_OK, guarded, local_actor, require_store
from vakr.cli.node_guard import refused_on_peer
from vakr.domain.enums import InterestClass
from vakr.domain.errors import NotFoundError
from vakr.domain.interfaces import Interface
from vakr.inventory.edits.annotations import AnnotationEditor
from vakr.inventory.edits.records import find_device, find_interface
from vakr.ports.clock import SystemClock
from vakr.ports.metadata import MetadataUnitOfWorkFactory

SCALARS = ("interest", "high_priority", "sticky", "wan_circuit", "provider", "circuit_id",
           "contracted_bps", "capacity_override_bps", "comment")  # fmt: skip
"""Options that set one ``[[interface_annotations]]`` field each."""


def register_edits(actions: Any) -> None:
    """Add ``annotate`` and ``tag`` to ``vakr interface``.

    Args:
        actions: The ``interface`` sub-parser collection.

    Returns:
        None

    Raises:
        None
    """
    note = actions.add_parser("annotate", help="set an interface's annotation (META-007)")
    note.add_argument("device", metavar="DEVICE", help="device name or UUID")
    note.add_argument("ifname", metavar="IFNAME", help="interface name, as the device reports it")
    note.add_argument("--interest", choices=[c.value for c in InterestClass])
    flags = (("high-priority", "the alias of --interest primary"), ("sticky", "keep monitoring"),
             ("wan-circuit", "a WAN circuit"))  # fmt: skip
    for flag, what in flags:
        note.add_argument(f"--{flag}", action=argparse.BooleanOptionalAction, help=what)
    for flag in ("provider", "circuit-id", "comment"):
        note.add_argument(f"--{flag}")
    note.add_argument("--contracted-bps", help="contracted rate in bit/s")
    note.add_argument("--capacity-override-bps", help="capacity to use instead of ifSpeed")
    note.add_argument("--tags", help="comma-separated vakr tags; replaces them")
    note.add_argument("--json", action="store_true", help="print JSON instead of text")
    note.set_defaults(func=run_annotate)
    tag = actions.add_parser("tag", help="add or remove vakr tags on interfaces (META-013)")
    tag.add_argument("device", metavar="DEVICE", help="device name or UUID")
    tag.add_argument("ifnames", metavar="IFNAME", nargs="+", help="interface names")
    tag.add_argument("--add", action="append", default=[], metavar="TAG", help="tag to add")
    tag.add_argument("--remove", action="append", default=[], metavar="TAG", help="tag to remove")
    tag.set_defaults(func=run_tag)


def _changes(args: argparse.Namespace) -> dict[str, Any]:
    """Collect the annotation fields an ``annotate`` sets.

    Args:
        args: Parsed options.

    Returns:
        dict[str, Any]: The fields; rates given as digits become integers.

    Raises:
        None
    """
    found = fields_from(args, SCALARS)
    for key in ("contracted_bps", "capacity_override_bps"):
        value = found.get(key)
        if isinstance(value, str) and value.isdigit():
            found[key] = int(value)
    return found


def _interfaces(
    factory: MetadataUnitOfWorkFactory, device_key: str, names: list[str]
) -> list[Interface]:
    """Find interfaces of one device by name.

    Args:
        factory: Opens units of work.
        device_key: Device name or UUID.
        names: Interface names.

    Returns:
        list[Interface]: The interfaces, in order.

    Raises:
        NotFoundError: If the device or an interface does not exist.
        ValidationError: If a name is shared by two interfaces (an open review).
    """
    with factory() as uow:
        device = find_device(uow, device_key)
        if device is None:
            raise NotFoundError("device", device_key)
        return [find_interface(uow, device, n) for n in names]


@guarded
@refused_on_peer
def run_annotate(args: argparse.Namespace) -> int:
    """Set one interface's annotation.

    Args:
        args: ``device``, ``ifname``, and the field options.

    Returns:
        int: Exit status.

    Raises:
        DomainError, InventoryError, StorageUnavailableError: Translated by ``guarded``.
    """
    store = require_store()
    try:
        factory = sqlite_uow_factory(store)
        [iface] = _interfaces(factory, args.device, [args.ifname])
        record = AnnotationEditor(factory, SystemClock()).annotate(
            iface.id, _changes(args), version=None, actor=local_actor()
        )
    finally:
        store.close()
    print_record(args, record)
    return EXIT_OK


@guarded
@refused_on_peer
def run_tag(args: argparse.Namespace) -> int:
    """Add and remove vakr tags on interfaces of one device.

    Args:
        args: ``device``, ``ifnames``, ``add``, and ``remove``.

    Returns:
        int: Exit status.

    Raises:
        DomainError, InventoryError, StorageUnavailableError: Translated by ``guarded``.
    """
    store = require_store()
    try:
        factory = sqlite_uow_factory(store)
        found = _interfaces(factory, args.device, args.ifnames)
        done = AnnotationEditor(factory, SystemClock()).tag(
            [i.id for i in found], add=args.add, remove=args.remove, actor=local_actor()
        )
    finally:
        store.close()
    print(f"tagged {len(done.changed)} interface(s), {len(done.unchanged)} already so")
    return EXIT_OK
