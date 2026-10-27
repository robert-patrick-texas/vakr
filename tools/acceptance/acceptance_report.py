# Copyright 2026 Robert Patrick
# SPDX-License-Identifier: Apache-2.0
# /// script
# requires-python = ">=3.15"
# dependencies = []
# ///
"""Turn a parallel run's field bundles into MVP-ACC-03 and MVP-ACC-04 evidence.

MVP acceptance (blueprint 27.6) asks that, during a parallel run of at
least 14 days, the application's 5-minute traffic averages agree with
MRTG's within ±2 % after aligning the windows, with every difference
outside that explained (MVP-ACC-03), and that from the same vantage point
its median RTT agrees with SmokePing's within ±1 ms or ±5 % and its loss
is consistent (MVP-ACC-04). The operator collects field bundles daily
(profiles ``mrtg``, ``smokeping``, and ``acceptance``, on one host or two);
``field_inbox.py`` extracts them into ``inbox/``. This tool merges every
bundle, matches the targets, aligns the windows, compares, explains, and
writes a Markdown report plus a JSON report with every window, into the
inbox by default. The rules are in ``acceptance.toml``.

Only a report built from pseudonymized bundles may be committed (E.34
decision 11, owner 2026-10-27): a clear one names devices and addresses. So
a report from clear bundles is written only under the inbox, which git
ignores; ``--output`` elsewhere is refused for it.

Exit status: 0 the report was written (``--gate``: and both criteria
pass); 1 with ``--gate``, a criterion did not pass; 2 a usage or
configuration error.

Dependencies:
    acc_*: Sibling modules: configuration, bundle formats, matching, the
        two comparisons, and rendering. Standard library only, so ``uv
        run`` installs nothing.

Safety:
    Reads the bundles and writes only the two report files. Imports
    nothing from vakr (blueprint 29.1).

Example:
    $ uv run tools/acceptance/acceptance_report.py --output docs/acceptance
    acceptance: 14 bundles; MVP-ACC-03 pass (412 pass); MVP-ACC-04 pass (96 pass); wrote ...
"""

import argparse
import json
import sys
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path

from acc_bundles import load_inbox
from acc_config import ConfigError, load_rules
from acc_format import overall, summary
from acc_json import as_json
from acc_latency_run import run_latency
from acc_render import render_markdown
from acc_traffic_run import run_traffic

HERE = Path(__file__).resolve().parent


def build_parser() -> argparse.ArgumentParser:
    """Create the command-line parser.

    Args:
        None

    Returns:
        argparse.ArgumentParser: The parser.

    Raises:
        None
    """
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--config", type=Path, default=HERE / "acceptance.toml", metavar="TOML")
    parser.add_argument("--inbox", type=Path, metavar="DIR", help="extracted bundles")
    parser.add_argument("--bundle", action="append", metavar="ID", help="only these bundles")
    parser.add_argument(
        "--output",
        type=Path,
        metavar="DIR",
        help="where the reports go (default: the inbox; clear reports only there)",
    )
    parser.add_argument("--stem", default="parallel-run-report", metavar="NAME")
    parser.add_argument("--gate", action="store_true", help="exit 1 unless both criteria pass")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Write the reports.

    Args:
        argv: Arguments without the program name.

    Returns:
        int: 0 written (and passing with ``--gate``), 1 not passing with
        ``--gate``, 2 usage or configuration error.

    Raises:
        SystemExit: From argparse for ``--help`` and usage errors.
    """
    args = build_parser().parse_args(argv)
    try:
        rules = load_rules(args.config)
    except ConfigError as exc:
        print(f"acceptance: {exc}", file=sys.stderr)
        return 2
    root = args.inbox or rules.inbox
    output = args.output or root
    if not root.is_dir() or not output.is_dir():
        print(f"acceptance: {root if not root.is_dir() else output} is not a directory",
              file=sys.stderr)  # fmt: skip
        return 2
    inbox = load_inbox(root, rules.traffic.step_s, rules.max_bundles, args.bundle)
    if not inbox.pseudonymized and not output.resolve().is_relative_to(root.resolve()):
        print(f"acceptance: the bundles are not all pseudonymized with one key, so the report "
              f"names devices and addresses; it is written only under the inbox ({root}), not "
              f"{output} (E.34 decision 11). Collect with --pseudonymize to commit a report.",
              file=sys.stderr)  # fmt: skip
        return 2
    traffic = run_traffic(inbox, rules.traffic)
    latency = run_latency(inbox, rules.latency)
    now = datetime.now(UTC)
    markdown = output / f"{args.stem}.md"
    data = output / f"{args.stem}.json"
    markdown.write_text(render_markdown(inbox, traffic, latency, rules, now), encoding="utf-8")
    report = as_json(inbox, traffic, latency, rules)
    data.write_text(json.dumps(report, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    acc03 = overall(r.verdict for r in traffic.results)
    acc04 = overall(r.verdict for r in latency.results)
    print(
        f"acceptance: {len(inbox.bundles)} bundles; MVP-ACC-03 {acc03} "
        f"({summary(r.verdict for r in traffic.results)}); MVP-ACC-04 {acc04} "
        f"({summary(r.verdict for r in latency.results)}); wrote {markdown} and {data}"
    )
    return 1 if args.gate and (acc03, acc04) != ("pass", "pass") else 0


if __name__ == "__main__":
    sys.exit(main())
