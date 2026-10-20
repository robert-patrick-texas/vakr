# Copyright 2026 Robert Patrick
# SPDX-License-Identifier: Apache-2.0
r"""Check locked Python packages against PyPI, one by one and as a whole lockfile.

Two views catch different drift. Per package, every direct dependency
and dependency-group member in ``pyproject.toml`` is compared with the
version ``uv.lock`` pins, so ruff, ty, and runtime libraries show up by
name. For the whole lockfile, ``uv lock --upgrade --dry-run`` reports what
resolution would change, which also covers transitive packages and
upgrades blocked by version constraints. ``uv.lock`` is never modified.

Dependencies:
    tomllib: Reads ``pyproject.toml`` and ``uv.lock``.
    updates_checks: Shared task model and row helpers.

Safety:
    Read-only for the repository. If a misconfigured lockfile command does
    rewrite ``uv.lock``, the original bytes are restored before returning.

Example:
    >>> parse_lock_changes("Resolved 3 packages\nUpdate tomlkit v0.13.0 -> v0.15.2")
    [LockChange(action='Update', name='tomlkit', old='0.13.0', new='0.15.2')]
"""

import functools
import re
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from updates_checks import Context, Outcome, Task, attempt, failed_task, make_row
from updates_fetch import FetchError
from updates_report import Result
from updates_versions import CURRENT, UNKNOWN, UPDATE

DEFAULT_LOCK_COMMAND = ["uv", "lock", "--upgrade", "--dry-run"]
_LOCK_LINE = re.compile(r"^(Update|Add|Remove)\s+(\S+)\s+v?(\S+)(?:\s+->\s+v?(\S+))?\s*$")
_ANSI = re.compile(r"\x1b\[[0-9;]*m")


@dataclass(frozen=True, slots=True)
class LockChange:
    """One package change reported by ``uv lock --upgrade --dry-run``.

    Attributes:
        action: ``Update``, ``Add``, or ``Remove``.
        name: Package name as uv prints it.
        old: Locked version (the new one for ``Add``).
        new: Version after the upgrade; empty for ``Add`` and ``Remove``.
    """

    action: str
    name: str
    old: str
    new: str


def normalize_name(name: str) -> str:
    """Normalize a Python package name as PEP 503 does.

    Args:
        name: Name as written, such as ``Mdit_Py.Plugins``.

    Returns:
        str: Lower-case name with runs of ``-_.`` replaced by ``-``.

    Raises:
        None
    """
    return re.sub(r"[-_.]+", "-", name).lower()


def direct_dependencies(pyproject: Path) -> list[str]:
    """List direct dependencies: runtime, optional extras, and every dependency group.

    Args:
        pyproject: Path to ``pyproject.toml``.

    Returns:
        list[str]: Normalized names in order of first appearance.

    Raises:
        OSError: If the file cannot be read.
        tomllib.TOMLDecodeError: If the file is not valid TOML.
    """
    data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
    project = data.get("project", {})
    specs: list[object] = list(project.get("dependencies", []))
    for group in (project.get("optional-dependencies", {}), data.get("dependency-groups", {})):
        for members in group.values():
            specs.extend(members)
    names: list[str] = []
    for spec in specs:
        # Skip {include-group = "..."} tables; their members are listed anyway.
        match = re.match(r"\s*([A-Za-z0-9][A-Za-z0-9._-]*)", spec if isinstance(spec, str) else "")
        if match and normalize_name(match[1]) not in names:
            names.append(normalize_name(match[1]))
    return names


def locked_versions(lockfile: Path) -> dict[str, str]:
    """Map each package in ``uv.lock`` to its locked version.

    Args:
        lockfile: Path to ``uv.lock``.

    Returns:
        dict[str, str]: Normalized name to version; the project itself,
        which has no registry version, is omitted.

    Raises:
        OSError: If the file cannot be read.
        tomllib.TOMLDecodeError: If the file is not valid TOML.
    """
    data = tomllib.loads(lockfile.read_text(encoding="utf-8"))
    return {
        normalize_name(p["name"]): p["version"] for p in data.get("package", []) if "version" in p
    }


