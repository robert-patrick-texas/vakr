"""``vakr snmp discover|poll TARGET --credential NAME``: SNMP diagnostics.

``discover`` runs vakr's discovery against one device and prints what it
learned: the system group, every interface with its state, speed, counter
width, description, and addresses, and how completely each MIB table
answered. ``poll`` runs discovery and then one Stage 1/Stage 2 poll, and
with ``--rate-after SECONDS`` a second poll, so it can show traffic rates.

The device is named by address and a *credential name* from the protected
credentials file; a community is never accepted on the command line
(``vakr.cli.snmp_args``).

Exit status: 0 everything answered; 1 the device answered but some data is
missing; 2 usage or invalid target; 4 no usable answer from the device;
5 a collector-side problem (credential, DNS, socket), never a device problem.

Dependencies:
    vakr.adapters.snmp: Imported on first use; PySNMP takes time to load.
    vakr.discovery, vakr.collectors.snmp: Discovery and polling.
    vakr.credentials, vakr.config: Credentials and ``[snmp]`` settings.

Safety:
    Read-only SNMP. ``--dev`` relaxes credential-file permission checks for
    a developer workstation only.

Example:
    $ sudo vakr snmp discover 192.0.2.1 --credential network-v2c
"""

import argparse
import asyncio
import sys
from collections.abc import Sequence
from dataclasses import dataclass

from vakr.cli.common import SubParsers
from vakr.cli.snmp_args import common_arguments
from vakr.cli.snmp_poll_report import poll_report
from vakr.cli.snmp_report import discovery_report
from vakr.collectors.snmp.config import snmp_config
from vakr.collectors.snmp.plan import plan_from_discovery
from vakr.collectors.snmp.poll import SnmpPollResult, poll_device, snmp_state
from vakr.config.loader import load_effective_config
from vakr.credentials.provider import FileCredentialProvider
from vakr.discovery import DiscoveryResult, discover
from vakr.domain.enums import AddressFamily, SnmpState
from vakr.domain.errors import ValidationError
from vakr.domain.quality import Quality
from vakr.paths import InstallPaths
from vakr.ports.clock import SystemClock
from vakr.ports.snmp import SnmpError, SnmpSessionError, SnmpSettings, SnmpTarget

EXIT_OK, EXIT_PARTIAL, EXIT_USAGE, EXIT_NO_ANSWER, EXIT_COLLECTOR = 0, 1, 2, 4, 5
MAX_RATE_AFTER_S = 300.0
_NO_ANSWER_HINT = (
    "This does not mean the device is down: the community may be wrong, an ACL may drop this "
    "collector's requests, or SNMP may be off. Check reachability with ping separately."
)


@dataclass(frozen=True, slots=True)
class _Request:
    """One validated diagnostic request.

    Attributes:
        target: The agent.
        settings: The request budget.
        provider: Resolves the credential name.
        rate_after: Seconds before a second poll; 0 for none.
    """

    target: SnmpTarget
    settings: SnmpSettings
    provider: FileCredentialProvider
    rate_after: float = 0.0


def register(subparsers: SubParsers) -> None:
    """Add ``snmp`` with its ``discover`` and ``poll`` actions.

    Args:
        subparsers: The top-level sub-parser collection.

    Returns:
        None

    Raises:
        None
    """
    parser = subparsers.add_parser("snmp", help="query one device over SNMP (discovery, polling)")
    actions = parser.add_subparsers(title="actions", metavar="ACTION", required=True)
    discover_cmd = actions.add_parser(
        "discover", help="discover one device and print what it reports"
    )
    poll_cmd = actions.add_parser("poll", help="discover, then poll once (or twice, for rates)")
    for action, handler in ((discover_cmd, run_discover), (poll_cmd, run_poll)):
        common_arguments(action)
        action.set_defaults(func=handler)
    poll_cmd.add_argument(
        "--rate-after", type=float, default=0.0, metavar="SECONDS",
        help="poll again after SECONDS (1-300) and show traffic rates",
    )  # fmt: skip


