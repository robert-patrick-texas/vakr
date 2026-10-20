"""``vakr collector run`` and ``vakr collector once DEVICE`` (6.2, SVC-001..005).

``run`` is the foreground collector process that ``vakr-collector.service``
starts: it runs the startup preflight, then polls the inventory until
SIGTERM, and reloads its configuration on SIGHUP. Operators normally use
``systemctl start|stop|reload vakr-collector`` instead of running it by
hand; see docs/operations/collector.md.

``once`` polls one device a single time, exactly as the collector would
(ICMP, the gate, Stage 1, Stage 2), and explains the result in plain
language. It writes nothing and is safe beside a running collector. Its
exit status follows the Nagios convention: 0 OK, 1 warning, 2 critical
(down), 3 unknown (collector-side trouble), and 78 when the configuration
or database cannot be used.

Dependencies:
    argparse, asyncio: Parsing and the event loop.
    vakr.app.collector: The service and the diagnostic cycle (imported on use,
        because loading PySNMP takes a noticeable fraction of a second).

Safety:
    No community or other secret is accepted on the command line.

Example:
    $ sudo -u vakr vakr collector once core1
    core1 (192.0.2.1) polled once as collector vakr-east: icmp_gated, every 60 s
"""

import argparse
import asyncio
import logging
import sys

from vakr.cli.common import SubParsers
from vakr.paths import InstallPaths


def register(subparsers: SubParsers) -> None:
    """Add ``collector`` with its ``run`` and ``once`` actions.

    Args:
        subparsers: The top-level sub-parser collection.

    Returns:
        None

    Raises:
        None
    """
    parser = subparsers.add_parser("collector", help="the headless collector service")
    actions = parser.add_subparsers(title="actions", metavar="ACTION", required=True)
    run = actions.add_parser("run", help="run the collector in the foreground (the service)")
    run.add_argument("--debug", action="store_true", help="also log debug messages")
    once = actions.add_parser("once", help="poll one device once and explain the result")
    once.add_argument("device", metavar="DEVICE", help="device name or UUID")
    for action in (run, once):
        action.add_argument(
            "--dev",
            action="store_true",
            help="developer workstation: warn instead of refusing unsafe credential permissions",
        )
    run.set_defaults(func=run_service)
    once.set_defaults(func=run_once)


def run_service(args: argparse.Namespace) -> int:
    """Run the collector until it is stopped.

    Args:
        args: Parsed arguments.

    Returns:
        int: 0 after a clean stop, 1 after a failure, 78 when the startup
        preflight refused to start.

    Raises:
        None
    """
    # Imported here so `vakr --help` does not pay for loading PySNMP.
    from vakr.app.collector.entry import run_collector

    level = logging.DEBUG if args.debug else logging.INFO
    paths = InstallPaths.from_environment()
    return asyncio.run(run_collector(paths, development=args.dev, level=level))


def run_once(args: argparse.Namespace) -> int:
    """Poll one device once and print the report.

    Args:
        args: Parsed arguments.

    Returns:
        int: The Nagios-style status, or 78 when the setup is unusable.

    Raises:
        None
    """
    # Imported here so `vakr --help` does not pay for loading PySNMP.
    from vakr.app.collector.entry import EX_PREFLIGHT
    from vakr.app.collector.once import poll_once
    from vakr.app.collector.once_report import render_once
    from vakr.app.collector.preflight import PreflightError
    from vakr.ports.clock import SystemClock

    paths = InstallPaths.from_environment()
    try:
        result = asyncio.run(poll_once(paths, args.device, development=args.dev))
    except PreflightError as exc:
        print(f"vakr: {exc.check}: {exc.message}", file=sys.stderr)
        return EX_PREFLIGHT
    lines, status = render_once(result, SystemClock().now_utc())
    print("\n".join(lines))
    return status