def parse_lock_changes(output: str) -> list[LockChange]:
    """Parse the change lines printed by ``uv lock --upgrade --dry-run``.

    Args:
        output: Combined stdout and stderr of the command.

    Returns:
        list[LockChange]: One entry per ``Update``, ``Add``, or ``Remove`` line.

    Raises:
        None
    """
    changes = []
    for line in _ANSI.sub("", output).splitlines():
        if match := _LOCK_LINE.match(line.strip()):
            changes.append(LockChange(match[1], match[2], match[3], match[4] or ""))
    return changes


def check_package(ctx: Context, name: str, locked: Outcome) -> list[Result]:
    """Compare one locked PyPI package with its latest release.

    Args:
        ctx: Task context.
        name: Normalized package name.
        locked: Locked version or why it is unknown.

    Returns:
        list[Result]: One row.

    Raises:
        None
    """
    latest = attempt(lambda: ctx.upstream.lookup("pypi", name))
    return [make_row(name, "pypi", locked, latest)]


def plan_pypi(ctx: Context, cfg: Mapping[str, Any]) -> tuple[list[Task], set[str]]:
    """Plan one task per direct dependency, dependency-group member, and extra name.

    Args:
        ctx: Task context.
        cfg: The ``[pypi]`` table.

    Returns:
        tuple[list[Task], set[str]]: Tasks, and the names they cover so the
        lockfile check does not report them twice.

    Raises:
        None
    """
    try:
        names = []
        if cfg.get("direct_dependencies", True):
            names = direct_dependencies(ctx.root / cfg.get("pyproject", "pyproject.toml"))
        locked = locked_versions(ctx.root / cfg.get("lockfile", "uv.lock"))
    except (OSError, ValueError) as exc:
        return [failed_task("PyPI dependencies", "pypi", str(exc))], set()
    for extra in map(normalize_name, cfg.get("extra", [])):
        if extra not in names:
            names.append(extra)
    tasks = []
    for name in names:
        current = (locked[name], "") if name in locked else ("", "not in the lockfile")
        tasks.append(Task(name, "pypi", functools.partial(check_package, ctx, name, current)))
    return tasks, set(names)


def _lock_rows(changes: list[LockChange], reported: set[str]) -> list[Result]:
    """Turn lockfile changes into a summary row plus rows for unreported packages.

    Args:
        changes: Parsed dry-run output.
        reported: Packages already reported as direct dependencies.

    Returns:
        list[Result]: Summary row first, then one row per other upgrade.

    Raises:
        None
    """
    summary = "; ".join(
        f"{c.action} {c.name} {c.old}" + (f" -> {c.new}" if c.new else "") for c in changes
    )
    state = UPDATE if changes else CURRENT
    rows = [Result("uv.lock (all packages)", "lockfile", "", "", state, "", summary)]
    for change in changes:
        if change.new and normalize_name(change.name) not in reported:
            notes = f"https://pypi.org/project/{change.name}/{change.new}/"
            rows.append(Result(change.name, "lockfile", change.old, change.new, UPDATE, notes))
    return rows


def check_lockfile(ctx: Context, cfg: Mapping[str, Any], reported: set[str]) -> list[Result]:
    """Report what upgrading the whole lockfile would change, without changing it.

    Args:
        ctx: Task context.
        cfg: The ``[lockfile]`` table.
        reported: Packages already reported as direct dependencies.

    Returns:
        list[Result]: A summary row plus one row per changed package that
        is not already reported, or one unknown row if the command fails.

    Raises:
        None
    """
    item, lock = "uv.lock (all packages)", ctx.root / cfg.get("lockfile", "uv.lock")
    argv = cfg.get("command", DEFAULT_LOCK_COMMAND)
    before = lock.read_bytes() if lock.is_file() else None
    try:
        result = ctx.fetcher.run(argv, cwd=ctx.root)
    except FetchError as exc:
        return [Result(item, "lockfile", "", "", UNKNOWN, "", str(exc))]
    finally:
        if before is not None and (not lock.is_file() or lock.read_bytes() != before):
            lock.write_bytes(before)  # checking must never change the repository
    if result.returncode != 0:
        reason = (result.stderr.strip().splitlines() or ["no output"])[-1]
        return [Result(item, "lockfile", "", "", UNKNOWN, "", f"{argv[0]} failed: {reason}")]
    return _lock_rows(parse_lock_changes(result.stdout + "\n" + result.stderr), reported)
