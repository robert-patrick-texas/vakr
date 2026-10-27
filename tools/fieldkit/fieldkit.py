# Copyright 2026 Robert Patrick
# SPDX-License-Identifier: Apache-2.0
r"""Collect production facts into one encrypted bundle for the developers (FIELD-001..010).

Development happens away from the production site (blueprint 28.1). When
developers need facts from production (host details, SNMP walks of the real
devices, RTT between the east and west positions, vakr's own diagnostics),
the operator runs one command and sends back one file. That command is this
tool. It uses only the Python standard library, so it runs on a host where
vakr is not installed yet, and it ships as a single zipapp built by
``build_pyz.py``.

Dependencies:
    fieldkit_collect: Preflight, profiles, bundle, and encryption.
    fieldkit_run: Build information embedded in the zipapp.

Safety:
    The tool changes nothing on the host except its output directory and,
    with ``--pseudonymize``, its own pseudonym key. SNMP communities are read
    from a protected file or a prompt; there is deliberately no option that
    takes one on the command line (FIELD-004).

Example:
    $ uv run --python 3.15 vakr-fieldkit.pyz collect --profile host,snmp,paths \
        --devices devices.txt --community-file ~/.vakr-community --peer vakr-west
    vakr-fieldkit: wrote ./vakr-field-east1-20261020T101500Z.tar.gz.p7m; next: ...
"""

import argparse
import sys
from collections.abc import Callable, Sequence
from pathlib import Path

from fieldkit_collect import run_collect
from fieldkit_plan import profile_names
from fieldkit_run import BuildInfo, load_build_info

MIB = 1024 * 1024


def _bounded(kind: Callable[[str], float], low: float, high: float) -> Callable[[str], float]:
    """Make an argparse type that accepts numbers in ``[low, high]``.

    Args:
        kind: ``int`` or ``float``.
        low: Smallest accepted value.
        high: Largest accepted value.

    Returns:
        Callable[[str], float]: Converter raising ``ArgumentTypeError``.

    Raises:
        None
    """

    def convert(text: str) -> float:
        """Convert and range-check one option value.

        Args:
            text: Raw option value.

        Returns:
            float: The parsed number.

        Raises:
            argparse.ArgumentTypeError: If it is not a number in range.
        """
        try:
            value = kind(text)
        except ValueError:
            raise argparse.ArgumentTypeError(f"not a number: {text!r}") from None
        if not low <= value <= high:
            raise argparse.ArgumentTypeError(f"must be between {low:g} and {high:g}")
        return value

    return convert


def _add_inputs(parser: argparse.ArgumentParser, info: BuildInfo) -> None:
    """Add the options that choose what is collected.

    Args:
        parser: The ``collect`` parser.
        info: Build information (names the application profile).

    Returns:
        None

    Raises:
        None
    """
    group = parser.add_argument_group("what to collect")
    names = ",".join(profile_names(info))
    group.add_argument("--profile", default="host", help=f"comma-separated: {names}")
    group.add_argument("--devices", type=Path, metavar="FILE", help="one device per line")
    group.add_argument(
        "--community-file",
        type=Path,
        metavar="FILE",
        help="file holding the SNMPv2c community (mode 0600); without it you are prompted",
    )
    group.add_argument("--peer", metavar="HOST", help="the other collection position")
    group.add_argument("--ping-devices", type=_bounded(int, 0, 100), default=5, metavar="N")
    group.add_argument("--extra-oid", action="append", default=[], metavar="OID")
    group.add_argument("--pseudonymize", action="store_true", help="replace identifying values")
    group.add_argument("--keep-keyword", action="append", default=[], metavar="WORD")


def _add_compare(parser: argparse.ArgumentParser) -> None:
    """Add the options of the parallel-run profiles (``mrtg``, ``smokeping``, ``acceptance``).

    Args:
        parser: The ``collect`` parser.

    Returns:
        None

    Raises:
        None
    """
    group = parser.add_argument_group("parallel run with MRTG and SmokePing")
    add = group.add_argument
    add("--mrtg-config", action="append", default=[], metavar="FILE",
        help="MRTG configuration, * allowed (default /etc/mrtg.cfg, /etc/mrtg/*.cfg)")  # fmt: skip
    add("--smokeping-config", action="append", default=[], metavar="FILE",
        help="SmokePing configuration; default /etc/smokeping/config")  # fmt: skip
    add("--compare-hours", type=_bounded(float, 1, 744), default=50.0, metavar="H",
        help="data window ending now (default 50: MRTG logs keep 5-minute rows ~50 h)")  # fmt: skip
    add("--max-targets", type=_bounded(int, 1, 20000), default=5000, metavar="N")
    add("--acceptance-targets", type=Path, metavar="FILE",
        help="interfaces and hosts to export when MRTG/SmokePing run elsewhere")  # fmt: skip
    add("--acceptance-max-rows", type=_bounded(int, 1, 20_000_000), default=5_000_000, metavar="N")
    add("--rrdtool", default="rrdtool", metavar="PATH")


