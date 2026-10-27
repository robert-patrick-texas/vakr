# Copyright 2026 Robert Patrick
# SPDX-License-Identifier: Apache-2.0
"""Build report rows: the shared task model plus the Python and tool checks.

A task pairs one item's current version (from the repository or a local
command) with its latest stable release (from upstream) and yields report
rows. Planning reads only local files, so it is fast; tasks do the slow
network work and run concurrently. Every failure while reading or fetching
becomes an "unknown" row with the reason, never an exception that would
lose the rest of the report. The Go, PyPI/lockfile, and GitHub Actions
checks live in ``updates_go``, ``updates_pypi``, and ``updates_actions``
and reuse these helpers.

Dependencies:
    updates_local: Readers for pinned files and command output.
    updates_upstream: Latest-release lookups, cached across tasks.
    updates_fetch: Local commands, through the same replaceable ``Fetcher``.

Safety:
    Commands come from the config file only and run without a shell, with
    the fetcher's timeout. Nothing in the repository is written here.

Example:
    >>> make_row("uv", "tool", ("0.13.3", ""), (None, "pypi.org: timed out")).status
    'unknown'
"""

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import updates_local as local
from updates_fetch import Fetcher, FetchError
from updates_report import Result
from updates_sources import SOURCES, Latest
from updates_upstream import Upstream
from updates_versions import REFERENCE, UNKNOWN, parse_version, status

Outcome = tuple[str, str]  # (value, error); at most one is non-empty
Found = tuple[Latest | None, str]
# tomllib.TOMLDecodeError is a ValueError, so malformed TOML is covered too.
_LOCAL_ERRORS = (OSError, ValueError, KeyError, TypeError, FetchError)


@dataclass(frozen=True, slots=True)
class Context:
    """What every task needs: where the repository is and how to reach out.

    Attributes:
        root: Repository root; config paths are relative to it.
        fetcher: Network and command access.
        upstream: Cached latest-release lookups.
    """

    root: Path
    fetcher: Fetcher
    upstream: Upstream


@dataclass(frozen=True, slots=True)
class Task:
    """One unit of concurrent work.

    Attributes:
        item: Name used if the task fails unexpectedly.
        category: Category used for that fallback row.
        run: Produces this task's report rows.
    """

    item: str
    category: str
    run: Callable[[], list[Result]]


def failed_task(item: str, category: str, reason: str) -> Task:
    """Build a task that reports one unknown row, for planning failures.

    Args:
        item: Row label.
        category: Row category.
        reason: Why nothing could be checked.

    Returns:
        Task: Task yielding the single unknown row.

    Raises:
        None
    """
    return Task(item, category, lambda: [Result(item, category, "", "", UNKNOWN, "", reason)])


def attempt(fetch: Callable[[], Latest]) -> Found:
    """Call a latest-release lookup, converting failure into an error message.

    Args:
        fetch: Zero-argument lookup.

    Returns:
        Found: ``(latest, "")`` on success or ``(None, reason)`` on failure.

    Raises:
        None
    """
    try:
        return fetch(), ""
    except FetchError as exc:
        return None, str(exc)


def read_local(read: Callable[[], str | None], missing: str = "version not found") -> Outcome:
    """Call a local reader, converting failure or no result into an error message.

    Args:
        read: Zero-argument reader returning a version or None.
        missing: Reason used when the reader returns nothing.

    Returns:
        Outcome: ``(version, "")`` or ``("", reason)``.

    Raises:
        None
    """
    try:
        value = read()
    except _LOCAL_ERRORS as exc:
        return "", str(exc) or type(exc).__name__
    return (value, "") if value else ("", missing)


