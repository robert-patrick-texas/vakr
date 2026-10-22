"""``vakr interface claim`` and ``vakr interface review list|map|new`` (E.19, IFID-007).

``claim DEVICE IFNAME`` hands a discovered, API-managed interface to
``inventory.toml``, so the file's ``[[interface_annotations]]`` entry
governs it from now on. It is the explicit, audited ownership transfer
the plan asks for (META-008, SEC-018).

``review`` resolves interfaces whose identity vakr would not guess after a
rediscovery (IFID-004): ``list`` shows each open case with its candidates,
``map REVIEW INTERFACE`` says the row is that existing interface (its
history continues), and ``new REVIEW`` gives the row a new identity. Each
decision is recorded in identity history and audited.

Exit status: 0 success; 1 refused (the message says why); 2 usage error;
4 the metadata database is unavailable.

Dependencies:
    vakr.inventory.claim, vakr.discovery.reconcile.admin: The operations.
    vakr.cli.inventory_support: Files, storage, and exit codes.

Safety:
    Changes only ownership, annotations, and identity mapping; history and
    telemetry are never deleted.

Example:
    $ sudo vakr interface claim edge1.example.net Gi0/0/0
    claimed edge1.example.net Gi0/0/0 for inventory.toml
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
    guarded,
    load_file,
    local_actor,
    require_store,
)
from vakr.cli.node_guard import refused_on_peer
from vakr.discovery.reconcile.admin import ReviewError, create_identity, map_review
from vakr.domain.ids import InterfaceId, ReviewId, parse_id
from vakr.domain.reviews import IdentityReview
from vakr.inventory.claim import ClaimError, claim_interface
from vakr.inventory.reads import open_reviews
from vakr.ports.clock import SystemClock
from vakr.ports.metadata import MetadataUnitOfWork


def register(subparsers: SubParsers) -> None:
    """Add ``interface`` with ``claim`` and ``review``.

    Args:
        subparsers: The top-level sub-parser collection.

    Returns:
        None

    Raises:
        None
    """
    parser = subparsers.add_parser("interface", help="claim interfaces; resolve identity reviews")
    parser.set_defaults(func=lambda _args: _usage(parser))
    actions = parser.add_subparsers(title="subcommands", metavar="SUBCOMMAND")
    claim = actions.add_parser("claim", help="let inventory.toml govern a discovered interface")
    claim.add_argument("device", metavar="DEVICE", help="device name")
    claim.add_argument("ifname", metavar="IFNAME", help="interface name, as the device reports it")
    claim.add_argument("--config-dir", default=None, type=Path, help="configuration directory")
    claim.add_argument("--file", default=None, type=Path, metavar="FILE", help="inventory file")
    claim.add_argument("--dev", action="store_true", help=argparse.SUPPRESS)
    claim.set_defaults(func=run_claim)
    review = actions.add_parser("review", help="resolve interfaces with an uncertain identity")
    review.set_defaults(func=lambda _args: _usage(review))
    steps = review.add_subparsers(title="subcommands", metavar="SUBCOMMAND")
    listing = steps.add_parser("list", help="list open identity reviews")
    listing.add_argument("--json", action="store_true", help="print JSON instead of text")
    listing.set_defaults(func=run_review_list)
    mapping = steps.add_parser("map", help="the row is this existing interface")
    mapping.add_argument("review", metavar="REVIEW", help="review UUID")
    mapping.add_argument("interface", metavar="INTERFACE", help="interface UUID")
    mapping.set_defaults(func=run_review_map)
    fresh = steps.add_parser("new", help="the row is a new interface")
    fresh.add_argument("review", metavar="REVIEW", help="review UUID")
    fresh.set_defaults(func=run_review_new)


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


@guarded
@refused_on_peer
def run_claim(args: argparse.Namespace) -> int:
    """Transfer an interface to ``inventory.toml`` and write its annotation.

    Args:
        args: ``device``, ``ifname``, ``config_dir``, ``file``, ``dev``.

    Returns:
        int: Exit status.

    Raises:
        InventoryError, StorageUnavailableError: Translated by ``guarded``.
    """
    inv, path, _ = load_file(args)
    if inv is None:
        print(f"vakr: {path} not found; add the annotation there first", file=sys.stderr)
        return EXIT_INVALID
    store = require_store()
    try:
        claimed, changes = claim_interface(sqlite_uow_factory(store), inv, args.device,
                                           args.ifname, local_actor(), SystemClock())  # fmt: skip
    except ClaimError as exc:
        print(f"vakr: cannot claim {args.device} {args.ifname}: {exc}", file=sys.stderr)
        return EXIT_INVALID
    finally:
        store.close()
    print(f"claimed {args.device} {claimed.if_name} ({claimed.id}) for {inv.source}")
    for change in changes:
        print(f"  {change}")
    return EXIT_OK


def _candidate_text(uow: MetadataUnitOfWork, review: IdentityReview) -> list[str]:
    """Describe each candidate of a review.

    Args:
        uow: The open unit of work.
        review: The review.

    Returns:
        list[str]: ``name (ifIndex N, lifecycle) UUID`` per candidate.

    Raises:
        None
    """
    out = []
    for candidate in review.candidates:
        i = uow.interfaces.get(candidate)
        where = "?" if i is None else f"(ifIndex {i.if_index}, {i.lifecycle})"
        label = "?" if i is None else f"{i.if_name or i.if_descr} {where}"
        out.append(f"{label} {candidate}")
    return out


def _review_entry(
    uow: MetadataUnitOfWork, r: IdentityReview
) -> tuple[list[str], dict[str, object]]:
    """Describe one open review as text lines and as JSON data.

    Args:
        uow: The open unit of work.
        r: The review.

    Returns:
        tuple[list[str], dict[str, object]]: Lines and data.

    Raises:
        None
    """
    device = uow.devices.get(r.device_id)
    name = device.name if device else str(r.device_id)
    head = (f"review {r.id}: {name} ifIndex {r.if_index} {r.if_name or r.if_descr!r} "
            f"[{r.outcome.value}] since {r.opened_at:%Y-%m-%d %H:%M}Z")  # fmt: skip
    lines = [head, *(f"  candidate {c}" for c in _candidate_text(uow, r))]
    data: dict[str, object] = {
        "id": str(r.id), "device": name, "if_index": r.if_index, "if_name": r.if_name,
        "outcome": r.outcome.value, "candidates": [str(c) for c in r.candidates],
        "evidence": list(r.evidence),
    }  # fmt: skip
    return lines, data


@guarded
def run_review_list(args: argparse.Namespace) -> int:
    """List open identity reviews with their candidates (IFID-007).

    Args:
        args: ``json``.

    Returns:
        int: Exit status.

    Raises:
        StorageUnavailableError: Translated by ``guarded``.
    """
    store = require_store()
    lines: list[str] = []
    data: list[dict[str, object]] = []
    try:
        with sqlite_uow_factory(store)() as uow:
            for r in open_reviews(uow, None):
                text, entry = _review_entry(uow, r)
                lines.extend(text)
                data.append(entry)
    finally:
        store.close()
    emit(args, lines or ["no open identity reviews"], {"reviews": data})
    return EXIT_OK


def _decide(args: argparse.Namespace, interface: InterfaceId | None) -> int:
    """Map a review to an interface, or create a new identity.

    Args:
        args: ``review``.
        interface: The interface to map to, or ``None`` for a new identity.

    Returns:
        int: Exit status.

    Raises:
        StorageUnavailableError: Translated by ``guarded``.
    """
    review = parse_id(ReviewId, args.review)
    store = require_store()
    try:
        factory, actor, clock = sqlite_uow_factory(store), local_actor(), SystemClock()
        if interface is None:
            result = create_identity(factory, review, actor, clock)
        else:
            result = map_review(factory, review, interface, actor, clock)
    except ReviewError as exc:
        print(f"vakr: {exc}", file=sys.stderr)
        return EXIT_INVALID
    finally:
        store.close()
    verb = "created new identity" if interface is None else "mapped to"
    print(f"review {review}: {verb} {result.id} at ifIndex {result.if_index}")
    return EXIT_OK


@guarded
@refused_on_peer
def run_review_map(args: argparse.Namespace) -> int:
    """The review's row is an existing interface; its history continues.

    Args:
        args: ``review`` and ``interface``.

    Returns:
        int: Exit status.

    Raises:
        StorageUnavailableError: Translated by ``guarded``.
    """
    return _decide(args, parse_id(InterfaceId, args.interface))


@guarded
@refused_on_peer
def run_review_new(args: argparse.Namespace) -> int:
    """The review's row is a new interface with its own identity.

    Args:
        args: ``review``.

    Returns:
        int: Exit status.

    Raises:
        StorageUnavailableError: Translated by ``guarded``.
    """
    return _decide(args, None)
