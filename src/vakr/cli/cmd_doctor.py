"""``vakr doctor``: human-readable diagnosis of the local vakr installation.

When collection looks wrong, an operator needs to tell quickly whether the
problem is vakr itself (configuration, permissions, egress, storage) or the
network. ``doctor`` runs every registered self-check (SVC-007) and prints one
line per check with its severity. ``--network`` adds the step-by-step
outbound HTTPS diagnosis to the GitHub hosts (EGRESS-005).

Dependencies:
    vakr.health.registry: The shared check list also used by ``check health``.

Safety:
    Read-only. Output is safe to paste into a ticket because checks never
    include secrets.

Example:
    $ vakr doctor
    [OK      ] version: vakr <version>
    [OK      ] python: Python 3.15.0
"""

import argparse

from vakr.cli.cmd_tls import network_checks
from vakr.cli.common import SubParsers
from vakr.health.model import worst
from vakr.health.registry import run_checks


def register(subparsers: SubParsers) -> None:
    """Add the ``doctor`` subcommand.

    Args:
        subparsers: The top-level sub-parser collection.

    Returns:
        None

    Raises:
        None
    """
    parser = subparsers.add_parser("doctor", help="diagnose this vakr installation")
    parser.add_argument(
        "--network", action="store_true", help="also test outbound HTTPS step by step (EGRESS-005)"
    )
    parser.set_defaults(func=run)


def run(args: argparse.Namespace) -> int:
    """Run all self-checks and print one line per result.

    Args:
        args: Parsed arguments; ``network`` adds the egress diagnosis.

    Returns:
        int: Exit code of the most severe result (0 OK, 1 WARNING,
        2 CRITICAL, 3 UNKNOWN).

    Raises:
        None
    """
    results = run_checks()
    if getattr(args, "network", False):
        results += network_checks()
    for result in results:
        print(f"[{result.status.name:<8}] {result.name}: {result.summary}")
    return worst(results).exit_code
