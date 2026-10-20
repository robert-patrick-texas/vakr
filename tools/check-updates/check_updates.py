# Copyright 2026 Robert Patrick
# SPDX-License-Identifier: Apache-2.0
# /// script
# requires-python = ">=3.15"
# dependencies = []
# ///
"""Report newer versions of a project's toolchain, dependencies, and pinned actions.

Staying on current releases is a release-time chore that is easy to skip:
CPython and Go ship patch releases, uv, ruff, and ty move weekly, locked
dependencies drift, and pinned GitHub Actions age silently. check-updates
lists everything a project pins next to the latest stable upstream
version, with release-notes links, so each release can apply or knowingly
defer every update (TOOLS-001, TOOLS-002). What to check is declared in
``check-updates.toml`` beside this script; another project reuses the tool
by copying the directory and editing only that file (blueprint section 29).

The report is informational and exits 0. ``--fail-on-outdated`` exits 1
when any update is available. An item whose status cannot be determined,
for example because a release API is unreachable, is reported as unknown
with the reason and never stops the run. Configuration errors exit 2.

Dependencies:
    updates_*: Sibling modules for configuration, checks, upstream
        parsers, network access, versions, and output. Standard library
        only, so ``uv run`` installs nothing.
    concurrent.futures: A bounded thread pool overlaps the network waits.

Safety:
    Read-only for the repository: the lockfile check uses ``uv lock
    --dry-run`` and restores ``uv.lock`` if a command ever changes it.
    Network access is HTTPS with verification on and per-request timeouts
    and size caps. No credentials are handled (``gh`` keeps its own token).

Example:
    $ uv run tools/check-updates/check_updates.py --fail-on-outdated
    ITEM                      CURRENT  LATEST  STATUS   RELEASE NOTES
    Python (.python-version)  3.15     3.15.0  current  https://docs.python.org/release/...
    ...
    check-updates: 27 items: 24 current, 0 update available, 3 unknown
"""

import argparse
import functools
import sys
from collections.abc import Mapping
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from updates_actions import plan_actions
from updates_checks import Context, Task, check_entry, check_python
from updates_config import ConfigError, load_config
from updates_fetch import Fetcher
from updates_go import check_go
from updates_pypi import check_lockfile, plan_pypi
from updates_report import Result, exit_code, render_json, render_table
from updates_upstream import GO_RELEASES_URL, PYTHON_RELEASES_URL, Upstream
from updates_versions import UNKNOWN

DEFAULT_CONFIG = Path(__file__).with_name("check-updates.toml")
MAX_WORKERS = 32  # upper bound whatever the config says; APIs rate-limit bursts


def plan(ctx: Context, config: Mapping[str, Any]) -> list[Task]:
    """Turn the configuration into tasks in report order.

    Args:
        ctx: Task context.
        config: Validated configuration.

    Returns:
        list[Task]: Python, Go, tools, PyPI, lockfile, actions, then
        assets; sections absent from the config are skipped.

    Raises:
        None
    """
    tasks = []
    for section, check in (("python", check_python), ("go", check_go)):
        if section in config:
            tasks.append(Task(section, section, functools.partial(check, ctx, config[section])))
    for entry in config.get("tools", []):
        tasks.append(
            Task(entry["name"], "tool", functools.partial(check_entry, ctx, entry, "tool"))
        )
    reported: set[str] = set()
    if "pypi" in config:
        pypi_tasks, reported = plan_pypi(ctx, config["pypi"])
        tasks += pypi_tasks
    if "lockfile" in config:
        lockfile = functools.partial(check_lockfile, ctx, config["lockfile"], reported)
        tasks.append(Task("uv.lock", "lockfile", lockfile))
    if "actions" in config:
        tasks += plan_actions(ctx, config["actions"])
    for entry in config.get("assets", []):
        tasks.append(
            Task(entry["name"], "asset", functools.partial(check_entry, ctx, entry, "asset"))
        )
    return tasks


def _run_one(task: Task) -> list[Result]:
    """Run one task, turning any unexpected exception into an unknown row.

    Args:
        task: The task.

    Returns:
        list[Result]: The task's rows, or one unknown row on failure.

    Raises:
        None
    """
    try:
        return task.run()
    except Exception as exc:  # noqa: BLE001 - one broken item must not lose the whole report
        return [Result(task.item, task.category, "", "", UNKNOWN, "", f"check failed: {exc!r}")]


def run_tasks(tasks: list[Task], workers: int) -> list[Result]:
    """Run tasks on a bounded thread pool and keep the planned order.

    Args:
        tasks: Planned tasks.
        workers: Requested concurrency, clamped to 1..MAX_WORKERS.

    Returns:
        list[Result]: All rows in task order.

    Raises:
        None
    """
    with ThreadPoolExecutor(max_workers=min(max(workers, 1), MAX_WORKERS)) as pool:
        futures = [pool.submit(_run_one, task) for task in tasks]
        return [row for future in futures for row in future.result()]


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    """Parse the command line.

    Args:
        argv: Arguments without the program name, or None for ``sys.argv``.

    Returns:
        argparse.Namespace: ``config``, ``root``, ``json``,
        ``fail_on_outdated``, and ``timeout``.

    Raises:
        SystemExit: With status 2 on invalid arguments (argparse behaviour).
    """
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--config", type=Path, default=DEFAULT_CONFIG, help="TOML file listing what to check"
    )
    parser.add_argument("--root", type=Path, default=Path(), help="repository root (default: .)")
    parser.add_argument("--json", action="store_true", help="print JSON instead of a table")
    parser.add_argument(
        "--fail-on-outdated", action="store_true", help="exit 1 when any update is available"
    )
    parser.add_argument("--timeout", type=float, help="seconds per request or command")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None, fetcher: Fetcher | None = None) -> int:
    """Run every configured check and print the report.

    Args:
        argv: Command-line arguments without the program name.
        fetcher: Network and command access; tests pass a fake.

    Returns:
        int: 0 normally, 1 for an available update with
        ``--fail-on-outdated``, 2 for a configuration error.

    Raises:
        None
    """
    args = parse_args(argv)
    try:
        config = load_config(args.config)
    except ConfigError as exc:
        print(f"check-updates: {exc}", file=sys.stderr)
        return 2
    settings = config.get("settings", {})
    timeout = args.timeout or float(settings.get("timeout_seconds", 20))
    fetcher = fetcher or Fetcher(timeout=timeout)
    python_url = config.get("python", {}).get("releases_url", PYTHON_RELEASES_URL)
    go_url = config.get("go", {}).get("releases_url", GO_RELEASES_URL)
    ctx = Context(args.root.resolve(), fetcher, Upstream(fetcher, python_url, go_url))
    results = run_tasks(plan(ctx, config), int(settings.get("max_workers", 8)))
    print(render_json(results) if args.json else render_table(results))
    return exit_code(results, args.fail_on_outdated)


if __name__ == "__main__":
    sys.exit(main())
