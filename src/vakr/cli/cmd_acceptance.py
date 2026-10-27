"""``vakr acceptance``: MVP acceptance evidence computed on this node (blueprint 27.6).

The group holds one subcommand per report. Each module listed in
``SUBCOMMAND_MODULES`` adds its own subcommands with ``register(actions)``,
so a report joins the group by adding its module name here and nothing
else. ``export`` (``vakr.cli.acceptance_export``) writes this node's
samples and ICMP cycles for the MRTG/SmokePing comparison (MVP-ACC-03,
MVP-ACC-04).

Exit status: 0 pass (``export``: written in full), 1 fail, 2 usage error,
3 incomplete or the data could not be read.

Dependencies:
    importlib: Loads the listed subcommand modules.

Safety:
    Read-only; see each subcommand module.

Example:
    $ sudo vakr acceptance export --window 50h > export.txt
"""

import argparse
import importlib
import sys

from vakr.cli.common import SubParsers

SUBCOMMAND_MODULES: tuple[str, ...] = ("vakr.cli.acceptance_export",)
"""Modules whose ``register(actions)`` adds ``vakr acceptance`` subcommands, in help order."""

EXIT_USAGE = 2


def register(subparsers: SubParsers) -> None:
    """Add ``acceptance`` and the subcommands of every listed module.

    Args:
        subparsers: The top-level sub-parser collection.

    Returns:
        None

    Raises:
        ImportError: If a listed module is missing, a packaging bug that should fail loudly.
    """
    parser = subparsers.add_parser(
        "acceptance", help="MVP acceptance evidence from this node's data (read-only)"
    )
    parser.set_defaults(func=lambda _a: _usage(parser))
    actions = parser.add_subparsers(title="subcommands", metavar="SUBCOMMAND")
    for name in SUBCOMMAND_MODULES:
        importlib.import_module(name).register(actions)


def _usage(parser: argparse.ArgumentParser) -> int:
    """Print help when no subcommand is given.

    Args:
        parser: The group's parser.

    Returns:
        int: 2, the usage-error status.

    Raises:
        None
    """
    parser.print_help(sys.stderr)
    return EXIT_USAGE
