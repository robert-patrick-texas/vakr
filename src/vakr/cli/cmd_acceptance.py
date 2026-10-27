"""``vakr acceptance``: evidence for MVP acceptance (blueprint 27.6).

MVP acceptance needs evidence from the production node: how vakr's numbers
compare with MRTG's and SmokePing's during the parallel run (MVP-ACC-03,
MVP-ACC-04). Each subcommand lives in its own module and registers itself
here; this module only builds the group.

Dependencies:
    vakr.cli.cmd_acceptance_export: ``vakr acceptance export``.

Safety:
    Every subcommand is read-only. Without a subcommand the group prints
    its help and exits 2.

Example:
    $ vakr acceptance export --since 50h > export.txt
"""

import argparse
import sys

from vakr.cli import cmd_acceptance_export
from vakr.cli.common import SubParsers


def register(subparsers: SubParsers) -> None:
    """Add ``acceptance`` and its subcommands.

    Args:
        subparsers: The top-level sub-parser collection.

    Returns:
        None

    Raises:
        None
    """
    parser = subparsers.add_parser("acceptance", help="MVP acceptance evidence (27.6)")
    parser.set_defaults(func=lambda _args: _usage(parser))
    commands = parser.add_subparsers(title="acceptance commands", metavar="COMMAND")
    cmd_acceptance_export.register(commands)


def _usage(parser: argparse.ArgumentParser) -> int:
    """Print the group's help when no subcommand is given.

    Args:
        parser: The ``acceptance`` parser.

    Returns:
        int: 2, a usage error.

    Raises:
        None
    """
    parser.print_help(sys.stderr)
    return 2
