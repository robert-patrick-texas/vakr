"""``vakr self-update``: placeholder until milestone M1 delivers it.

Verified self-update from GitHub releases with rollback (blueprint 28.3).
The command is registered now so the CLI surface, packaging, and tests are
stable while the implementation is built in parallel.

Dependencies:
    vakr.cli.common: Shared placeholder handler.

Safety:
    The placeholder changes nothing and exits with status 3.

Example:
    $ vakr self-update
    vakr: self-update is not implemented in this release yet
"""

from vakr.cli.common import SubParsers, not_implemented


def register(subparsers: SubParsers) -> None:
    """Add the ``self-update`` subcommand.

    Args:
        subparsers: The top-level sub-parser collection.

    Returns:
        None

    Raises:
        None
    """
    parser = subparsers.add_parser(
        "self-update", help="update vakr from signed GitHub releases (UPD-*)"
    )
    parser.set_defaults(func=not_implemented, command_name="self-update")
