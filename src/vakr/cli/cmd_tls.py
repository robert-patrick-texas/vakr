"""``vakr tls``: placeholder until milestone M1 delivers it.

Enterprise TLS-inspection CA management for outbound HTTPS (blueprint
28.3.1). The command is registered now so the CLI surface, packaging, and
tests are stable while the implementation is built in parallel.

Dependencies:
    vakr.cli.common: Shared placeholder handler.

Safety:
    The placeholder changes nothing and exits with status 3.

Example:
    $ vakr tls
    vakr: tls is not implemented in this release yet
"""

from vakr.cli.common import SubParsers, not_implemented


def register(subparsers: SubParsers) -> None:
    """Add the ``tls`` subcommand.

    Args:
        subparsers: The top-level sub-parser collection.

    Returns:
        None

    Raises:
        None
    """
    parser = subparsers.add_parser("tls", help="manage extra trusted CA certificates (EGRESS-003)")
    parser.set_defaults(func=not_implemented, command_name="tls")
