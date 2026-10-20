"""``vakr config``: inspect, validate, upgrade, and document configuration.

Operator files under ``/etc/vakr`` hold only overrides of the packaged
defaults (CFGM-001). These subcommands show the merged result and where each
value came from, validate edits before a restart, apply config migrations
after a package upgrade (self-update runs ``upgrade`` automatically), report
options a release added, and render the packaged defaults and starter file
(blueprint 28.4).

Dependencies:
    argparse: Subcommand parsing.
    vakr.config.commands: The handlers.

Safety:
    Only ``upgrade`` (without ``--dry-run``), ``new-options --annotate``, and
    ``defaults --output`` write files; the first two back up and write
    atomically, and leave operator files unchanged on any failure.

Example:
    $ vakr config upgrade --dry-run
    dry run: 0 file(s) would change; nothing written
"""

import argparse
import sys
from pathlib import Path

from vakr.cli.common import SubParsers
from vakr.config import commands


def register(subparsers: SubParsers) -> None:
    """Add the ``config`` command and its subcommands.

    Args:
        subparsers: The top-level sub-parser collection.

    Returns:
        None

    Raises:
        None
    """
    parser = subparsers.add_parser(
        "config", help="validate, upgrade, and inspect configuration (CFGM-*)"
    )
    parser.set_defaults(func=lambda _args: _usage(parser))
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument(
        "--config-dir",
        type=Path,
        default=None,
        help="operator configuration directory (default: /etc/vakr, relocated by VAKR_ROOT)",
    )
    actions = parser.add_subparsers(title="subcommands", metavar="SUBCOMMAND")
    show = actions.add_parser(
        "show-effective", parents=[common], help="print merged settings (credentials masked)"
    )
    show.add_argument("--sources", action="store_true", help="show the file:line behind each")
    show.set_defaults(func=commands.show_effective)
    check = actions.add_parser("validate", parents=[common], help="validate files; exit 0 or 1")
    check.set_defaults(func=commands.validate)
    up = actions.add_parser("upgrade", parents=[common], help="apply config migrations")
    up.add_argument("--dry-run", action="store_true", help="print the diff without writing")
    up.set_defaults(func=commands.run_upgrade)
    new = actions.add_parser("new-options", parents=[common], help="list options a release added")
    new.add_argument("--since", metavar="VERSION", help="baseline release (default: previous)")
    new.add_argument("--annotate", action="store_true", help="add them as comments to your files")
    new.set_defaults(func=commands.list_new_options)
    defaults = actions.add_parser("defaults", help="render the documented defaults")
    defaults.add_argument("--output", type=Path, metavar="DIR", help="write the defaults set")
    defaults.add_argument("--markdown", action="store_true", help="Markdown reference instead")
    defaults.set_defaults(func=commands.write_defaults)
    template = actions.add_parser("template", help="print the starter config.toml")
    template.set_defaults(func=commands.print_template)


def _usage(parser: argparse.ArgumentParser) -> int:
    """Print ``vakr config`` help when no subcommand is given.

    Args:
        parser: The ``config`` parser.

    Returns:
        int: 2, the usage-error status.

    Raises:
        None
    """
    parser.print_help(sys.stderr)
    return 2