def _request(args: argparse.Namespace) -> _Request | None:
    """Validate the arguments against the configuration.

    Args:
        args: Parsed arguments.

    Returns:
        _Request | None: The request, or ``None`` after printing why not.

    Raises:
        None
    """
    paths = InstallPaths.from_environment()
    cfg = snmp_config(load_effective_config(paths.config_dir, strict=False))
    timeout = cfg.settings.timeout_s if args.timeout is None else args.timeout
    retries = cfg.settings.retries if args.retries is None else args.retries
    rate_after = getattr(args, "rate_after", 0.0)
    problems = []
    if not 1 <= timeout <= 30:
        problems.append("--timeout must be from 1 to 30 seconds")
    if rate_after and not 1 <= rate_after <= MAX_RATE_AFTER_S:
        problems.append("--rate-after must be from 1 to 300 seconds")
    try:
        settings = SnmpSettings(timeout, retries, cfg.settings.max_varbinds,
                                max_repetitions=cfg.settings.max_repetitions,
                                max_walk_rows=cfg.settings.max_walk_rows)  # fmt: skip
        family = None if args.family is None else AddressFamily(args.family)
        port = cfg.port if args.port is None else args.port
        target = SnmpTarget(args.target, args.credential, port, family, source_address=args.source)
    except ValidationError as exc:
        problems.append(str(exc))
    if problems:
        for problem in problems:
            print(f"vakr: {problem}", file=sys.stderr)
        return None
    provider = FileCredentialProvider(paths.secrets_dir, development=args.dev)
    return _Request(target, settings, provider, rate_after)


def run_discover(args: argparse.Namespace) -> int:
    """Discover one device and print the report.

    Args:
        args: Parsed arguments.

    Returns:
        int: The exit status described in the module docstring.

    Raises:
        None
    """
    request = _request(args)
    return EXIT_USAGE if request is None else asyncio.run(_run(request, polls=0))


def run_poll(args: argparse.Namespace) -> int:
    """Discover one device, poll it, and print the report.

    Args:
        args: Parsed arguments.

    Returns:
        int: The exit status described in the module docstring.

    Raises:
        None
    """
    request = _request(args)
    if request is None:
        return EXIT_USAGE
    return asyncio.run(_run(request, polls=2 if request.rate_after else 1))


async def _run(request: _Request, polls: int) -> int:
    """Open a session, discover, and poll ``polls`` times.

    Args:
        request: The validated request.
        polls: 0 for discovery only, else 1 or 2 polls.

    Returns:
        int: The exit status.

    Raises:
        None
    """
    # Imported here because loading PySNMP takes a noticeable fraction of a second.
    from vakr.adapters.snmp import PysnmpSessionFactory

    clock, results = SystemClock(), []
    try:
        async with PysnmpSessionFactory(request.provider).open(
            request.target, request.settings
        ) as s:
            discovery = await discover(s, clock)
            plan = plan_from_discovery(discovery)
            for n in range(polls if discovery.answered else 0):
                if n:
                    await asyncio.sleep(request.rate_after)
                results.append(await poll_device(s, plan, clock))
                plan = plan.with_changes(results[-1].stage2.changes if results[-1].stage2 else ())
    except SnmpSessionError as exc:
        return _collector_problem(exc.error)
    finally:
        request.provider.forget()
    return _report(request, discovery, results)


def _collector_problem(error: SnmpError) -> int:
    """Report a collector-side failure.

    Args:
        error: The failure.

    Returns:
        int: ``EXIT_COLLECTOR``.

    Raises:
        None
    """
    print(f"vakr: {error.message}", file=sys.stderr)
    return EXIT_COLLECTOR


def _report(request: _Request, discovery: DiscoveryResult, polls: Sequence[SnmpPollResult]) -> int:
    """Print the report and choose the exit status.

    Args:
        request: The request.
        discovery: The discovery result.
        polls: Poll results, possibly empty.

    Returns:
        int: The exit status.

    Raises:
        None
    """
    collector = [t.error for t in discovery.subtables if t.error and t.error.collector_side]
    if collector:
        return _collector_problem(collector[0])
    if not discovery.answered:
        error = next((t.error for t in discovery.subtables if t.error), None)
        reason = "no answer" if error is None else error.message
        print(f"No SNMP answer from {request.target.label}: {reason}.\n{_NO_ANSWER_HINT}")
        return EXIT_NO_ANSWER
    if polls and snmp_state(polls[0]) is SnmpState.ERROR:
        reason = polls[0].error.message if polls[0].error else "nothing usable came back"
        print(f"{request.target.label} answered, but with nothing usable: {reason}.")
        print("Check the SNMP view configured on the device for this community.")
        return EXIT_NO_ANSWER
    credential = request.target.credential
    lines = poll_report(discovery, polls, credential) if polls else []
    print("\n".join(lines or discovery_report(discovery, credential)))
    incomplete = any(p.quality & (Quality.PARTIAL_RESPONSE | Quality.MISSING_FIELDS) for p in polls)
    return EXIT_PARTIAL if incomplete or not discovery.complete else EXIT_OK
