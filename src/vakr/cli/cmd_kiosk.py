"""``vakr kiosk``: kiosk tokens for unattended wall displays (UI-013, Appendix E.12).

``create LABEL [--lifetime DURATION]`` makes a token for one display and
prints it once, with the setup steps: open ``/kiosk/setup`` on the display
and paste it. ``list`` shows every token's label, state, expiry, last use,
and ID, never the token. ``revoke ID|LABEL`` ends a token at once: every
display that holds it is signed out at its next request. A kiosk token
grants the operator role only and expires after ``auth.kiosk_token_lifetime``
(30 days) unless ``--lifetime`` says otherwise (1 to 365 days). Every
change is audited with the invoking OS user.

Exit status: 0 success; 1 refused or invalid (label rule, taken label,
lifetime, unknown token, 100 tokens already); 2 usage error; 4 the
metadata database is unavailable.

Dependencies:
    vakr.cli.kiosk_support, inventory_support, user_support: Service, output, actor.

Safety:
    Run as root, it first becomes the ``vakr`` service user. Only ``create``
    prints a token, once; nothing logs it. A token pasted into ``revoke`` by
    mistake is refused without being repeated.

Example:
    $ sudo vakr kiosk create noc-wall
    created kiosk token noc-wall, expires 2026-11-20 14:03 UTC (id 0190...)
"""

import argparse
import sys
from uuid import UUID

from vakr.auth.kiosk import KioskService
from vakr.auth.kiosk_rules import parse_lifetime
from vakr.auth.tokens import KIOSK_PREFIX
from vakr.cli.common import SubParsers
from vakr.cli.inventory_support import (
    EXIT_INVALID,
    EXIT_OK,
    EXIT_USAGE,
    emit,
    guarded,
    require_store,
)
from vakr.cli.kiosk_support import created_lines, kiosk_lines, kiosk_row, kiosk_service
from vakr.cli.user_support import cli_actor
from vakr.domain.ids import KioskTokenId
from vakr.domain.sessions import KioskToken
from vakr.ports.clock import SystemClock


def register(subparsers: SubParsers) -> None:
    """Add ``kiosk`` and its subcommands.

    Args:
        subparsers: The top-level sub-parser collection.

    Returns:
        None

    Raises:
        None
    """
    parser = subparsers.add_parser("kiosk", help="kiosk tokens for wall displays")
    parser.set_defaults(func=lambda _args: _usage(parser))
    actions = parser.add_subparsers(title="subcommands", metavar="SUBCOMMAND")
    create = actions.add_parser("create", help="create a token for one display (shown once)")
    create.add_argument("label", help="the display's name, such as noc-wall")
    create.add_argument("--lifetime", metavar="DURATION",
                        help="1d to 365d (default: auth.kiosk_token_lifetime)")  # fmt: skip
    create.set_defaults(func=run_create)
    listing = actions.add_parser("list", help="list tokens (never the tokens themselves)")
    listing.add_argument("--json", action="store_true", help="print JSON instead of text")
    listing.set_defaults(func=run_list)
    revoke = actions.add_parser("revoke", help="revoke a token at once")
    revoke.add_argument("key", metavar="ID|LABEL", help="the token's ID or label")
    revoke.set_defaults(func=run_revoke)


def _usage(parser: argparse.ArgumentParser) -> int:
    """Print ``vakr kiosk`` help when no subcommand is given.

    Args:
        parser: The ``kiosk`` parser.

    Returns:
        int: 2, the usage-error status.

    Raises:
        None
    """
    parser.print_help(sys.stderr)
    return EXIT_USAGE


@guarded
def run_create(args: argparse.Namespace) -> int:
    """Create a token and print it once with the setup steps.

    Args:
        args: ``label`` and ``lifetime``.

    Returns:
        int: Exit status.

    Raises:
        DomainError, Unavailable: Translated by ``guarded``.
    """
    lifetime = parse_lifetime(args.lifetime)
    store = require_store()
    try:
        issued = kiosk_service(store).create(args.label, actor=cli_actor(), lifetime=lifetime)
    finally:
        store.close()
    print("\n".join(created_lines(issued)))
    return EXIT_OK


@guarded
def run_list(args: argparse.Namespace) -> int:
    """List token records by label.

    Args:
        args: ``json``.

    Returns:
        int: Exit status.

    Raises:
        Unavailable: Translated by ``guarded``.
    """
    store = require_store()
    try:
        tokens = kiosk_service(store).tokens()
    finally:
        store.close()
    now = SystemClock().now_utc()
    emit(args, kiosk_lines(tokens, now), {"kiosk_tokens": [kiosk_row(t, now) for t in tokens]})
    return EXIT_OK


def _record(service: KioskService, key: str) -> KioskToken | None:
    """Return the record whose ID is ``key``, else the one labelled ``key``.

    Args:
        service: Kiosk token administration.
        key: What was typed.

    Returns:
        KioskToken | None: The record to revoke, or ``None``.

    Raises:
        StorageError: If the records cannot be read.
    """
    try:
        by_id = service.get(KioskTokenId(UUID(key)))
    except ValueError:
        by_id = None
    return by_id or service.find(key)


@guarded
def run_revoke(args: argparse.Namespace) -> int:
    """Revoke a token by ID or label.

    Args:
        args: ``key``.

    Returns:
        int: Exit status.

    Raises:
        DomainError, Unavailable: Translated by ``guarded``.
    """
    if args.key.startswith(KIOSK_PREFIX):
        print("vakr: that is a kiosk token, not its ID or label; find the label with "
              "`vakr kiosk list` (nothing changed)", file=sys.stderr)  # fmt: skip
        return EXIT_INVALID
    store = require_store()
    try:
        service = kiosk_service(store)
        record = _record(service, args.key)
        done = record is not None and service.revoke(record.id, actor=cli_actor())
    finally:
        store.close()
    if record is None or not done:
        print(f"vakr: no kiosk token {args.key}; see `vakr kiosk list`", file=sys.stderr)
        return EXIT_INVALID
    print(f"revoked kiosk token {record.label}; displays holding it are signed out")
    return EXIT_OK
