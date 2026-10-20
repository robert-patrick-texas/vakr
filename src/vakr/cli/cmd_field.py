"""``vakr field``: placeholder until milestone M1 delivers it.

Production data collection into one encrypted bundle (blueprint 28.5). The
command is registered now so the CLI surface, packaging, and tests are
stable while the implementation is built in parallel.

Dependencies:
    vakr.cli.common: Shared placeholder handler.

Safety:
    The placeholder changes nothing and exits with status 3.

Example:
    $ vakr field
    vakr: field is not implemented in this release yet
"""

from vakr.cli.common import SubParsers, not_implemented


def register(subparsers: SubParsers) -> None:
    """Add the ``field`` subcommand.

    Args:
        subparsers: The top-level sub-parser collection.

    Returns:
        None

    Raises:
        None
    """
    parser = subparsers.add_parser(
        "field", help="collect a field data bundle for developers (FIELD-*)"
    )
    parser.set_defaults(func=not_implemented, command_name="field")
