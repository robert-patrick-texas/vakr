"""``vakr tui``: the read-only terminal UI on the monitoring node (TUI-001, 13.1).

``vakr tui [--refresh SECONDS]`` opens the device list from the local
databases through the in-process query services (TUI-007), refreshing every
``tui.refresh_interval`` (10 s by default, 2 s to 10 min, Appendix E.29).
Run as root, it first switches to the ``vakr`` service user, like ``vakr db``.

``--api-url`` is accepted for the 13.1 command line but refused: the API
that a remote TUI would talk to arrives with milestone M4. ``--profile``
(13.1) is not offered yet for the same reason; it selects API credentials.

Exit status: 0 after a normal quit; 3 when there is no data yet, the
metadata database cannot be read, or ``--api-url`` is given; 2 for a usage
error, including a ``--refresh`` outside 2 s to 10 min.

Dependencies:
    vakr.cli.status_stores: Opens the databases and the query service.
    vakr.tui.app: The Textual application.

Safety:
    Read-only. Never reads ``/etc/vakr/secrets``.

Example:
    $ vakr tui --refresh 5
"""

import argparse
import sys

from vakr.cli.common import SubParsers
from vakr.cli.service_user import drop_to_service_user
from vakr.cli.status_stores import NoDataYetError, open_status
from vakr.domain.errors import StorageError
from vakr.paths import InstallPaths
from vakr.ports.migrations import MigrationError
from vakr.query.settings import MAX_REFRESH_S, MIN_REFRESH_S, refresh_from_config
from vakr.tui.app import VakrTui

COMMAND = "vakr tui"
EXIT_CANNOT_START = 3
API_LATER = "the API arrives in a later release (M4); run vakr tui on the monitoring node"


def _refresh(text: str) -> float:
    """Parse ``--refresh`` and enforce its bounds.

    Args:
        text: Seconds, as typed.

    Returns:
        float: Seconds.

    Raises:
        argparse.ArgumentTypeError: If it is not a number from 2 to 600.
    """
    try:
        value = float(text)
    except ValueError:
        raise argparse.ArgumentTypeError(f"{text!r} is not a number of seconds") from None
    if not MIN_REFRESH_S <= value <= MAX_REFRESH_S:
        raise argparse.ArgumentTypeError(
            f"refresh must be {MIN_REFRESH_S:g} to {MAX_REFRESH_S:g} seconds"
        )
    return value


def register(subparsers: SubParsers) -> None:
    """Add the ``tui`` command.

    Args:
        subparsers: The top-level sub-parser collection.

    Returns:
        None

    Raises:
        None
    """
    parser = subparsers.add_parser("tui", help="read-only terminal UI of current state")
    parser.add_argument("--refresh", type=_refresh, metavar="SECONDS",
                        help="seconds between refreshes, 2-600 "
                        "(default: tui.refresh_interval)")  # fmt: skip
    parser.add_argument("--api-url", metavar="URL", help="remote API (not available yet)")
    parser.set_defaults(func=run)


def run(args: argparse.Namespace) -> int:
    """Open the stores and run the TUI until the operator quits.

    Args:
        args: Parsed arguments with ``refresh`` and ``api_url``.

    Returns:
        int: 0 after quitting; 3 when it cannot start.

    Raises:
        None
    """
    if args.api_url:
        print(f"{COMMAND}: --api-url: {API_LATER}", file=sys.stderr)
        return EXIT_CANNOT_START
    if (problem := drop_to_service_user()) is not None:
        print(f"{COMMAND}: {problem}", file=sys.stderr)
        return EXIT_CANNOT_START
    try:
        stores = open_status(InstallPaths.from_environment(), COMMAND)
    except NoDataYetError as exc:
        print(f"{COMMAND}: {exc}", file=sys.stderr)
        return EXIT_CANNOT_START
    except (MigrationError, StorageError) as exc:
        print(f"{COMMAND}: cannot read the metadata database: {exc}", file=sys.stderr)
        return EXIT_CANNOT_START
    try:
        configured = min(max(refresh_from_config(stores.config), MIN_REFRESH_S), MAX_REFRESH_S)
        refresh = args.refresh or configured
        zone = str(stores.config.get("time.timezone", ""))
        VakrTui(stores.queries, refresh_s=refresh, zone=zone).run()
    finally:
        stores.close()
    return 0
