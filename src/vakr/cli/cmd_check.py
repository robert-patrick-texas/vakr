"""``vakr check health``: Nagios/CheckMK plugin for vakr's own health (OPS-001).

NetOps already alerts from CheckMK and Nagios, so vakr does not page anyone
itself. Instead this command behaves like a standard monitoring plugin: one
output line with performance data and an exit code of 0, 1, 2, or 3.

Dependencies:
    vakr.health: Shared checks and Nagios rendering.

Safety:
    Read-only and quick; safe to run every minute from a monitoring agent.

Example:
    $ vakr check health
    VAKR OK - version: vakr <version>; python: Python 3.15.0
"""

import argparse

from vakr.cli.common import SubParsers
from vakr.health.model import nagios_line, worst
from vakr.health.registry import run_checks


def register(subparsers: SubParsers) -> None:
    """Add the ``check`` subcommand with its ``health`` target.

    Args:
        subparsers: The top-level sub-parser collection.

    Returns:
        None

    Raises:
        None
    """
    parser = subparsers.add_parser("check", help="monitoring-plugin checks")
    targets = parser.add_subparsers(title="targets", metavar="TARGET", required=True)
    health = targets.add_parser("health", help="Nagios-format self-health")
    health.set_defaults(func=run_health)


def run_health(args: argparse.Namespace) -> int:  # noqa: ARG001 - uniform handler signature
    """Print the Nagios plugin line and return the plugin exit code.

    Args:
        args: Parsed arguments (unused).

    Returns:
        int: Nagios exit code of the most severe check.

    Raises:
        None
    """
    results = run_checks()
    print(nagios_line(results))
    return worst(results).exit_code
