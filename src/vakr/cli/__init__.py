"""Command-line interface for operators and maintenance automation.

Each subcommand lives in its own ``cmd_<name>`` module exposing
``register(subparsers)``; ``vakr.cli.main`` lists those modules explicitly.
That keeps subcommands independent (separate owners, separate tests) while
the full command surface stays visible in one short file.

Dependencies:
    argparse: Standard-library parsing keeps the CLI usable before any
        optional dependency is installed, which matters for recovery commands.

Safety:
    Commands never accept secrets as arguments, because arguments are visible
    to other local users through the process table.

Example:
    >>> from vakr.cli.main import build_parser
    >>> build_parser().prog
    'vakr'
"""