def _add_outputs(parser: argparse.ArgumentParser) -> None:
    """Add output, encryption, and egress options.

    Args:
        parser: The ``collect`` parser.

    Returns:
        None

    Raises:
        None
    """
    group = parser.add_argument_group("output and encryption")
    group.add_argument("--output-dir", type=Path, default=Path(), metavar="DIR")
    group.add_argument("--no-encrypt", action="store_true", help="only for bundles kept here")
    group.add_argument("--keep-plaintext", action="store_true")
    group.add_argument("--recipient", type=Path, metavar="CERT", help="override recipient")
    group.add_argument("--openssl", default="openssl", metavar="PATH")
    egress = parser.add_argument_group("GitHub reachability test")
    egress.add_argument("--proxy", metavar="URL", help="http://[user:pass@]host:port")
    egress.add_argument("--ca-file", type=Path, metavar="PEM", help="extra trusted CA")
    egress.add_argument(
        "--no-strict-x509", action="store_true", help="relax only the strict X.509 profile"
    )
    egress.add_argument("--no-egress", action="store_true", help="skip the test")
    egress.add_argument("--egress-url", action="append", default=[], metavar="URL")
    egress.add_argument("--egress-head", action="append", default=[], metavar="URL")
    egress.add_argument("--egress-timeout", type=_bounded(float, 1, 120), default=15.0)


def _add_limits(parser: argparse.ArgumentParser) -> None:
    """Add the bounds that keep a run short and gentle on devices (FIELD-009).

    Args:
        parser: The ``collect`` parser.

    Returns:
        None

    Raises:
        None
    """
    group = parser.add_argument_group("limits")
    group.add_argument("--time-budget", type=_bounded(float, 10, 14400), default=1800.0)
    group.add_argument("--max-bytes", type=_bounded(int, MIB, 4096 * MIB), default=256 * MIB)
    group.add_argument("--max-member-bytes", type=_bounded(int, 1024, 1024 * MIB), default=32 * MIB)
    group.add_argument("--max-devices", type=_bounded(int, 1, 5000), default=500)
    group.add_argument("--walk-timeout", type=_bounded(float, 1, 600), default=60.0)
    group.add_argument("--device-pause", type=_bounded(float, 0, 60), default=1.0)
    group.add_argument("--uptime-gap", type=_bounded(float, 0, 60), default=5.0)
    group.add_argument("--snmp-timeout", type=_bounded(int, 1, 30), default=2)
    group.add_argument("--snmp-retries", type=_bounded(int, 0, 5), default=1)
    group.add_argument("--journal-lines", type=_bounded(int, 0, 100000), default=5000)


def build_parser(info: BuildInfo) -> argparse.ArgumentParser:
    """Create the command-line parser.

    Args:
        info: Build information for names and defaults.

    Returns:
        argparse.ArgumentParser: Parser whose ``collect`` command sets ``func``.

    Raises:
        None
    """
    parser = argparse.ArgumentParser(
        prog=info.tool_name, description="Collect production facts into one encrypted bundle."
    )
    parser.add_argument("--version", action="version", version=f"{info.tool_name} {info.version}")
    commands = parser.add_subparsers(title="commands", metavar="COMMAND")
    collect = commands.add_parser(
        "collect",
        help="collect a bundle",
        description="Collect the chosen profiles into one bundle, encrypted by default.",
    )
    _add_inputs(collect, info)
    _add_compare(collect)
    _add_outputs(collect)
    _add_limits(collect)
    collect.set_defaults(func=run_collect)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Run the field kit.

    Args:
        argv: Arguments without the program name; ``None`` means ``sys.argv[1:]``.

    Returns:
        int: 0 when a bundle was written, 1 when collection or encryption
        failed, 2 for usage and preflight errors, 130 when interrupted.

    Raises:
        SystemExit: From argparse for ``--help``, ``--version``, and usage errors.
    """
    info = load_build_info()
    parser = build_parser(info)
    args = parser.parse_args(argv)
    if not hasattr(args, "func"):
        parser.print_help(sys.stderr)
        return 2
    return int(args.func(args, info))


if __name__ == "__main__":
    sys.exit(main())