def make_row(
    item: str,
    category: str,
    current: Outcome,
    latest: Found,
    precision: int | None = None,
    note: str = "",
) -> Result:
    """Combine a current and a latest version into one report row.

    Args:
        item: Row label.
        category: Source family for JSON consumers.
        current: Current version or the reason it is unknown.
        latest: Latest release or the reason it is unavailable.
        precision: Release components to compare (2 compares minor series).
        note: Context such as where the pin lives.

    Returns:
        Result: The row, with status ``unknown`` and a reason when either
        side is missing or unparseable, or ``reference`` when the item pins
        nothing and only its latest release is shown.

    Raises:
        None
    """
    version, local_error = current
    found, fetch_error = latest
    detail = local_error or fetch_error or note
    if found is None:
        return Result(item, category, version, "", UNKNOWN, "", detail)
    if not version and not local_error:
        detail = note or "not pinned in this repository"
        return Result(item, category, "", found.version.text, REFERENCE, found.notes_url, detail)
    state = status(parse_version(version), found.version, precision)
    if state == UNKNOWN and not detail:
        detail = f"cannot parse version {version!r}"
    return Result(item, category, version, found.version.text, state, found.notes_url, detail)


def command_version(ctx: Context, argv: list[str], regex: str | None = None) -> str | None:
    """Run a version command and extract the version it prints.

    Args:
        ctx: Task context.
        argv: Command such as ``["uv", "--version"]``.
        regex: Optional pattern whose first group is the version.

    Returns:
        str | None: The version, or None if none is printed.

    Raises:
        FetchError: If the command is missing, times out, or fails.
    """
    result = ctx.fetcher.run(argv, cwd=ctx.root)
    if result.returncode != 0:
        raise FetchError(f"{' '.join(argv)} exited with status {result.returncode}")
    return local.find_version(result.stdout + "\n" + result.stderr, regex)


def check_python(ctx: Context, cfg: Mapping[str, Any]) -> list[Result]:
    """Compare the pinned and selected Python with the latest CPython (TOOLS-003).

    Args:
        ctx: Task context.
        cfg: The ``[python]`` table.

    Returns:
        list[Result]: Rows for the version file, ``requires-python``, and
        the interpreter ``uv`` selects, as configured.

    Raises:
        None
    """
    latest = attempt(ctx.upstream.python)
    rows = []
    if name := cfg.get("version_file"):
        pin = ctx.root / name
        current = read_local(lambda: local.read_version_file(pin))
        rows.append(make_row(f"Python ({name})", "python", current, latest, precision=2))
    if name := cfg.get("requires_python"):
        pyproject = ctx.root / name
        current = read_local(lambda: local.requires_python_floor(pyproject))
        rows.append(make_row(f"Python (requires-python in {name})", "python", current, latest, 2))
    if cfg.get("uv_interpreter", True):
        argv = ["uv", "python", "find", "--show-version"]
        current = read_local(lambda: command_version(ctx, argv))
        rows.append(make_row("Python (interpreter uv selects)", "python", current, latest))
    return rows


def entry_current(ctx: Context, entry: Mapping[str, Any]) -> Outcome:
    """Read the current version of a ``[[tools]]`` or ``assets`` entry.

    Args:
        ctx: Task context.
        entry: The entry; uses ``command``, ``file``, or ``version``.

    Returns:
        Outcome: The version, or why it is unknown; ``("", "")`` when the
        entry pins nothing and is listed for reference only.

    Raises:
        None
    """
    regex = entry.get("regex")
    if "command" in entry:
        return read_local(lambda: command_version(ctx, entry["command"], regex))
    if "file" in entry:
        path = ctx.root / entry["file"]
        return read_local(lambda: local.find_version(path.read_text(encoding="utf-8"), regex))
    return str(entry.get("version", "")), ""


def check_entry(ctx: Context, entry: Mapping[str, Any], category: str) -> list[Result]:
    """Compare one ``[[tools]]`` or ``assets`` entry with its upstream source.

    Args:
        ctx: Task context.
        entry: Validated entry with ``name`` and one source key.
        category: ``tool`` or ``asset``.

    Returns:
        list[Result]: One row.

    Raises:
        None
    """
    source = next(key for key in SOURCES if key in entry)
    per_page = int(entry.get("per_page", 10))
    latest = attempt(lambda: ctx.upstream.lookup(source, entry[source], per_page))
    note = f"pinned in {entry['file']}" if "file" in entry else ""
    return [make_row(entry["name"], category, entry_current(ctx, entry), latest, note=note)]
