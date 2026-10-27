"""``vakr acceptance schedule|gaps|coverage``: the reports judged from vakr's own data (E.35).

Options shared by the three reports:

- ``--window DURATION``: how far back from five minutes ago to judge, such
  as ``7d`` or ``36h`` (1 hour to 30 days); ``coverage`` uses it for the
  reload and discontinuity search, its graph ranges are fixed;
- ``--collector NAME``: one position only;
- ``--limit N``: rows listed per collector (1 to 10,000; summaries count all);
- ``--json``: a JSON document instead of text.

Dependencies:
    vakr.cli.acceptance_stores, acceptance_render: The service and text output.
    vakr.query.acceptance_*: Requests, bounds, and views.

Safety:
    Read-only. Prints names, counts, and times; never credentials.

Example:
    $ vakr acceptance coverage --collector east --json
"""

import argparse
import json
import re
import sys
from collections.abc import Callable
from datetime import timedelta

from vakr.cli.acceptance_render import render
from vakr.cli.acceptance_stores import open_acceptance
from vakr.cli.common import SubParsers
from vakr.cli.status_stores import NoDataYetError
from vakr.domain.errors import StorageError, ValidationError
from vakr.paths import InstallPaths
from vakr.ports.migrations import MigrationError
from vakr.query.acceptance import AcceptanceQueries
from vakr.query.acceptance_limits import (
    DEFAULT_ROWS,
    EVENTS_DEFAULT,
    GAPS_DEFAULT,
    SCHEDULE_DEFAULT,
)
from vakr.query.acceptance_request import AcceptanceRequest
from vakr.query.acceptance_views import AcceptanceReport, Verdict
from vakr.query.jsonable import to_jsonable

EXIT = {Verdict.PASS: 0, Verdict.FAIL: 1, Verdict.INCOMPLETE: 3}
"""Exit status by verdict."""

EXIT_UNAVAILABLE = 3
FORMAT = "vakr-acceptance-1"
"""The ``format`` member of every ``--json`` document."""

_DURATION = re.compile(r"(\d{1,4})([hd])")
_REPORTS: dict[str, tuple[str, timedelta]] = {
    "schedule": ("MVP-ACC-01: on-time polls, no overlap, no drift, 60 s workload",
                 SCHEDULE_DEFAULT),
    "gaps": ("MVP-ACC-10: every period without data, and what explains it", GAPS_DEFAULT),
    "coverage": ("MVP-ACC-02: graphs at every range; no spike across reloads", EVENTS_DEFAULT),
}  # fmt: skip


def parse_window(text: str) -> timedelta:
    """Parse ``--window`` (``14d`` or ``36h``).

    Args:
        text: As typed.

    Returns:
        timedelta: The duration.

    Raises:
        argparse.ArgumentTypeError: If it is not a whole number of hours or days.
    """
    found = _DURATION.fullmatch(text.strip())
    if found is None:
        raise argparse.ArgumentTypeError("use a whole number of hours or days, such as 14d")
    value, unit = int(found.group(1)), found.group(2)
    return timedelta(hours=value) if unit == "h" else timedelta(days=value)


def register(actions: SubParsers) -> None:
    """Add ``schedule``, ``gaps``, and ``coverage`` to ``vakr acceptance``.

    Args:
        actions: The group's sub-parser collection.

    Returns:
        None

    Raises:
        None
    """
    for name, (text, default) in _REPORTS.items():
        sub = actions.add_parser(name, help=text)
        sub.add_argument("--window", type=parse_window, default=default, metavar="DURATION",
                         help=f"how far back to judge (default {default.days}d)")  # fmt: skip
        sub.add_argument("--collector", metavar="NAME", help="judge only this collector")
        sub.add_argument("--limit", type=int, default=DEFAULT_ROWS, metavar="N",
                         help=f"rows to list per collector (default {DEFAULT_ROWS})")  # fmt: skip
        sub.add_argument("--json", action="store_true", help="print a JSON document")
        sub.set_defaults(func=run, report=name)


def _method(queries: AcceptanceQueries, report: str) -> Callable[..., AcceptanceReport]:
    """Return the service method of a report.

    Args:
        queries: The service.
        report: ``schedule``, ``gaps``, or ``coverage``.

    Returns:
        Callable: The method.

    Raises:
        None
    """
    return {"schedule": queries.schedule, "gaps": queries.gaps,
            "coverage": queries.coverage}[report]  # fmt: skip


def run(args: argparse.Namespace, paths: InstallPaths | None = None) -> int:
    """Compute one report and print it.

    Args:
        args: ``report``, ``window``, ``collector``, ``limit``, and ``json``.
        paths: Installation layout; from the environment when ``None``.

    Returns:
        int: 0 pass, 1 fail, 3 incomplete or unreadable data.

    Raises:
        None
    """
    command = f"vakr acceptance {args.report}"
    try:
        request = AcceptanceRequest(args.window, args.limit, args.collector)
        stores = open_acceptance(paths or InstallPaths.from_environment(), command)
    except ValidationError as exc:
        print(f"{command}: {exc}", file=sys.stderr)
        return 2
    except (NoDataYetError, MigrationError, StorageError) as exc:
        print(f"{command}: {exc}", file=sys.stderr)
        return EXIT_UNAVAILABLE
    try:
        report = _method(stores.queries, args.report)(request)
    except ValidationError as exc:
        print(f"{command}: {exc}", file=sys.stderr)
        return 2
    except StorageError as exc:
        print(f"{command}: telemetry unavailable: {exc}", file=sys.stderr)
        return EXIT_UNAVAILABLE
    finally:
        stores.close()
    if args.json:
        body = to_jsonable(report)
        document = {"format": FORMAT, **body} if isinstance(body, dict) else {"report": body}
        print(json.dumps(document, indent=2, ensure_ascii=False))
    else:
        print("\n".join(render(report)))
    return EXIT[report.verdict]
