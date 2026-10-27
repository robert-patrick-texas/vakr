"""Entry point for the ``vakr`` command.

Operators run ``vakr`` on monitoring hosts to diagnose, update, configure, and
(later) inspect the network monitor. This module only assembles subcommands
from their modules and dispatches; behavior lives in each ``cmd_*`` module.

Dependencies:
    argparse: Parses arguments without third-party packages.
    importlib: Loads the explicitly listed subcommand modules.

Safety:
    Unknown commands and bad arguments exit with status 2 and a usage message
    before anything touches the system.

Example:
    $ vakr --version
    vakr <version>
"""

import argparse
import importlib
import sys
from collections.abc import Sequence

from vakr.version import get_version

COMMAND_MODULES: tuple[str, ...] = (
    "vakr.cli.cmd_doctor",
    "vakr.cli.cmd_check",
    "vakr.cli.cmd_setup",
    "vakr.cli.cmd_config",
    "vakr.cli.cmd_credentials",
    "vakr.cli.cmd_selfupdate",
    "vakr.cli.cmd_tls",
    "vakr.cli.cmd_field",
    "vakr.cli.cmd_policy",
    "vakr.cli.cmd_db",
    "vakr.cli.cmd_icmp",
    "vakr.cli.cmd_snmp",
    "vakr.cli.cmd_status",
    "vakr.cli.cmd_tui",
    "vakr.cli.cmd_inventory",
    "vakr.cli.cmd_device",
    "vakr.cli.cmd_interface",
    "vakr.cli.cmd_collector",
    "vakr.cli.cmd_api",
    "vakr.cli.cmd_user",
    "vakr.cli.cmd_kiosk",
    "vakr.cli.cmd_replication",
    "vakr.cli.cmd_acceptance",
    "vakr.cli.cmd_node",
)
"""Subcommand modules, in the order they appear in ``vakr --help``."""


def build_parser() -> argparse.ArgumentParser:
    """Create the top-level parser with every registered subcommand.

    Args:
        None

    Returns:
        argparse.ArgumentParser: Parser whose subcommands set ``func`` to a
        handler taking the parsed namespace and returning an exit code.

    Raises:
        ImportError: If a listed subcommand module is missing, which is a
            packaging bug that should fail loudly.
    """
    parser = argparse.ArgumentParser(
        prog="vakr",
        description="vakr network performance and reachability monitor",
    )
    parser.add_argument("--version", action="version", version=f"vakr {get_version()}")
    subparsers = parser.add_subparsers(title="commands", metavar="COMMAND")
    for module_name in COMMAND_MODULES:
        importlib.import_module(module_name).register(subparsers)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Parse arguments, run the chosen subcommand, and return its exit code.

    Args:
        argv: Arguments without the program name; ``None`` means
            ``sys.argv[1:]``.

    Returns:
        int: Process exit status from the subcommand, or 2 when no command
        was given.

    Raises:
        SystemExit: Raised by argparse for ``--help``, ``--version``, and
            usage errors.
    """
    parser = build_parser()
    args = parser.parse_args(argv)
    handler = getattr(args, "func", None)
    if handler is None:
        parser.print_help(sys.stderr)
        return 2
    return int(handler(args))


if __name__ == "__main__":
    raise SystemExit(main())
