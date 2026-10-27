r"""``vakr acceptance export``: vakr's measurements for the MRTG/SmokePing comparison.

MVP-ACC-03 and MVP-ACC-04 compare vakr with MRTG and SmokePing during a
parallel run of at least 14 days. This command writes this node's own
measurements over a window, for the interfaces MRTG graphs and the hosts
SmokePing probes (``--targets``): every interface sample's interval,
in/out bits per second, ``ifIndex``, and quality flags, and every ICMP
cycle's probe RTTs. The field kit's ``acceptance`` profile runs it and
puts the result in a bundle; ``tools/acceptance`` on the development host
re-aggregates it over MRTG's and SmokePing's exact windows.

Output is one member stream on standard output (``vakr.query.member_stream``)
or ``--output FILE``. Exit status: 0 written (``export.json`` says whether
the row budget cut it short); 2 usage error or invalid targets file;
4 the databases are unavailable.

Dependencies:
    vakr.adapters.sqlite: The stores (this module is the composition root).
    vakr.query.acceptance, acceptance_stream, acceptance_targets: Plan, reads, encoding.
    vakr.cli.service_user, status_stores: Privileges and configuration.

Safety:
    Read-only. The targets file and ``--output`` are opened before a root
    invocation switches to the ``vakr`` user (as ``vakr status`` does), so
    files the operator owns stay usable and no database file becomes
    root-owned. Bounded by the window (at most 31 days) and ``--max-rows``.

Example:
    $ sudo vakr acceptance export --targets mrtg-targets.txt --since 50h > export.txt
    vakr acceptance export: 412 interfaces on 96 devices, 1236004 samples, 28800 ICMP cycles
"""

import argparse
import contextlib
import socket
import sys
from collections.abc import Generator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TextIO

from vakr.adapters.sqlite.metadata import METADATA_FILE, SqliteMetadataStore, sqlite_uow_factory
from vakr.adapters.sqlite.telemetry import (
    TELEMETRY_FILE,
    SqliteTelemetryStore,
    sqlite_telemetry_factory,
)
from vakr.cli.common import SubParsers
from vakr.cli.service_user import drop_to_service_user
from vakr.cli.status_stores import load_config
from vakr.domain.errors import StorageError, ValidationError
from vakr.domain.series import TimeWindow
from vakr.lifecycle.configmerge.values import parse_duration
from vakr.paths import InstallPaths
from vakr.ports.migrations import MigrationError
from vakr.query import settings_from_config
from vakr.query.acceptance import DEFAULT_MAX_ROWS, MAX_ROWS, AcceptanceExport, check_window
from vakr.query.acceptance_stream import write_export
from vakr.query.acceptance_targets import TargetSpec, parse_targets
from vakr.version import get_version

COMMAND = "vakr acceptance export"
EXIT_OK, EXIT_USAGE, EXIT_STORAGE = 0, 2, 4
DEFAULT_SINCE = "50h"
"""MRTG's ``.log`` files keep 5-minute rows for about 50 hours (E.34)."""
MAX_TARGETS_BYTES = 4 * 1024 * 1024


def register(subparsers: SubParsers) -> None:
    """Add ``export`` to the ``acceptance`` group.

    Args:
        subparsers: The ``acceptance`` group's sub-parser collection.

    Returns:
        None

    Raises:
        None
    """
    parser = subparsers.add_parser(
        "export",
        help="write vakr's samples and ICMP cycles for the MRTG/SmokePing comparison",
        description="Write this node's interface samples and ICMP cycles over a window "
        "as a member stream (MVP-ACC-03, MVP-ACC-04).",
    )
    add = parser.add_argument
    add("--targets", type=Path, metavar="FILE", help="interfaces and hosts (default: all)")
    add("--since", default=DEFAULT_SINCE, metavar="DURATION",
        help=f"window length ending at --end (default {DEFAULT_SINCE})")  # fmt: skip
    add("--end", metavar="UTC", help="window end, ISO 8601 (default now)")
    add("--collector", default="", metavar="NAME", help="position (default this node's)")
    add("--max-rows", type=int, default=DEFAULT_MAX_ROWS, metavar="N",
        help=f"row budget, 1 to {MAX_ROWS} (default {DEFAULT_MAX_ROWS})")  # fmt: skip
    add("--output", type=Path, metavar="FILE", help="write here, not stdout")
    parser.set_defaults(func=run_export)


def _window(args: argparse.Namespace, now: datetime) -> TimeWindow:
    """Build the export window from ``--since`` and ``--end``.

    Args:
        args: Parsed arguments.
        now: The current time (UTC).

    Returns:
        TimeWindow: The window.

    Raises:
        ValueError: If a duration or time cannot be parsed.
        ValidationError: If the window breaks a bound.
    """
    end = now
    if args.end:
        end = datetime.fromisoformat(args.end.replace("Z", "+00:00"))
        if end.tzinfo is None:
            raise ValueError("--end needs a time zone, such as 2026-10-27T12:00:00Z")
        end = end.astimezone(UTC)
    window = TimeWindow(end - timedelta(seconds=parse_duration(args.since)), end)
    check_window(window, now)
    return window


