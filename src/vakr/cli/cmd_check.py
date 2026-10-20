"""``vakr check health``: Nagios/CheckMK plugin for vakr's own health (OPS-001).

NetOps already alerts from CheckMK and Nagios, so vakr does not page anyone
itself. Instead this command behaves like a standard monitoring plugin: one
output line with performance data and an exit code of 0, 1, 2, or 3.

Self-update also runs it through the newly installed binary as its final
post-install step. Services that were just restarted can briefly look
CRITICAL, so ``--retry-for`` (or ``VAKR_HEALTH_RETRY_FOR``, which older
releases ignore) keeps re-checking while the result is CRITICAL, instead of
triggering a needless rollback (UPD-006).

Dependencies:
    vakr.health: Shared checks and Nagios rendering.
    time: Monotonic clock and sleep for the bounded retry window.

Safety:
    Read-only. Without a retry window it is quick and safe to run every
    minute from a monitoring agent; the retry window is capped at 600 s.

Example:
    $ vakr check health
    VAKR OK - version: vakr <version>; python: Python 3.15.0
"""

import argparse
import os
import time
from collections.abc import Callable

from vakr.cli.common import SubParsers
from vakr.health.model import CheckResult, Status, nagios_line, worst
from vakr.health.registry import run_checks

RETRY_ENV = "VAKR_HEALTH_RETRY_FOR"
"""Environment variable self-update uses to pass the settle window."""

MAX_RETRY_SECONDS = 600
RETRY_INTERVAL = 2.0


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
    health.add_argument(
        "--retry-for",
        type=int,
        metavar="SECONDS",
        help=f"re-check while CRITICAL for up to SECONDS (default: ${RETRY_ENV} or 0)",
    )
    health.set_defaults(func=run_health)


def retry_window(flag: int | None, environ: dict[str, str] | os._Environ[str]) -> int:
    """Choose the retry window from the flag, then the environment, capped.

    Args:
        flag: Value of ``--retry-for``, or ``None`` when not given.
        environ: Process environment, read for ``VAKR_HEALTH_RETRY_FOR``.

    Returns:
        int: Seconds to keep re-checking while CRITICAL, 0 to check once.

    Raises:
        None
    """
    if flag is None:
        raw = environ.get(RETRY_ENV, "0")
        flag = int(raw) if raw.strip().isdigit() else 0
    return max(0, min(flag, MAX_RETRY_SECONDS))


def settle(
    window: int,
    check: Callable[[], tuple[CheckResult, ...]] = run_checks,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> tuple[CheckResult, ...]:
    """Run the checks, repeating while CRITICAL until ``window`` seconds pass.

    Args:
        window: Seconds to keep retrying; 0 runs the checks once.
        check: Function returning one evaluation of all checks.
        clock: Monotonic clock, injectable for tests.
        sleep: Sleep function, injectable for tests.

    Returns:
        tuple[CheckResult, ...]: The first non-CRITICAL evaluation, or the
        last one when the window expires.

    Raises:
        None
    """
    deadline = clock() + window
    results = check()
    while worst(results) is Status.CRITICAL and clock() < deadline:
        sleep(min(RETRY_INTERVAL, max(0.0, deadline - clock())))
        results = check()
    return results


def run_health(args: argparse.Namespace) -> int:
    """Print the Nagios plugin line and return the plugin exit code.

    Args:
        args: Parsed arguments; ``retry_for`` may set a settle window.

    Returns:
        int: Nagios exit code of the most severe check.

    Raises:
        None
    """
    results = settle(retry_window(getattr(args, "retry_for", None), os.environ))
    print(nagios_line(results))
    return worst(results).exit_code
