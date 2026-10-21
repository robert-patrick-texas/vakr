"""``vakr tui``: the read-only terminal UI on the monitoring node (TUI-001, 13.1).

``vakr tui [--local] [--refresh SECONDS]`` opens the device list and
refreshes it every ``tui.refresh_interval`` (10 s by default, 2 s to 10 min,
Appendix E.29). It reads through the running ``vakr-api`` on its local
socket, which serves root and members of ``vakr-admin`` and ``vakr`` without
a password (13.1, E.29 #10). When ``vakr-api`` is not running, or with
``--local``, it reads the local databases in-process instead and says so in
the header; on that path, run as root, it first switches to the ``vakr``
service user, like ``vakr db``.

``--api-url`` and ``--profile`` are accepted for the 13.1 command line but
refused: a TUI on another host would need API credentials or tokens for a
person, which do not exist yet.

Exit status: 0 after a normal quit; 3 when it cannot start (no data yet, an
unreadable metadata database, a ``vakr-api`` that does not answer or
refuses the account, or ``--api-url``/``--profile``); 2 for a usage error,
including a ``--refresh`` outside 2 s to 10 min.

Dependencies:
    vakr.cli.status_source: Picks the API socket or the databases.
    vakr.tui.app: The Textual application.

Safety:
    Read-only. Never reads ``/etc/vakr/secrets`` and sends no credentials.

Example:
    $ vakr tui --refresh 5
"""

import argparse
import sys

from vakr.cli.common import SubParsers
from vakr.cli.status_source import open_source
from vakr.cli.status_stores import NoDataYetError
from vakr.client.status import SocketStatusSource
from vakr.domain.errors import StorageError
from vakr.paths import InstallPaths
from vakr.ports.migrations import MigrationError
from vakr.query import SourceUnavailableError
from vakr.query.settings import MAX_REFRESH_S, MIN_REFRESH_S, refresh_from_config
from vakr.tui.app import VakrTui

COMMAND = "vakr tui"
EXIT_CANNOT_START = 3
REMOTE_LATER = ("a TUI on another host needs API credentials or tokens, which vakr does not "
                "offer yet; run vakr tui on the monitoring node")  # fmt: skip


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
    parser.add_argument("--local", action="store_true",
                        help="read the local databases instead of asking vakr-api")  # fmt: skip
    parser.add_argument("--api-url", metavar="URL", help="remote API (not available yet)")
    parser.add_argument(
        "--profile", metavar="NAME", help="remote API credentials (not available yet)"
    )
    parser.set_defaults(func=run)


def _refused(args: argparse.Namespace) -> str | None:
    """Return why the remote options cannot be used, if one was given.

    Args:
        args: Parsed arguments.

    Returns:
        str | None: The message, or ``None``.

    Raises:
        None
    """
    given = [flag for flag, value in (("--api-url", args.api_url), ("--profile", args.profile))
             if value]  # fmt: skip
    return f"{'/'.join(given)}: {REMOTE_LATER}" if given else None


def run(args: argparse.Namespace) -> int:
    """Open the data source and run the TUI until the operator quits.

    Args:
        args: Parsed arguments with ``refresh``, ``local``, ``api_url``, and ``profile``.

    Returns:
        int: 0 after quitting; 3 when it cannot start.

    Raises:
        None
    """
    if (problem := _refused(args)) is not None:
        print(f"{COMMAND}: {problem}", file=sys.stderr)
        return EXIT_CANNOT_START
    try:
        data = open_source(InstallPaths.from_environment(), COMMAND, local=args.local)
    except (NoDataYetError, SourceUnavailableError) as exc:
        print(f"{COMMAND}: {exc}", file=sys.stderr)
        return EXIT_CANNOT_START
    except (MigrationError, StorageError) as exc:
        print(f"{COMMAND}: cannot read the metadata database: {exc}", file=sys.stderr)
        return EXIT_CANNOT_START
    try:
        if isinstance(data.source, SocketStatusSource):
            data.source.check()  # 13.4: say at once that the API is unusable, not after a refresh
        configured = min(max(refresh_from_config(data.config), MIN_REFRESH_S), MAX_REFRESH_S)
        zone = str(data.config.get("time.timezone", ""))
        VakrTui(data.source, refresh_s=args.refresh or configured, zone=zone, data=data.label).run()
    except SourceUnavailableError as exc:
        print(f"{COMMAND}: {exc}", file=sys.stderr)
        return EXIT_CANNOT_START
    finally:
        data.close()
    return 0
