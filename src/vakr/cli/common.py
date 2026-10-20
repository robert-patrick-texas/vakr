"""Helpers shared by ``vakr`` subcommand modules.

Subcommands are registered on an argparse sub-parser collection and run
handlers that return an integer exit status. Keeping the alias and the
placeholder behavior here gives every command module the same shape.

Dependencies:
    argparse: The parser objects subcommands register on.

Safety:
    The placeholder handler changes nothing and says so, so an operator who
    runs an unfinished command on production is never left guessing.

Example:
    >>> NOT_IMPLEMENTED_EXIT
    3
"""

import argparse
import sys

type SubParsers = argparse._SubParsersAction[argparse.ArgumentParser]

NOT_IMPLEMENTED_EXIT = 3
"""Exit status for commands that exist in the CLI but are not built yet."""


def not_implemented(args: argparse.Namespace) -> int:
    """Report that a subcommand is planned but not yet implemented.

    Args:
        args: Parsed arguments; ``args.command_name`` names the command.

    Returns:
        int: ``NOT_IMPLEMENTED_EXIT``.

    Raises:
        None
    """
    name = getattr(args, "command_name", "this command")
    print(f"vakr: {name} is not implemented in this release yet", file=sys.stderr)
    return NOT_IMPLEMENTED_EXIT