def _targets(path: Path | None) -> list[TargetSpec] | None:
    """Read and parse the targets file.

    Args:
        path: The file, or ``None`` for everything.

    Returns:
        list[TargetSpec] | None: The targets.

    Raises:
        OSError: If it cannot be read.
        ValidationError: If it is malformed or too large.
    """
    if path is None:
        return None
    data = path.read_bytes()
    if len(data) > MAX_TARGETS_BYTES:
        raise ValidationError("the targets file is larger than 4 MiB", field="targets")
    return parse_targets(data.decode("utf-8"))


@contextlib.contextmanager
def _stores(paths: InstallPaths) -> Generator[tuple[SqliteMetadataStore, SqliteTelemetryStore]]:
    """Open both databases, which must exist; never create one.

    Args:
        paths: Installation layout.

    Returns:
        Generator: The metadata and telemetry stores, closed on exit.

    Raises:
        StorageError: If a database is missing.
        MigrationError: If a schema is not current.
    """
    meta_path, tele_path = paths.state_dir / METADATA_FILE, paths.state_dir / TELEMETRY_FILE
    for path in (meta_path, tele_path):
        if not path.exists():
            raise StorageError(f"{path} does not exist", retryable=False)
    meta = SqliteMetadataStore(meta_path)
    try:
        tele = SqliteTelemetryStore(tele_path)
    except BaseException:
        meta.close()
        raise
    try:
        yield meta, tele
    finally:
        tele.close()
        meta.close()


def _export(args: argparse.Namespace, targets: list[TargetSpec] | None, out: TextIO) -> int:
    """Plan and write the export after privileges are settled.

    Args:
        args: Parsed arguments.
        targets: The targets, or ``None``.
        out: Where the stream goes.

    Returns:
        int: Exit status.

    Raises:
        StorageError, MigrationError, ValidationError: Reported by the caller.
    """
    paths = InstallPaths.from_environment()
    config = load_config(paths, COMMAND)
    host = socket.gethostname()
    settings = settings_from_config(config, host)
    with _stores(paths) as (meta, tele):
        service = AcceptanceExport(sqlite_uow_factory(meta), sqlite_telemetry_factory(tele),
                                   settings)  # fmt: skip
        window = _window(args, service.clock.now_utc())
        plan = service.plan(targets, args.collector)
        about = {"tool": COMMAND, "vakr_version": get_version(), "host": host,
                 "targets_file": args.targets is not None}  # fmt: skip
        counts = write_export(service, plan, window, out, max_rows=args.max_rows, about=about)
    ifaces = sum(len(d.interfaces) for d in plan.devices)
    note = "; the row budget cut it short" if counts.truncated or plan.truncated else ""
    unmatched = f"; {len(plan.unmatched)} targets matched nothing" if plan.unmatched else ""
    print(f"{COMMAND}: {ifaces} interfaces on {len(plan.devices)} devices, {counts.rates} "
          f"samples, {counts.icmp} ICMP cycles{unmatched}{note}", file=sys.stderr)  # fmt: skip
    return EXIT_OK


def run_export(args: argparse.Namespace) -> int:
    """Handle ``vakr acceptance export``.

    Args:
        args: Parsed arguments.

    Returns:
        int: 0 written, 2 usage or targets error, 4 storage unavailable.

    Raises:
        None
    """
    if not 1 <= args.max_rows <= MAX_ROWS:
        print(f"{COMMAND}: --max-rows must be 1 to {MAX_ROWS}", file=sys.stderr)
        return EXIT_USAGE
    try:
        targets = _targets(args.targets)
        out = sys.stdout if args.output is None else args.output.open("w", encoding="utf-8")
    except (OSError, UnicodeDecodeError, ValidationError) as exc:
        print(f"{COMMAND}: {exc}", file=sys.stderr)
        return EXIT_USAGE
    try:
        if (problem := drop_to_service_user()) is not None:
            print(f"{COMMAND}: {problem}", file=sys.stderr)
            return EXIT_STORAGE
        return _export(args, targets, out)
    except (ValueError, ValidationError) as exc:
        print(f"{COMMAND}: {exc}", file=sys.stderr)
        return EXIT_USAGE
    except (StorageError, MigrationError, OSError) as exc:
        print(f"{COMMAND}: databases unavailable: {exc}", file=sys.stderr)
        return EXIT_STORAGE
    finally:
        if out is not sys.stdout:
            out.close()
