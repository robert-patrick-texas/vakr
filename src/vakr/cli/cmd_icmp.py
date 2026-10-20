"""``vakr icmp probe``: run one ICMP reachability cycle by hand (ICMP-001..012).

When a device looks wrong in vakr, an operator wants to see exactly what
the collector sees from this host: each probe's RTT, which ones were lost
and who said why, the state vakr derives (Appendix E.1), the summary, and
whether SNMP would run under the default ``icmp_gated`` strategy. A
collector problem, such as missing permission, is reported as such and
never as a device failure (OBS-002).

Exit status follows the Nagios plugin convention, so the command also works
as a CheckMK/Nagios check:

- 0: reachable;
- 1: impaired (it answers, with loss or high latency);
- 2: unreachable (no replies), or a command-line usage error;
- 3: unknown (the collector could not observe the device, or the target is
  invalid or does not resolve).

Dependencies:
    asyncio: Runs the cycle and bounded name resolution.
    vakr.adapters.icmp: The prober. vakr.collectors.icmp: Settings, summary.
    vakr.cli.icmp_render: Operator-language output.

Safety:
    Sends ICMP Echo only; never runs a shell command with the target.
    Name resolution is bounded by ``RESOLVE_TIMEOUT_S``.

Example:
    $ vakr icmp probe 192.0.2.1 --count 3
    probing 192.0.2.1 (IPv4) from the default source: 3 probes, 200 ms apart, 1000 ms timeout
      probe 1: reply in 1.204 ms
"""

import argparse
import asyncio
import ipaddress
import socket
import sys

from vakr.adapters.icmp.transport import SocketIcmpProber
from vakr.cli.common import SubParsers
from vakr.cli.icmp_render import EXIT_UNKNOWN, exit_code, render
from vakr.collectors.icmp.cycle import PollContext, build_cycle
from vakr.collectors.icmp.settings import IcmpSettings, settings_from_config
from vakr.config.loader import load_effective_config
from vakr.domain.errors import ValidationError
from vakr.domain.ids import CollectorId, DeviceId, PollId, new_id
from vakr.paths import InstallPaths
from vakr.ports.icmp import ProbeRequest

RESOLVE_TIMEOUT_S = 2.0
"""Most time spent resolving a host name (Appendix E.22)."""


def register(subparsers: SubParsers) -> None:
    """Add the ``icmp`` command with its ``probe`` subcommand.

    Args:
        subparsers: The top-level sub-parser collection.

    Returns:
        None

    Raises:
        None
    """
    parser = subparsers.add_parser("icmp", help="ICMP reachability diagnostics")
    parser.set_defaults(func=lambda _args: parser.print_help(sys.stderr) or 2)
    actions = parser.add_subparsers(title="subcommands", metavar="SUBCOMMAND")
    probe = actions.add_parser("probe", help="send one probe group and explain the result")
    probe.add_argument("target", metavar="TARGET", help="IP address or host name")
    probe.add_argument("--count", type=int, help="probes to send (default: icmp.probes)")
    probe.add_argument(
        "--interval-ms", type=int, metavar="MS", help="time between probes (icmp.spacing)"
    )
    probe.add_argument("--timeout-ms", type=int, metavar="MS", help="wait per probe (icmp.timeout)")
    probe.add_argument("--source", metavar="ADDR", help="source address to send from")
    probe.set_defaults(func=run_probe)


def configured_settings() -> IcmpSettings:
    """Load the ``[icmp]`` settings, falling back to the packaged defaults.

    Args:
        None

    Returns:
        IcmpSettings: Settings from ``/etc/vakr`` when readable, else defaults.

    Raises:
        None
    """
    try:
        config = load_effective_config(InstallPaths.from_environment().config_dir, strict=False)
        return settings_from_config(config)
    except OSError, ValueError:
        return IcmpSettings()


async def resolve(target: str, source: str | None) -> str:
    """Turn a target into a canonical IP literal, resolving a name if needed.

    Args:
        target: IP literal or host name.
        source: Source address; its family restricts resolution.

    Returns:
        str: The canonical address.

    Raises:
        OSError: If the name does not resolve.
        TimeoutError: If resolution takes longer than ``RESOLVE_TIMEOUT_S``.
    """
    try:
        return str(ipaddress.ip_address(target))
    except ValueError:
        pass
    family = socket.AF_UNSPEC
    if source is not None and ":" in source:
        family = socket.AF_INET6
    elif source is not None:
        family = socket.AF_INET
    loop = asyncio.get_running_loop()
    async with asyncio.timeout(RESOLVE_TIMEOUT_S):
        infos = await loop.getaddrinfo(target, None, family=family, type=socket.SOCK_DGRAM)
    return str(ipaddress.ip_address(infos[0][4][0]))


def build_request(args: argparse.Namespace, address: str, settings: IcmpSettings) -> ProbeRequest:
    """Combine command-line overrides with the configured settings.

    Args:
        args: Parsed ``probe`` arguments.
        address: Resolved canonical target.
        settings: Configured settings.

    Returns:
        ProbeRequest: The cycle to run.

    Raises:
        ValidationError: If an address or bound is not acceptable.
    """
    count = settings.probes if args.count is None else args.count
    spacing = settings.spacing_s if args.interval_ms is None else args.interval_ms / 1000
    timeout = settings.timeout_s if args.timeout_ms is None else args.timeout_ms / 1000
    return ProbeRequest(address, count, spacing, timeout, args.source)


async def _probe(args: argparse.Namespace) -> int:
    """Resolve, probe, print, and choose the exit status.

    Args:
        args: Parsed ``probe`` arguments.

    Returns:
        int: Exit status (see the module docstring).

    Raises:
        None
    """
    settings = configured_settings()
    try:
        address = await resolve(args.target, args.source)
        request = build_request(args, address, settings)
    except (OSError, TimeoutError) as exc:
        print(f"vakr icmp probe: cannot resolve {args.target}: {exc}", file=sys.stderr)
        return EXIT_UNKNOWN
    except ValidationError as exc:
        print(f"vakr icmp probe: {exc}", file=sys.stderr)
        return EXIT_UNKNOWN
    async with SocketIcmpProber(max_in_flight=max(16, request.count)) as prober:
        result = await prober.probe(request)
    context = PollContext(new_id(PollId), new_id(CollectorId), new_id(DeviceId))
    cycle = build_cycle(result, context, settings.policy)
    shown = args.target if args.target != address else None
    print("\n".join(render(cycle, shown)))
    return exit_code(cycle)


def run_probe(args: argparse.Namespace) -> int:
    """Run ``vakr icmp probe``.

    Args:
        args: Parsed arguments.

    Returns:
        int: 0 reachable, 1 impaired, 2 unreachable, 3 unknown.

    Raises:
        None
    """
    return asyncio.run(_probe(args))
