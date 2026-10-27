# Copyright 2026 Robert Patrick
# SPDX-License-Identifier: Apache-2.0
"""Check npm packages: each direct dependency's locked version against the registry.

A front end built with npm pins its packages in ``package-lock.json``;
``package.json`` names the direct ones, both those bundled into the
shipped app (``dependencies``) and the build and test tools
(``devDependencies``). Each direct package is compared with the version
behind the registry's ``latest`` dist-tag, the npm counterpart of the
PyPI rows (TOOLS-001, ADR-035 #18). Transitive packages follow from the
direct ones; ``extra`` names more locked packages to watch. A package in
``lines`` is compared instead with the newest release on the major line
another file pins, as Node's type definitions follow the Node build pin.

Dependencies:
    json: Reads ``package.json`` and ``package-lock.json``.
    updates_checks: Shared task model and row helpers.

Safety:
    Read-only: no npm command runs, and the lockfile is only parsed.

Example:
    >>> locked_from({"packages": {"node_modules/@scope/a": {"version": "1.2.0"}}})
    {'@scope/a': '1.2.0'}
"""

import functools
import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import updates_local as local
from updates_checks import Context, Outcome, Task, attempt, failed_task, make_row, read_local
from updates_report import Result
from updates_versions import parse_version

_DIRECT_KINDS = (("dependencies", "bundled"), ("devDependencies", "development"))
_PREFIX = "node_modules/"


def direct_packages(package_json: Path) -> dict[str, str]:
    """List direct dependencies and dev dependencies in the order they are written.

    Args:
        package_json: Path to ``package.json``.

    Returns:
        dict[str, str]: Package name to its kind (``bundled`` or ``development``);
        a name listed in both counts as bundled.

    Raises:
        OSError: If the file cannot be read.
        ValueError: If it is not JSON.
        TypeError: If it is JSON but not an object.
    """
    data = json.loads(package_json.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise TypeError(f"{package_json.name} is not a JSON object")
    names: dict[str, str] = {}
    for key, kind in _DIRECT_KINDS:
        for name in data.get(key) or {}:
            names.setdefault(name, kind)
    return names


def locked_from(lock: Mapping[str, Any]) -> dict[str, str]:
    """Map each top-level package of a decoded lockfile to its version.

    Lockfile versions 2 and 3 list ``packages`` by install path; version 1
    has only ``dependencies``. Nested copies (``node_modules/a/node_modules/b``)
    are not the project's own pin and are skipped.

    Args:
        lock: Decoded ``package-lock.json``.

    Returns:
        dict[str, str]: Package name to locked version.

    Raises:
        None
    """
    found: dict[str, str] = {}
    for path, entry in (lock.get("packages") or {}).items():
        name = path.removeprefix(_PREFIX)
        if path.startswith(_PREFIX) and _PREFIX not in name and "version" in entry:
            found[name] = entry["version"]
    if not found:
        for name, entry in (lock.get("dependencies") or {}).items():
            if isinstance(entry, Mapping) and "version" in entry:
                found[name] = entry["version"]
    return found


def locked_versions(lockfile: Path) -> dict[str, str]:
    """Read ``package-lock.json`` and map each top-level package to its version.

    Args:
        lockfile: Path to the lockfile.

    Returns:
        dict[str, str]: Package name to locked version.

    Raises:
        OSError: If the file cannot be read.
        ValueError: If it is not JSON.
    """
    data = json.loads(lockfile.read_text(encoding="utf-8"))
    return locked_from(data if isinstance(data, dict) else {})


def check_package(
    ctx: Context, name: str, locked: Outcome, kind: str, line_file: str = ""
) -> list[Result]:
    """Compare one locked npm package with the registry's ``latest`` release.

    Args:
        ctx: Task context.
        name: Package name, scoped names included (``@scope/name``).
        locked: Locked version or why it is unknown.
        kind: ``bundled``, ``development``, or ``extra``, for the detail.
        line_file: Version file whose major line the package follows
            instead of ``latest`` (the ``lines`` table); empty for none.

    Returns:
        list[Result]: One row.

    Raises:
        None
    """
    note = f"{kind} npm package"
    if not line_file:
        latest = attempt(lambda: ctx.upstream.lookup("npm", name))
        return [make_row(name, "npm", locked, latest, note=note)]
    text, error = read_local(lambda: local.read_version_file(ctx.root / line_file))
    pinned = None if error else parse_version(text)
    if pinned is None:
        reason = f"{line_file}: {error or f'cannot parse {text!r}'}"
        return [make_row(name, "npm", locked, (None, reason))]
    line = pinned.release[0]
    latest = attempt(lambda: ctx.upstream.npm_line(name, line))
    note += f"; follows the {line}.x line of {line_file}"
    return [make_row(name, "npm", locked, latest, note=note)]


def plan_npm(ctx: Context, cfg: Mapping[str, Any]) -> list[Task]:
    """Plan one task per direct npm package and per ``extra`` name.

    Args:
        ctx: Task context.
        cfg: The ``[npm]`` table.

    Returns:
        list[Task]: One task per package, or one unknown task when
        ``package.json`` or the lockfile cannot be read (for example
        before the front end exists).

    Raises:
        None
    """
    package_json = cfg.get("package_json", "package.json")
    lockfile = cfg.get("lockfile", "package-lock.json")
    try:
        names = {}
        if cfg.get("direct_dependencies", True):
            names = direct_packages(ctx.root / package_json)
        locked = locked_versions(ctx.root / lockfile)
    except (OSError, ValueError, TypeError) as exc:
        return [failed_task("npm packages", "npm", f"{package_json} or {lockfile}: {exc}")]
    for extra in cfg.get("extra", []):
        names.setdefault(extra, "extra")
    tasks = []
    for name, kind in names.items():
        current = (locked[name], "") if name in locked else ("", "not in the lockfile")
        line_file = cfg.get("lines", {}).get(name, "")
        run = functools.partial(check_package, ctx, name, current, kind, line_file)
        tasks.append(Task(name, "npm", run))
    return tasks
