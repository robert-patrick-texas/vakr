"""``vakr config``: placeholder until milestone M1 delivers it.

Configuration layering, versioned upgrades, and new-option reporting
(blueprint 28.4). The command is registered now so the CLI surface,
packaging, and tests are stable while the implementation is built in
parallel.

Dependencies:
    vakr.cli.common: Shared placeholder handler.

Safety:
    The placeholder changes nothing and exits with status 3.

Example:
    $ vakr config
    vakr: config is not implemented in this release yet
"""

from vakr.cli.common import SubParsers, not_implemented


def register(subparsers: SubParsers) -> None:
    """Add the ``config`` subcommand.

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
    parser.set_defaults(func=not_implemented, command_name="config")
